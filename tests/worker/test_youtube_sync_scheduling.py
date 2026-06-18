"""YouTube 智能同步调度的核心不变量:**每次同步尝试后,next_sync_at 必须被推进到未来时刻**。

事故根因(与队列死信并列的第二个真 bug):
- `update_publish_stats` 在「历史不足 2 条视频」时直接 return,从不写 next_sync_at;
- `sync_channel_videos` 的「无新视频」分支(稳态最常见路径)只写 videos_synced_at,根本不调
  update_publish_stats。
两者叠加 → 大量频道的 next_sync_at 永远停在 NULL/旧值。一旦队列修好、check_scheduled_syncs
真正跑起来,它每小时都会用 `next_sync_at IS NULL OR <= now` 把这些频道一再选中、无限重复
同步,既烧 YouTube 配额又自我打满。本文件锁定「调度必然前进」这条不变量。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.services.youtube import sync_scheduler
from app.services.youtube.sync_scheduler import DEFAULT_SYNC_HOURS
from worker.tasks import sync_youtube_videos


# ---- 最小同步 session 替身 -------------------------------------------------
class _R:
    """一个 Result 替身,按需支持 scalar_one_or_none / scalars().all() / all()。"""

    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value

    def scalars(self) -> _R:
        return self

    def all(self) -> list:
        return list(self._value) if self._value is not None else []


class _Session:
    """按 execute 调用顺序依次吐预置 Result 的最小同步 session(本身即上下文管理器)。"""

    def __init__(self, results: list[object]) -> None:
        self._results = list(results)
        self.committed = 0

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *_a: object) -> bool:
        return False

    def execute(self, *_a: object, **_k: object) -> _R:
        return _R(self._results.pop(0))

    def commit(self) -> None:
        self.committed += 1


def _publish_session(publish_dts: list[datetime]) -> _Session:
    # update_publish_stats 只 select published_at 一列 → 行是 1-tuple
    return _Session([[(d,) for d in publish_dts]])


# ---- update_publish_stats:数据充足/不足都必须推进 next_sync_at ----------
def test_update_publish_stats_advances_next_sync_at_when_history_insufficient() -> None:
    """少于 2 条发布记录时,不能两手一摊不写 next_sync_at —— 否则该频道永远 NULL、被反复选中。
    退而用默认间隔(now + DEFAULT_SYNC_HOURS)兜底。"""
    sub = SimpleNamespace(
        id="sub1",
        avg_publish_interval_hours=None,
        last_publish_at=None,
        next_sync_at=None,
        videos_synced_at=None,
    )
    before = datetime.now(UTC)
    sync_scheduler.update_publish_stats(sub, _publish_session([]))  # 0 条记录
    after = datetime.now(UTC)

    assert sub.next_sync_at is not None, "历史不足也必须推进 next_sync_at(兜底默认间隔)"
    assert (
        before + timedelta(hours=DEFAULT_SYNC_HOURS) <= sub.next_sync_at <= after + timedelta(hours=DEFAULT_SYNC_HOURS)
    )


def test_update_publish_stats_advances_next_sync_at_when_timestamps_collapse() -> None:
    """两条视频时间戳相同(interval=0,不计正间隔)→ intervals 为空,同样必须兜底推进。"""
    t = datetime(2026, 6, 18, tzinfo=UTC)
    sub = SimpleNamespace(
        id="sub1",
        avg_publish_interval_hours=None,
        last_publish_at=None,
        next_sync_at=None,
        videos_synced_at=None,
    )
    before = datetime.now(UTC)
    sync_scheduler.update_publish_stats(sub, _publish_session([t, t]))
    after = datetime.now(UTC)

    assert sub.next_sync_at is not None
    assert (
        before + timedelta(hours=DEFAULT_SYNC_HOURS) <= sub.next_sync_at <= after + timedelta(hours=DEFAULT_SYNC_HOURS)
    )


def test_update_publish_stats_computes_interval_with_history() -> None:
    """回归守卫:有 ≥2 条规律发布记录时,仍按发布频率算 avg 间隔并设 next_sync_at。"""
    t0 = datetime(2026, 6, 18, tzinfo=UTC)
    dts = [t0, t0 - timedelta(hours=24), t0 - timedelta(hours=48)]  # 稳定 24h 间隔
    sub = SimpleNamespace(
        id="sub1",
        avg_publish_interval_hours=None,
        last_publish_at=None,
        next_sync_at=None,
        videos_synced_at=None,
    )
    sync_scheduler.update_publish_stats(sub, _publish_session(dts))

    assert sub.avg_publish_interval_hours == 24
    assert sub.last_publish_at == t0
    assert sub.next_sync_at is not None and sub.next_sync_at > datetime.now(UTC)


# ---- sync_channel_videos 的「无新视频」分支也必须推进 next_sync_at --------
class _FakeRequest:
    def __init__(self, resp: dict) -> None:
        self._resp = resp

    def execute(self) -> dict:
        return self._resp


class _FakePlaylistItems:
    def list(self, **_k: object) -> _FakeRequest:
        return _FakeRequest({"items": []})  # 无新视频、无 nextPageToken


class _FakeYouTube:
    def playlistItems(self) -> _FakePlaylistItems:
        return _FakePlaylistItems()


class _FakeRedis:
    """同步 redis 替身:set(nx) 受 acquire 控制,记录 set/delete 以验证锁的获取与释放。"""

    def __init__(self, acquire: bool = True) -> None:
        self._acquire = acquire
        self.set_calls: list[tuple] = []
        self.deleted: list[str] = []

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
        self.set_calls.append((key, value, nx, ex))
        return self._acquire

    def delete(self, key: str) -> None:
        self.deleted.append(key)


def _wire_empty_sync(monkeypatch, fake_redis: _FakeRedis) -> SimpleNamespace:
    """装配一次「无新视频」的成功同步:返回 subscription 供断言其 next_sync_at。"""
    account = SimpleNamespace(
        user_id="u1",
        provider="youtube",
        needs_reauth=False,
        access_token="at",
        refresh_token="rt",
        token_expires_at=datetime.now(UTC) + timedelta(hours=10),  # 未过期 → 不刷新
    )
    subscription = SimpleNamespace(
        id="sub1",
        user_id="u1",
        channel_id="c1",
        sync_enabled=True,
        uploads_playlist_id="UU1",  # 已知 → 跳过 channels().list
        videos_synced_at=None,
        next_sync_at=None,
        last_publish_at=None,
        avg_publish_interval_hours=None,
        channel_title="Chan",
    )
    # execute 顺序:Account → Subscription → 既有 video_id 集合 → (update_publish_stats)发布时间
    session = _Session([account, subscription, [], []])
    monkeypatch.setattr(sync_youtube_videos, "get_sync_redis_client", lambda: fake_redis)
    monkeypatch.setattr(sync_youtube_videos, "get_sync_db_session", lambda: session)
    monkeypatch.setattr(sync_youtube_videos, "_build_credentials", lambda _a: object())
    monkeypatch.setattr(sync_youtube_videos, "build", lambda *_a, **_k: _FakeYouTube())
    return subscription


def test_empty_sync_still_advances_next_sync_at(monkeypatch) -> None:
    """增量同步发现没有新视频(稳态最常见)时,仍必须推进 next_sync_at,
    否则 check_scheduled_syncs 会每小时把该频道反复选中、无限重复空同步。"""
    subscription = _wire_empty_sync(monkeypatch, _FakeRedis(acquire=True))

    before = datetime.now(UTC)
    result = sync_youtube_videos.sync_channel_videos.apply(kwargs={"user_id": "u1", "channel_id": "c1"}).get()

    assert result["status"] == "success"
    assert result["synced_count"] == 0
    assert subscription.next_sync_at is not None, "空同步也必须推进 next_sync_at"
    assert subscription.next_sync_at > before


# ---- 去重锁:同一(用户,频道)的同步不得并发执行 --------------------------
def test_sync_channel_videos_skips_when_lock_already_held(monkeypatch) -> None:
    """锁已被占用(redis.set NX 返回 False)时,直接返回 skipped,且绝不触库。"""
    fake_redis = _FakeRedis(acquire=False)
    db_calls = {"n": 0}

    def _fake_db():
        db_calls["n"] += 1
        return _Session([None])  # 若真触库:account=None → 走 error 返回(便于区分)

    monkeypatch.setattr(sync_youtube_videos, "get_sync_redis_client", lambda: fake_redis)
    monkeypatch.setattr(sync_youtube_videos, "get_sync_db_session", _fake_db)

    result = sync_youtube_videos.sync_channel_videos.apply(kwargs={"user_id": "u1", "channel_id": "c1"}).get()

    assert result.get("reason") == "sync_already_in_progress"
    assert db_calls["n"] == 0, "未拿到锁时不得触库"
    # 锁键按 (user, channel) 隔离,且用 NX
    assert fake_redis.set_calls, "应尝试以 NX 获取锁"
    key, _val, nx, _ex = fake_redis.set_calls[0]
    assert key == "youtube_channel_sync_lock:u1:c1"
    assert nx is True


def test_sync_channel_videos_releases_lock_on_success(monkeypatch) -> None:
    """成功跑完(含空同步)后必须释放锁,否则 TTL 内该频道再不会被同步。"""
    fake_redis = _FakeRedis(acquire=True)
    _wire_empty_sync(monkeypatch, fake_redis)

    result = sync_youtube_videos.sync_channel_videos.apply(kwargs={"user_id": "u1", "channel_id": "c1"}).get()

    assert result["status"] == "success"
    assert "youtube_channel_sync_lock:u1:c1" in fake_redis.deleted, "成功后必须释放锁"
