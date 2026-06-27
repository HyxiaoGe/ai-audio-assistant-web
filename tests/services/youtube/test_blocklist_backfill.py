from types import SimpleNamespace

import pytest

from app.services.youtube import blocklist_backfill as bb

pytestmark = pytest.mark.asyncio


class _FakeCommitDB:
    def __init__(self) -> None:
        self.committed = False

    async def commit(self) -> None:
        self.committed = True


async def test_backfill_fills_from_local_index_and_network(monkeypatch: pytest.MonkeyPatch) -> None:
    # 三条 channel 型待回填条目:① channel_id 命中本地索引 ② channel_name 用 raw_value ③ channel_id 仅 yt-dlp 命中
    e_local = SimpleNamespace(
        match_field="channel_id", normalized_value="UCaaa", raw_value="https://x/@a", display_name=None
    )
    e_name = SimpleNamespace(
        match_field="channel_name", normalized_value="lex fridman", raw_value="Lex Fridman", display_name=None
    )
    e_net = SimpleNamespace(match_field="channel_id", normalized_value="UCbbb", raw_value="UCbbb", display_name=None)

    async def _pending(_db):
        return [e_local, e_name, e_net]

    async def _index(_db):
        return ({"UCaaa": "本地频道A"}, {})  # by_channel_id, by_handle

    async def _by_id(cid: str):
        return "网络频道B" if cid == "UCbbb" else None

    monkeypatch.setattr(bb, "_pending_name_entries", _pending)
    monkeypatch.setattr(bb, "_build_local_name_index", _index)
    monkeypatch.setattr(bb.blocklist_service, "resolve_channel_name_by_id", _by_id)

    db = _FakeCommitDB()
    stats = await bb.backfill_display_names(db)

    assert e_local.display_name == "本地频道A"
    assert e_name.display_name == "Lex Fridman"
    assert e_net.display_name == "网络频道B"
    assert stats == {"total": 3, "filled": 3, "unresolved": 0}
    assert db.committed is True


async def test_backfill_leaves_unresolved_when_all_sources_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    e = SimpleNamespace(match_field="channel_id", normalized_value="UCzzz", raw_value="UCzzz", display_name=None)

    async def _pending(_db):
        return [e]

    async def _index(_db):
        return ({}, {})

    async def _by_id(_cid: str):
        return None

    monkeypatch.setattr(bb, "_pending_name_entries", _pending)
    monkeypatch.setattr(bb, "_build_local_name_index", _index)
    monkeypatch.setattr(bb.blocklist_service, "resolve_channel_name_by_id", _by_id)

    stats = await bb.backfill_display_names(_FakeCommitDB())
    assert e.display_name is None
    assert stats == {"total": 1, "filled": 0, "unresolved": 1}


async def test_backfill_use_network_false_skips_yt_dlp(monkeypatch: pytest.MonkeyPatch) -> None:
    e = SimpleNamespace(match_field="channel_id", normalized_value="UCbbb", raw_value="UCbbb", display_name=None)

    async def _pending(_db):
        return [e]

    async def _index(_db):
        return ({}, {})

    async def _boom_by_id(_cid: str):
        raise AssertionError("use_network=False 不该走网络")

    monkeypatch.setattr(bb, "_pending_name_entries", _pending)
    monkeypatch.setattr(bb, "_build_local_name_index", _index)
    monkeypatch.setattr(bb.blocklist_service, "resolve_channel_name_by_id", _boom_by_id)

    stats = await bb.backfill_display_names(_FakeCommitDB(), use_network=False)
    assert e.display_name is None
    assert stats == {"total": 1, "filled": 0, "unresolved": 1}
