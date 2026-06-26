import pytest

import app.services.youtube.blocklist_service as bls
from app.services.youtube.blocklist_service import Blocklist
from app.services.youtube.search_service import VideoHit


def _hit(vid: str, channel: str | None = None, channel_id: str | None = None) -> VideoHit:
    return VideoHit(
        video_id=vid,
        title=f"T {vid}",
        channel=channel,
        channel_id=channel_id,
        thumbnail=f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        url=f"https://www.youtube.com/watch?v={vid}",
    )


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeSession:
    """get_blocklist 的轻量假会话:execute(...).all() 返回 (match_field, normalized_value) 元组。"""

    def __init__(self, rows):
        self._rows = rows
        self.execute_calls = 0

    async def execute(self, _stmt):
        self.execute_calls += 1
        return _Result(self._rows)


# ---- 纯函数:词命中 ----


def test_is_term_blocked_exact_normalized() -> None:
    bl = Blocklist(terms=frozenset({"bad word"}), channel_ids=frozenset(), channel_names=frozenset())
    assert bls.is_term_blocked("bad word", bl) is True
    assert bls.is_term_blocked("good word", bl) is False


# ---- 纯函数:频道命中(按 id 与按归一化名两路)----


def test_is_channel_blocked_by_id() -> None:
    bl = Blocklist(terms=frozenset(), channel_ids=frozenset({"UCabc"}), channel_names=frozenset())
    assert bls.is_channel_blocked(_hit("v1", channel_id="UCabc"), bl) is True
    assert bls.is_channel_blocked(_hit("v2", channel_id="UCxyz"), bl) is False


def test_is_channel_blocked_by_normalized_name() -> None:
    bl = Blocklist(terms=frozenset(), channel_ids=frozenset(), channel_names=frozenset({"lex fridman"}))
    assert bls.is_channel_blocked(_hit("v1", channel="Lex  Fridman"), bl) is True
    assert bls.is_channel_blocked(_hit("v2", channel="Other"), bl) is False


def test_filter_hits_drops_blocked_channels() -> None:
    bl = Blocklist(terms=frozenset(), channel_ids=frozenset({"UCbad"}), channel_names=frozenset())
    hits = [_hit("v1", channel_id="UCbad"), _hit("v2", channel_id="UCok")]
    assert [h.video_id for h in bls.filter_hits(hits, bl)] == ["v2"]


# ---- 判型 ----


def test_classify_channel_input_bare_id() -> None:
    assert bls.classify_channel_input("UCXuqSBlHAE6Xw-yeJA0Tunw") == ("channel_id", "UCXuqSBlHAE6Xw-yeJA0Tunw")


def test_classify_channel_input_channel_url() -> None:
    assert bls.classify_channel_input("https://www.youtube.com/channel/UCXuqSBlHAE6Xw-yeJA0Tunw") == (
        "channel_id",
        "UCXuqSBlHAE6Xw-yeJA0Tunw",
    )


def test_classify_channel_input_display_name_normalized() -> None:
    assert bls.classify_channel_input("  Lex  Fridman ") == ("channel_name", "lex fridman")


def test_classify_channel_input_handle_treated_as_name() -> None:
    # v1 不把 @handle 映射成 channel_id —— 当作名字归一化
    assert bls.classify_channel_input("@LexFridman") == ("channel_name", "@lexfridman")


# ---- 加载 + 缓存 ----


async def test_get_blocklist_partitions_rows_and_unions_env(monkeypatch: pytest.MonkeyPatch) -> None:
    bls.invalidate_cache()
    monkeypatch.setattr(bls.settings, "YOUTUBE_SEARCH_DENYLIST", ["  Env Bad "])
    rows = [("query", "db term"), ("channel_id", "UC123"), ("channel_name", "lex fridman")]
    bl = await bls.get_blocklist(_FakeSession(rows))
    assert bl.terms == frozenset({"env bad", "db term"})  # env(归一化) ∪ DB
    assert bl.channel_ids == frozenset({"UC123"})
    assert bl.channel_names == frozenset({"lex fridman"})


async def test_get_blocklist_caches_within_ttl_and_invalidates(monkeypatch: pytest.MonkeyPatch) -> None:
    bls.invalidate_cache()
    monkeypatch.setattr(bls.settings, "BLOCKLIST_CACHE_TTL_SECONDS", 300)
    monkeypatch.setattr(bls.settings, "YOUTUBE_SEARCH_DENYLIST", [])
    session = _FakeSession([])
    await bls.get_blocklist(session)
    await bls.get_blocklist(session)
    assert session.execute_calls == 1  # 第二次命中缓存,未再查 DB
    bls.invalidate_cache()
    await bls.get_blocklist(session)
    assert session.execute_calls == 2  # 失效后重载
