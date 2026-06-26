from datetime import UTC, datetime

import pytest

import app.services.youtube.blocklist_service as bls
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.models.youtube_blocklist import YouTubeBlocklistEntry
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


class _CrudResult:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._row

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _CrudSession:
    def __init__(self, *, found=None, rows=None):
        self._found = found
        self._rows = rows or []
        self.added: list = []
        self.committed = False

    async def execute(self, _stmt):
        return _CrudResult(row=self._found, rows=self._rows)

    def add(self, obj) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.committed = True


async def test_add_entry_inserts_new_term() -> None:
    db = _CrudSession(found=None)
    entry = await bls.add_entry(db, kind="term", value="  Bad Word ", note="x", created_by="admin-1")
    assert entry.kind == "term"
    assert entry.match_field == "query"
    assert entry.normalized_value == "bad word"
    assert entry.raw_value == "Bad Word"
    assert db.added == [entry]
    assert db.committed is True


async def test_add_entry_classifies_channel_id() -> None:
    db = _CrudSession(found=None)
    entry = await bls.add_entry(db, kind="channel", value="UCXuqSBlHAE6Xw-yeJA0Tunw", note=None, created_by=None)
    assert entry.match_field == "channel_id"
    assert entry.normalized_value == "UCXuqSBlHAE6Xw-yeJA0Tunw"


async def test_add_entry_blank_value_raises() -> None:
    db = _CrudSession(found=None)
    with pytest.raises(BusinessError) as ei:
        await bls.add_entry(db, kind="term", value="   ", note=None, created_by=None)
    assert ei.value.code == ErrorCode.INVALID_PARAMETER


async def test_add_entry_idempotent_when_active_exists() -> None:
    existing = YouTubeBlocklistEntry(
        kind="term",
        match_field="query",
        raw_value="bad word",
        normalized_value="bad word",
        note=None,
        created_by="x",
    )
    existing.deleted_at = None
    db = _CrudSession(found=existing)
    out = await bls.add_entry(db, kind="term", value="Bad Word", note="new", created_by="admin-2")
    assert out is existing
    assert db.added == []  # 未重复插入
    assert db.committed is False  # 活跃命中:纯幂等,不写库


async def test_add_entry_revives_soft_deleted() -> None:
    existing = YouTubeBlocklistEntry(
        kind="channel",
        match_field="channel_name",
        raw_value="old",
        normalized_value="lex fridman",
        note="old",
        created_by="x",
    )
    existing.deleted_at = datetime(2020, 1, 1, tzinfo=UTC)
    db = _CrudSession(found=existing)
    out = await bls.add_entry(db, kind="channel", value="Lex Fridman", note="new reason", created_by="admin-9")
    assert out is existing
    assert existing.deleted_at is None
    assert existing.note == "new reason"
    assert existing.created_by == "admin-9"
    assert existing.raw_value == "Lex Fridman"
    assert db.committed is True


async def test_delete_entry_soft_deletes_found() -> None:
    existing = YouTubeBlocklistEntry(
        kind="term",
        match_field="query",
        raw_value="x",
        normalized_value="x",
        note=None,
        created_by=None,
    )
    existing.deleted_at = None
    db = _CrudSession(found=existing)
    ok = await bls.delete_entry(db, "some-id")
    assert ok is True
    assert existing.deleted_at is not None
    assert db.committed is True


async def test_delete_entry_returns_false_when_missing() -> None:
    db = _CrudSession(found=None)
    ok = await bls.delete_entry(db, "nope")
    assert ok is False
    assert db.committed is False


async def test_list_entries_returns_rows() -> None:
    rows = [
        YouTubeBlocklistEntry(
            kind="term",
            match_field="query",
            raw_value="a",
            normalized_value="a",
            note=None,
            created_by=None,
        )
    ]
    db = _CrudSession(rows=rows)
    out = await bls.list_entries(db)
    assert out == rows
