from datetime import UTC, datetime

import pytest

import app.services.youtube.allowlist_service as als
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.models.youtube_allowlist import YouTubeAllowlistEntry
from app.services.youtube.allowlist_service import Allowlist
from app.services.youtube.search_service import VideoHit


def _hit(vid, channel=None, channel_id=None, handle=None) -> VideoHit:
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

    def scalars(self):
        return self

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.execute_calls = 0

    async def execute(self, _stmt):
        self.execute_calls += 1
        return _Result(self._rows)


def test_is_channel_allowed_by_id() -> None:
    al = Allowlist(channel_ids=frozenset({"UCabc"}), channel_handles=frozenset(), channel_names=frozenset())
    assert als.is_channel_allowed(_hit("v1", channel_id="UCabc"), al) is True
    assert als.is_channel_allowed(_hit("v2", channel_id="UCxyz"), al) is False


def test_is_channel_allowed_by_handle() -> None:
    al = Allowlist(channel_ids=frozenset(), channel_handles=frozenset({"globalnewstw"}), channel_names=frozenset())
    assert als.is_channel_allowed(_hit("v1", handle="@globalnewstw"), al) is True
    assert als.is_channel_allowed(_hit("v2", handle="@Other"), al) is False
    assert als.is_channel_allowed(_hit("v3", handle=None), al) is False


def test_is_channel_allowed_by_normalized_name() -> None:
    al = Allowlist(channel_ids=frozenset(), channel_handles=frozenset(), channel_names=frozenset({"lex fridman"}))
    assert als.is_channel_allowed(_hit("v1", channel="Lex  Fridman"), al) is True
    assert als.is_channel_allowed(_hit("v2", channel="Other"), al) is False


async def test_get_allowlist_partitions_rows() -> None:
    als.invalidate_cache()
    rows = [
        ("channel_id", "UCabc"),
        ("channel_handle", "globalnewstw"),
        ("channel_name", "lex fridman"),
    ]
    al = await als.get_allowlist(_FakeSession(rows))
    assert al.channel_ids == frozenset({"UCabc"})
    assert al.channel_handles == frozenset({"globalnewstw"})
    assert al.channel_names == frozenset({"lex fridman"})
    als.invalidate_cache()


async def test_get_allowlist_caches_within_ttl_and_invalidates() -> None:
    als.invalidate_cache()
    session = _FakeSession([("channel_id", "UCabc")])
    await als.get_allowlist(session)
    await als.get_allowlist(session)
    assert session.execute_calls == 1  # 第二次命中缓存
    als.invalidate_cache()
    await als.get_allowlist(session)
    assert session.execute_calls == 2  # 失效后重载
    als.invalidate_cache()


async def test_list_entries_returns_rows() -> None:
    rows = [
        YouTubeAllowlistEntry(
            match_field="channel_id", raw_value="UCabc", normalized_value="UCabc", note=None, created_by=None
        )
    ]
    out = await als.list_entries(_FakeSession(rows))
    assert out == rows


# ---------------------------------------------------------------------------
# Write-path helpers
# ---------------------------------------------------------------------------


class _CrudResult:
    def __init__(self, row=None):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _CrudSession:
    def __init__(self, *, found=None):
        self._found = found
        self.added: list = []
        self.committed = False

    async def execute(self, _stmt):
        return _CrudResult(row=self._found)

    def add(self, obj) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.committed = True


def _no_conflict(monkeypatch):
    async def _none(_db, _mf, _nv):
        return False

    monkeypatch.setattr(als, "_blocklist_has_active", _none)


# ---------------------------------------------------------------------------
# Write-path tests
# ---------------------------------------------------------------------------


async def test_add_entry_classifies_channel_id(monkeypatch) -> None:
    _no_conflict(monkeypatch)
    db = _CrudSession(found=None)
    entry, created = await als.add_entry(db, value="UCXuqSBlHAE6Xw-yeJA0Tunw", note=None, created_by=None)
    assert entry.match_field == "channel_id"
    assert entry.normalized_value == "UCXuqSBlHAE6Xw-yeJA0Tunw"
    assert created is True
    assert db.added == [entry] and db.committed is True


async def test_add_entry_resolves_handle_to_channel_id(monkeypatch) -> None:
    _no_conflict(monkeypatch)

    async def _meta(_raw):
        return ("UCp2f7tGJGN6R9Muxipem8Nw", "全球新闻")

    monkeypatch.setattr(als.blocklist_service, "resolve_channel_meta", _meta)
    db = _CrudSession(found=None)
    entry, _ = await als.add_entry(db, value="https://www.youtube.com/@globalnewstw", note=None, created_by=None)
    assert entry.match_field == "channel_id"
    assert entry.normalized_value == "UCp2f7tGJGN6R9Muxipem8Nw"
    assert entry.display_name == "全球新闻"


async def test_add_entry_bare_name_uses_input(monkeypatch) -> None:
    _no_conflict(monkeypatch)
    db = _CrudSession(found=None)
    entry, _ = await als.add_entry(db, value="Lex Fridman", note=None, created_by=None)
    assert entry.match_field == "channel_name"
    assert entry.normalized_value == "lex fridman"
    assert entry.display_name == "Lex Fridman"


async def test_add_entry_blank_value_raises(monkeypatch) -> None:
    _no_conflict(monkeypatch)
    with pytest.raises(BusinessError) as ei:
        await als.add_entry(_CrudSession(found=None), value="   ", note=None, created_by=None)
    assert ei.value.code == ErrorCode.INVALID_PARAMETER


async def test_add_entry_idempotent_when_active_exists(monkeypatch) -> None:
    _no_conflict(monkeypatch)
    existing = YouTubeAllowlistEntry(
        match_field="channel_name", raw_value="lex", normalized_value="lex fridman", note=None, created_by="x"
    )
    existing.deleted_at = None
    db = _CrudSession(found=existing)
    out, created = await als.add_entry(db, value="Lex Fridman", note="new", created_by="a2")
    assert out is existing and created is False
    assert db.added == [] and db.committed is False


async def test_add_entry_revives_soft_deleted(monkeypatch) -> None:
    _no_conflict(monkeypatch)
    existing = YouTubeAllowlistEntry(
        match_field="channel_name", raw_value="old", normalized_value="lex fridman", note="old", created_by="x"
    )
    existing.deleted_at = datetime(2020, 1, 1, tzinfo=UTC)
    db = _CrudSession(found=existing)
    out, created = await als.add_entry(db, value="Lex Fridman", note="new", created_by="a9")
    assert out is existing and created is True
    assert existing.deleted_at is None and existing.note == "new"
    assert db.committed is True


async def test_add_entry_rejects_when_blocklisted(monkeypatch) -> None:
    async def _conflict(_db, _mf, _nv):
        return True

    monkeypatch.setattr(als, "_blocklist_has_active", _conflict)
    db = _CrudSession(found=None)
    with pytest.raises(BusinessError) as ei:
        await als.add_entry(db, value="UCXuqSBlHAE6Xw-yeJA0Tunw", note=None, created_by=None)
    assert ei.value.code == ErrorCode.CHANNEL_BLOCKLIST_ALLOWLIST_CONFLICT
    assert db.added == [] and db.committed is False


async def test_delete_entry_soft_deletes_found() -> None:
    existing = YouTubeAllowlistEntry(
        match_field="channel_name", raw_value="x", normalized_value="x", note=None, created_by=None
    )
    existing.deleted_at = None
    db = _CrudSession(found=existing)
    ok = await als.delete_entry(db, "some-id")
    assert ok is True and existing.deleted_at is not None and db.committed is True


async def test_delete_entry_returns_false_when_missing() -> None:
    ok = await als.delete_entry(_CrudSession(found=None), "nope")
    assert ok is False
