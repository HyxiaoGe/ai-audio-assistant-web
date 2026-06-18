"""YouTube token 刷新失败时的「止血」行为。

背景:某用户的 refresh token 失效(invalid_grant)后,按频道扇出的 sync_channel_videos
会对每个频道重复尝试刷新并打 ERROR+traceback,瞬间刷出 N 条 traceback,被 dev-ops-sentinel
的日志错误尖峰检测当成事故反复告警。

本测试锁定两条不变量:
1. 账号已知 needs_reauth 时,sync_channel_videos 直接跳过,不再构建凭证/尝试刷新,也不打 ERROR。
2. 首次遇到 invalid_grant 时,用 WARNING(不带 traceback)记录,而不是 logger.exception。
"""

from __future__ import annotations

import logging
import re
import types
from datetime import UTC, datetime, timedelta

from worker.tasks import sync_youtube_subscriptions, sync_youtube_videos

# 与 dev-ops-sentinel 旧版日志尖峰行过滤一致的宽匹配:降级后的 WARNING 文案绝不应命中,
# 否则即便降为 WARNING,仍可能被(历史/被回退的)宽匹配检测计入而误报。
_NOISE_RE = re.compile(r"(?i)error|exception|traceback")


def _assert_warning_is_clean(caplog: object) -> None:
    warns = [
        r.getMessage()
        for r in caplog.records  # type: ignore[attr-defined]
        if r.levelno == logging.WARNING and "refresh" in r.getMessage().lower()
    ]
    assert warns, "应至少有一条 refresh 相关 WARNING"
    offenders = [m for m in warns if _NOISE_RE.search(m)]
    assert not offenders, f"降级后的 WARNING 不应含 error/exception/traceback 子串: {offenders}"


class _Result:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class _FakeSession:
    """按 execute 调用顺序依次吐预置结果的最小同步 session 替身(本身即上下文管理器)。"""

    def __init__(self, results: list[object]) -> None:
        self._results = list(results)
        self.commits = 0

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *_a: object) -> bool:
        return False

    def execute(self, *_a: object, **_k: object) -> _Result:
        return _Result(self._results.pop(0))

    def commit(self) -> None:
        self.commits += 1


class _RaisingCreds:
    """凭证替身:refresh 抛 invalid_grant,模拟 refresh token 失效/吊销。"""

    token = "x"
    expiry = None

    def refresh(self, _request: object) -> None:
        raise Exception("('invalid_grant: Token has been expired or revoked.', {'error': 'invalid_grant'})")


def _account(**kw: object) -> types.SimpleNamespace:
    base: dict[str, object] = dict(
        user_id="u1",
        provider="youtube",
        needs_reauth=False,
        access_token="at",
        refresh_token="rt",
        token_expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def _subscription(**kw: object) -> types.SimpleNamespace:
    base: dict[str, object] = dict(id="sub1", sync_enabled=True, uploads_playlist_id="UU123")
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_sync_channel_videos_skips_when_account_needs_reauth(monkeypatch, caplog) -> None:
    """needs_reauth 账号:返回 skipped,且绝不构建凭证/尝试刷新,也不打 ERROR。"""
    account = _account(needs_reauth=True)
    subscription = _subscription()
    monkeypatch.setattr(sync_youtube_videos, "get_sync_db_session", lambda: _FakeSession([account, subscription]))

    def _must_not_build(*_a: object, **_k: object) -> object:
        raise AssertionError("needs_reauth 账号不得再构建凭证/尝试刷新")

    monkeypatch.setattr(sync_youtube_videos, "_build_credentials", _must_not_build)

    with caplog.at_level(logging.DEBUG):
        result = sync_youtube_videos.sync_channel_videos.apply(kwargs={"user_id": "u1", "channel_id": "c1"}).get()

    assert result["status"] == "skipped"
    assert result["reason"] == "needs_reauth"
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []


def test_sync_channel_videos_invalid_grant_warns_without_traceback(monkeypatch, caplog) -> None:
    """首次 invalid_grant:WARNING 记录(无 traceback)、标记 needs_reauth、发去重通知,不打 ERROR。"""
    account = _account(needs_reauth=False)
    subscription = _subscription()
    monkeypatch.setattr(sync_youtube_videos, "get_sync_db_session", lambda: _FakeSession([account, subscription]))
    monkeypatch.setattr(sync_youtube_videos, "_build_credentials", lambda _a: _RaisingCreds())

    notified: dict[str, object] = {}

    def _notify(_session: object, **kw: object) -> None:
        notified.update(kw)

    monkeypatch.setattr(sync_youtube_videos.NotificationService, "notify", _notify)

    with caplog.at_level(logging.DEBUG):
        result = sync_youtube_videos.sync_channel_videos.apply(kwargs={"user_id": "u1", "channel_id": "c1"}).get()

    assert result["status"] == "error"
    assert result["needs_reauth"] is True
    assert account.needs_reauth is True
    assert notified.get("type") == sync_youtube_videos.NotificationType.YOUTUBE_REAUTH_REQUIRED

    refresh_errors = [
        r for r in caplog.records if r.levelno >= logging.ERROR and "refresh token" in r.getMessage().lower()
    ]
    assert refresh_errors == [], "invalid_grant 不应再以 ERROR/traceback 记录"
    assert any(r.levelno == logging.WARNING and "refresh" in r.getMessage().lower() for r in caplog.records)
    _assert_warning_is_clean(caplog)


class _FakeRedis:
    def set(self, *_a: object, **_k: object) -> bool:
        return True  # 拿到锁

    def delete(self, *_a: object, **_k: object) -> None:
        return None


def test_sync_subscriptions_invalid_grant_warns_without_traceback(monkeypatch, caplog) -> None:
    """sync_youtube_subscriptions 同款止血:invalid_grant 走 WARNING 而非 logger.exception。"""
    account = _account(needs_reauth=False)
    monkeypatch.setattr(sync_youtube_subscriptions, "get_sync_redis_client", lambda: _FakeRedis())
    monkeypatch.setattr(sync_youtube_subscriptions, "get_sync_db_session", lambda: _FakeSession([account]))
    monkeypatch.setattr(sync_youtube_subscriptions, "_build_credentials", lambda _a: _RaisingCreds())

    with caplog.at_level(logging.DEBUG):
        result = sync_youtube_subscriptions.sync_youtube_subscriptions.apply(kwargs={"user_id": "u1"}).get()

    assert result["status"] == "error"
    refresh_errors = [
        r for r in caplog.records if r.levelno >= logging.ERROR and "refresh token" in r.getMessage().lower()
    ]
    assert refresh_errors == [], "invalid_grant 不应再以 ERROR/traceback 记录"
    assert any(r.levelno == logging.WARNING and "refresh" in r.getMessage().lower() for r in caplog.records)
    _assert_warning_is_clean(caplog)
