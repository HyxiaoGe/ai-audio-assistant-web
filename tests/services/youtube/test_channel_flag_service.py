import asyncio
from types import SimpleNamespace

import pytest

from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.services.youtube import channel_flag_service as cfs


def _vh(vid="v", title="t", channel=None, channel_id=None, handle=None):
    return SimpleNamespace(video_id=vid, title=title, channel=channel, channel_id=channel_id, handle=handle)


# ---- _flag_identity:三级优先 ----


def test_identity_prefers_channel_id() -> None:
    assert cfs._flag_identity(_vh(channel_id="UCabc", handle="Foo", channel="Foo Bar")) == ("channel_id", "UCabc")


def test_identity_falls_to_handle() -> None:
    assert cfs._flag_identity(_vh(handle="@Foo", channel="Foo Bar")) == ("channel_handle", "foo")


def test_identity_falls_to_name() -> None:
    field, value = cfs._flag_identity(_vh(channel="Foo Bar"))
    assert field == "channel_name" and value  # normalize_query 归一化非空


def test_identity_none_when_no_attribution() -> None:
    assert cfs._flag_identity(_vh()) is None


# ---- record_flags:best-effort ----


def test_record_flags_empty_does_not_open_session(monkeypatch) -> None:
    def _boom():
        raise AssertionError("不该开 session")

    monkeypatch.setattr(cfs, "async_session_factory", _boom)
    asyncio.run(cfs.record_flags([]))  # 不抛


def test_record_flags_all_unattributed_skips_session(monkeypatch) -> None:
    def _boom():
        raise AssertionError("不该开 session")

    monkeypatch.setattr(cfs, "async_session_factory", _boom)
    asyncio.run(cfs.record_flags([_vh()]))  # 三无身份 → 全跳过 → 不开 session


def test_record_flags_swallows_db_errors(monkeypatch) -> None:
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(cfs, "async_session_factory", _boom)
    # 有可归因项 → 进 try → 工厂抛 → 被吞,record_flags 不抛
    asyncio.run(cfs.record_flags([_vh(channel_id="UCx", title="bad")]))


# ---- resolve:状态机 ----


class _FakeDB:
    def __init__(self, flag):
        self._flag = flag
        self.committed = False
        self.rolled_back = False

    async def get(self, model, pk):
        return self._flag

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


def _pending_flag():
    return SimpleNamespace(
        id="f1",
        status="pending",
        channel_id="UCx",
        channel_handle=None,
        channel_name="Evil",
        resolved_by=None,
        resolved_at=None,
    )


def test_resolve_block_promotes_and_marks(monkeypatch) -> None:
    calls = {}

    async def _add(db, *, kind, value, note, created_by):
        calls["add"] = (kind, value, created_by)

    monkeypatch.setattr(cfs.blocklist_service, "add_entry", _add)
    monkeypatch.setattr(cfs.blocklist_service, "invalidate_cache", lambda: calls.setdefault("inval", True))
    flag = _pending_flag()
    db = _FakeDB(flag)
    out = asyncio.run(cfs.resolve(db, flag_id="f1", action="block", admin_id="admin-1"))
    assert out.status == "blocked"
    assert calls["add"] == ("channel", "UCx", "admin-1")  # 取最强原值 channel_id
    assert calls.get("inval") is True
    assert flag.resolved_by == "admin-1" and flag.resolved_at is not None
    assert db.committed


def test_resolve_dismiss_marks_without_blocklist(monkeypatch) -> None:
    called = {"add": False}

    async def _add(*a, **k):
        called["add"] = True

    monkeypatch.setattr(cfs.blocklist_service, "add_entry", _add)
    monkeypatch.setattr(cfs.blocklist_service, "invalidate_cache", lambda: None)
    flag = _pending_flag()
    out = asyncio.run(cfs.resolve(_FakeDB(flag), flag_id="f1", action="dismiss", admin_id="a2"))
    assert out.status == "dismissed"
    assert called["add"] is False


def test_resolve_not_found_raises() -> None:
    with pytest.raises(BusinessError) as ei:
        asyncio.run(cfs.resolve(_FakeDB(None), flag_id="x", action="block", admin_id="a"))
    assert ei.value.code == ErrorCode.RESOURCE_NOT_FOUND


def test_resolve_non_pending_raises() -> None:
    flag = _pending_flag()
    flag.status = "dismissed"
    with pytest.raises(BusinessError) as ei:
        asyncio.run(cfs.resolve(_FakeDB(flag), flag_id="f1", action="block", admin_id="a"))
    assert ei.value.code == ErrorCode.FLAG_ALREADY_RESOLVED


def test_resolve_invalid_action_raises() -> None:
    with pytest.raises(BusinessError) as ei:
        asyncio.run(cfs.resolve(_FakeDB(_pending_flag()), flag_id="f1", action="frob", admin_id="a"))
    assert ei.value.code == ErrorCode.INVALID_PARAMETER


def test_resolve_block_add_entry_failure_rolls_back(monkeypatch) -> None:
    async def _add_boom(*a, **k):
        raise RuntimeError("yt-dlp down")

    monkeypatch.setattr(cfs.blocklist_service, "add_entry", _add_boom)
    monkeypatch.setattr(cfs.blocklist_service, "invalidate_cache", lambda: None)
    flag = _pending_flag()
    db = _FakeDB(flag)
    with pytest.raises(RuntimeError):
        asyncio.run(cfs.resolve(db, flag_id="f1", action="block", admin_id="a"))
    assert flag.status == "pending"  # 未改
    assert db.rolled_back and not db.committed
