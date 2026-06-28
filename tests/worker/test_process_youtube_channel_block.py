from __future__ import annotations

from contextlib import contextmanager

import pytest

from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.services.youtube.blocklist_service import Blocklist, normalize_handle
from worker.tasks import process_youtube


@contextmanager
def _fake_session():
    yield object()  # _check_channel_blocklist 只把 session 透传给 get_blocklist_sync(已被 patch)


def _patch(monkeypatch, bl: Blocklist) -> None:
    monkeypatch.setattr(process_youtube, "get_sync_db_session", _fake_session)
    monkeypatch.setattr(
        process_youtube.blocklist_service, "get_blocklist_sync", lambda _session: bl
    )


def test_check_channel_blocklist_raises_when_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    bl = Blocklist(terms=frozenset(), channel_ids=frozenset({"UCbad"}), channel_names=frozenset())
    _patch(monkeypatch, bl)
    with pytest.raises(BusinessError) as ei:
        process_youtube._check_channel_blocklist("UCbad", None, "Some Name")
    assert ei.value.code == ErrorCode.CHANNEL_BLOCKED


def test_check_channel_blocklist_passes_when_not_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    bl = Blocklist(terms=frozenset(), channel_ids=frozenset({"UCbad"}), channel_names=frozenset())
    _patch(monkeypatch, bl)
    process_youtube._check_channel_blocklist("UCok", None, "Other")  # 不抛


def test_check_channel_blocklist_noop_when_no_identifiers(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"n": 0}

    @contextmanager
    def _counting_session():
        called["n"] += 1
        yield object()

    monkeypatch.setattr(process_youtube, "get_sync_db_session", _counting_session)
    process_youtube._check_channel_blocklist(None, None, None)  # 无任何标识 → 不查库、不抛
    assert called["n"] == 0


def test_check_channel_blocklist_raises_when_handle_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """handle-only 黑名单条目(无 channel_id)仍能被 worker 拦截。"""
    bl = Blocklist(
        terms=frozenset(),
        channel_ids=frozenset(),
        channel_names=frozenset(),
        channel_handles=frozenset({normalize_handle("@lexbad")}),
    )
    _patch(monkeypatch, bl)
    with pytest.raises(BusinessError) as ei:
        process_youtube._check_channel_blocklist(None, "@lexbad", None)
    assert ei.value.code == ErrorCode.CHANNEL_BLOCKED


def test_check_channel_blocklist_passes_when_handle_not_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """handle 不在黑名单时正常放行,零误杀。"""
    bl = Blocklist(
        terms=frozenset(),
        channel_ids=frozenset(),
        channel_names=frozenset(),
        channel_handles=frozenset({normalize_handle("@lexbad")}),
    )
    _patch(monkeypatch, bl)
    process_youtube._check_channel_blocklist(None, "@lexgood", None)  # 不抛
