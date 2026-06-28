from datetime import UTC, datetime

import pytest

import app.services.youtube.blocklist_service as bls
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.models.youtube_blocklist import YouTubeBlocklistEntry
from app.services.youtube.blocklist_service import Blocklist
from app.services.youtube.search_cache import normalize_query
from app.services.youtube.search_service import VideoHit


def _hit(
    vid: str,
    channel: str | None = None,
    channel_id: str | None = None,
    handle: str | None = None,
) -> VideoHit:
    return VideoHit(
        video_id=vid,
        title=f"T {vid}",
        channel=channel,
        channel_id=channel_id,
        handle=handle,
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


def test_is_channel_blocked_by_handle() -> None:
    # 结果 uploader_id 形如 "@globalnewstw";黑名单存归一化 handle "globalnewstw"
    bl = Blocklist(
        terms=frozenset(),
        channel_ids=frozenset(),
        channel_names=frozenset(),
        channel_handles=frozenset({"globalnewstw"}),
    )
    assert bls.is_channel_blocked(_hit("v1", handle="@globalnewstw"), bl) is True
    assert bls.is_channel_blocked(_hit("v2", handle="@OtherChannel"), bl) is False
    assert bls.is_channel_blocked(_hit("v3", handle=None), bl) is False


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


def test_classify_channel_input_bare_handle() -> None:
    # @handle → channel_handle(去 @、casefold)
    assert bls.classify_channel_input("@LexFridman") == ("channel_handle", "lexfridman")


def test_classify_channel_input_handle_url() -> None:
    assert bls.classify_channel_input("https://www.youtube.com/@globalnewstw") == (
        "channel_handle",
        "globalnewstw",
    )


def test_classify_channel_input_handle_url_percent_encoded() -> None:
    # 浏览器对非 ASCII handle 百分号编码:%E5%A4%B8%E5%85%8B%E8%AF%B4 == 夸克说
    assert bls.classify_channel_input("https://www.youtube.com/@%E5%A4%B8%E5%85%8B%E8%AF%B4") == (
        "channel_handle",
        "夸克说",
    )


def test_classify_channel_input_mobile_subdomain_handle() -> None:
    assert bls.classify_channel_input("https://m.youtube.com/@globalnewstw") == ("channel_handle", "globalnewstw")


def test_classify_channel_input_fake_youtube_host_not_handle() -> None:
    # 主机边界:notyoutube.com / xyoutube.com 不是 youtube 主机 → 不判 handle,落回按名匹配
    assert bls.classify_channel_input("https://notyoutube.com/@foo")[0] == "channel_name"
    assert bls.classify_channel_input("https://xyoutube.com/@foo")[0] == "channel_name"


def test_classify_channel_input_junk_handle_falls_back_to_name() -> None:
    # 解码后混入 / @ 等非法字符 → 不当 handle(不存垃圾、不喂畸形路径给 yt-dlp)
    field, _ = bls.classify_channel_input("https://www.youtube.com/@foo%2F..%2F@evil.com")
    assert field == "channel_name"


def test_normalize_handle_strips_before_stripping_at() -> None:
    assert bls.normalize_handle(" @GlobalNewsTW ") == "globalnewstw"
    assert bls.normalize_handle("@夸克说") == "夸克说"


def test_is_channel_blocked_by_fields_three_dimensions() -> None:
    bl = Blocklist(
        terms=frozenset(),
        channel_ids=frozenset({"UCabc"}),
        channel_names=frozenset({"lex fridman"}),
        channel_handles=frozenset({"globalnewstw"}),
    )
    assert bls.is_channel_blocked_by_fields("UCabc", None, None, bl) is True
    assert bls.is_channel_blocked_by_fields(None, "@GlobalNewsTW", None, bl) is True  # handle 归一化
    assert bls.is_channel_blocked_by_fields(None, None, "Lex  Fridman", bl) is True   # name normalize_query
    assert bls.is_channel_blocked_by_fields(None, None, None, bl) is False            # 全 None 放行
    assert bls.is_channel_blocked_by_fields("UCxyz", "@other", "Other", bl) is False  # 均未命中


# ---- 加载 + 缓存 ----


class _SyncFakeSession:
    """get_blocklist_sync 的同步假会话:execute(...).all() 返回 (match_field, normalized_value)。"""

    def __init__(self, rows):
        self._rows = rows
        self.execute_calls = 0

    def execute(self, _stmt):
        self.execute_calls += 1
        return _Result(self._rows)


def test_get_blocklist_sync_partitions_and_shares_cache() -> None:
    bls.invalidate_cache()
    rows = [
        ("channel_id", "UCbad"),
        ("query", "bad word"),
        ("channel_handle", "h1"),
        ("channel_name", "some name"),
    ]
    session = _SyncFakeSession(rows)
    bl = bls.get_blocklist_sync(session)
    assert "UCbad" in bl.channel_ids
    assert "bad word" in bl.terms
    assert "h1" in bl.channel_handles
    assert "some name" in bl.channel_names
    # 第二次命中缓存,不再查库
    bls.get_blocklist_sync(session)
    assert session.execute_calls == 1
    bls.invalidate_cache()


async def test_get_blocklist_partitions_rows_and_unions_env(monkeypatch: pytest.MonkeyPatch) -> None:
    bls.invalidate_cache()
    monkeypatch.setattr(bls.settings, "YOUTUBE_SEARCH_DENYLIST", ["  Env Bad "])
    rows = [
        ("query", "db term"),
        ("channel_id", "UC123"),
        ("channel_name", "lex fridman"),
        ("channel_handle", "globalnewstw"),
    ]
    bl = await bls.get_blocklist(_FakeSession(rows))
    assert bl.terms == frozenset({"env bad", "db term"})  # env(归一化) ∪ DB
    assert bl.channel_ids == frozenset({"UC123"})
    assert bl.channel_names == frozenset({"lex fridman"})
    assert bl.channel_handles == frozenset({"globalnewstw"})


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
    entry, _ = await bls.add_entry(db, kind="term", value="  Bad Word ", note="x", created_by="admin-1")
    assert entry.kind == "term"
    assert entry.match_field == "query"
    assert entry.normalized_value == "bad word"
    assert entry.raw_value == "Bad Word"
    assert db.added == [entry]
    assert db.committed is True


async def test_add_entry_classifies_channel_id() -> None:
    db = _CrudSession(found=None)
    entry, _ = await bls.add_entry(db, kind="channel", value="UCXuqSBlHAE6Xw-yeJA0Tunw", note=None, created_by=None)
    assert entry.match_field == "channel_id"
    assert entry.normalized_value == "UCXuqSBlHAE6Xw-yeJA0Tunw"


async def test_add_entry_resolves_handle_url_to_channel_id(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_resolve_meta(_raw: str) -> tuple[str | None, str | None]:
        return ("UCp2f7tGJGN6R9Muxipem8Nw", "全球新闻")

    monkeypatch.setattr(bls, "resolve_channel_meta", _fake_resolve_meta)
    db = _CrudSession(found=None)
    entry, _ = await bls.add_entry(
        db, kind="channel", value="https://www.youtube.com/@globalnewstw", note=None, created_by=None
    )
    assert entry.match_field == "channel_id"
    assert entry.normalized_value == "UCp2f7tGJGN6R9Muxipem8Nw"
    assert entry.raw_value == "https://www.youtube.com/@globalnewstw"  # 原样保留管理员所填


async def test_add_entry_handle_falls_back_when_resolution_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fail_resolve_meta(_raw: str) -> tuple[str | None, str | None]:
        return (None, None)

    monkeypatch.setattr(bls, "resolve_channel_meta", _fail_resolve_meta)
    db = _CrudSession(found=None)
    entry, _ = await bls.add_entry(
        db, kind="channel", value="https://www.youtube.com/@globalnewstw", note=None, created_by=None
    )
    # 解析失败 → 落库为 handle,匹配回落到结果 uploader_id
    assert entry.match_field == "channel_handle"
    assert entry.normalized_value == "globalnewstw"


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
    out, _ = await bls.add_entry(db, kind="term", value="Bad Word", note="new", created_by="admin-2")
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
    out, _ = await bls.add_entry(db, kind="channel", value="Lex Fridman", note="new reason", created_by="admin-9")
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


# ---- _resolve_channel_meta_sync ----


def test_resolve_channel_meta_sync_extracts_id_and_name(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeYDL:
        def __init__(self, *a, **k): ...

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return {"channel_id": "UCp2f7tGJGN6R9Muxipem8Nw", "channel": "全球新闻", "uploader": "x", "title": "y"}

    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeYDL)
    cid, name = bls._resolve_channel_meta_sync("https://www.youtube.com/@globalnewstw")
    assert cid == "UCp2f7tGJGN6R9Muxipem8Nw"
    assert name == "全球新闻"


def test_resolve_channel_meta_sync_handles_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BoomYDL:
        def __init__(self, *a, **k): ...

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            raise RuntimeError("network down")

    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _BoomYDL)
    assert bls._resolve_channel_meta_sync("https://www.youtube.com/@x") == (None, None)


# ---- add_entry 抓名 ----


async def test_add_entry_stores_explicit_name() -> None:
    # promote 路径:value 是裸 channel_id,name 显式传入 → 入库 display_name
    db = _CrudSession(found=None)
    entry, _ = await bls.add_entry(
        db, kind="channel", value="UCXuqSBlHAE6Xw-yeJA0Tunw", note=None, created_by=None, name="BBC News 中文"
    )
    assert entry.display_name == "BBC News 中文"


async def test_add_entry_handle_captures_resolved_name(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _meta(_raw: str) -> tuple[str | None, str | None]:
        return ("UCp2f7tGJGN6R9Muxipem8Nw", "全球新闻")

    monkeypatch.setattr(bls, "resolve_channel_meta", _meta)
    db = _CrudSession(found=None)
    entry, _ = await bls.add_entry(
        db, kind="channel", value="https://www.youtube.com/@globalnewstw", note=None, created_by=None
    )
    assert entry.match_field == "channel_id"
    assert entry.display_name == "全球新闻"


async def test_add_entry_bare_name_uses_input() -> None:
    db = _CrudSession(found=None)
    entry, _ = await bls.add_entry(db, kind="channel", value="Lex Fridman", note=None, created_by=None)
    assert entry.match_field == "channel_name"
    assert entry.display_name == "Lex Fridman"


async def test_add_entry_bare_channel_id_without_name_leaves_display_none() -> None:
    # 有意收窄:裸 UCID 不在加黑名单时做 yt-dlp 取名 → display_name 留空(交回填脚本)
    db = _CrudSession(found=None)
    entry, _ = await bls.add_entry(db, kind="channel", value="UCXuqSBlHAE6Xw-yeJA0Tunw", note=None, created_by=None)
    assert entry.match_field == "channel_id"
    assert entry.display_name is None


async def test_add_entry_term_has_no_display_name() -> None:
    db = _CrudSession(found=None)
    entry, _ = await bls.add_entry(db, kind="term", value="赌博", note=None, created_by=None, name="ignored")
    assert entry.display_name is None


async def test_add_entry_returns_created_true_on_insert() -> None:
    db = _CrudSession(found=None)
    entry, created = await bls.add_entry(db, kind="term", value="fresh word", note=None, created_by=None)
    assert created is True
    assert entry.raw_value == "fresh word"


async def test_add_entry_returns_created_false_when_active_exists() -> None:
    existing = YouTubeBlocklistEntry(
        kind="term",
        match_field="query",
        raw_value="dup",
        normalized_value=normalize_query("dup"),
        note=None,
        created_by=None,
    )
    existing.deleted_at = None
    db = _CrudSession(found=existing)
    out, created = await bls.add_entry(db, kind="term", value="dup", note="ignored", created_by=None)
    assert created is False
    assert out is existing


async def test_add_entry_returns_created_true_on_revive() -> None:
    revived = YouTubeBlocklistEntry(
        kind="channel",
        match_field="channel_name",
        raw_value="Lex Fridman",
        normalized_value=normalize_query("Lex Fridman"),
        note=None,
        created_by=None,
    )
    revived.deleted_at = datetime.now(UTC)
    db = _CrudSession(found=revived)
    out, created = await bls.add_entry(db, kind="channel", value="Lex Fridman", note="new", created_by="a")
    assert created is True
    assert out.deleted_at is None
