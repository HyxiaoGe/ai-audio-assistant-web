from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import FastAPI, Request
from httpx import ASGITransport

from app.api.deps import CurrentUser, get_admin_user, get_db
from app.api.v1 import youtube_blocklist
from app.core.exceptions import BusinessError
from app.core.response import error
from app.i18n.codes import ErrorCode
from app.services.youtube import blocklist_service

_ADMIN = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


class _Entry:
    """BlocklistEntryOut.model_validate(...) 用的鸭子类型行(from_attributes)。"""

    def __init__(self, *, id, kind, match_field, raw_value, note, created_at):
        self.id = id
        self.kind = kind
        self.match_field = match_field
        self.raw_value = raw_value
        self.note = note
        self.created_at = created_at


def _entry(eid: str, kind: str, match_field: str, raw_value: str) -> _Entry:
    return _Entry(
        id=eid,
        kind=kind,
        match_field=match_field,
        raw_value=raw_value,
        note=None,
        created_at=datetime(2026, 6, 26, tzinfo=UTC),
    )


def _make_app(monkeypatch: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(youtube_blocklist.router, prefix="/api/v1")

    @app.exception_handler(BusinessError)
    async def _handle(_req: Request, exc: BusinessError) -> Any:
        return error(int(exc.code), exc.code.name)

    async def _db() -> AsyncIterator[object]:
        yield object()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_admin_user] = lambda: CurrentUser(id=_ADMIN, email="a@ex.com", scopes=["admin"])
    # 刻意不桩 invalidate_cache:真实现仅清进程缓存,端点调用它在测试里无害。
    # add 用例自行覆盖成计数器(因这里不设默认,不会被 clobber)。
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_list_returns_entries(monkeypatch: Any) -> None:
    async def _list(_db: Any) -> list[_Entry]:
        return [_entry("e1", "term", "query", "bad word"), _entry("e2", "channel", "channel_name", "Lex Fridman")]

    monkeypatch.setattr(blocklist_service, "list_entries", _list)
    async with _client(_make_app(monkeypatch)) as client:
        body = (await client.get("/api/v1/admin/youtube-blocklist")).json()
    assert body["code"] == 0
    raws = [i["raw_value"] for i in body["data"]["items"]]
    assert raws == ["bad word", "Lex Fridman"]


async def test_add_channel_passes_admin_id_and_invalidates(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    inval = {"n": 0}

    async def _add(_db: Any, *, kind: str, value: str, note: Any, created_by: Any) -> _Entry:
        captured.update(kind=kind, value=value, note=note, created_by=created_by)
        return _entry("e9", "channel", "channel_name", value)

    monkeypatch.setattr(blocklist_service, "add_entry", _add)
    monkeypatch.setattr(blocklist_service, "invalidate_cache", lambda: inval.__setitem__("n", inval["n"] + 1))
    async with _client(_make_app(monkeypatch)) as client:
        body = (
            await client.post("/api/v1/admin/youtube-blocklist", json={"kind": "channel", "value": "Lex Fridman"})
        ).json()
    assert body["code"] == 0
    assert body["data"]["raw_value"] == "Lex Fridman"
    assert captured["kind"] == "channel"
    assert captured["created_by"] == _ADMIN
    assert inval["n"] == 1


async def test_delete_missing_returns_not_found(monkeypatch: Any) -> None:
    async def _del(_db: Any, _id: str) -> bool:
        return False

    monkeypatch.setattr(blocklist_service, "delete_entry", _del)
    async with _client(_make_app(monkeypatch)) as client:
        body = (await client.delete("/api/v1/admin/youtube-blocklist/nope")).json()
    assert body["code"] == int(ErrorCode.RESOURCE_NOT_FOUND)


async def test_delete_success(monkeypatch: Any) -> None:
    async def _del(_db: Any, _id: str) -> bool:
        return True

    monkeypatch.setattr(blocklist_service, "delete_entry", _del)
    async with _client(_make_app(monkeypatch)) as client:
        body = (await client.delete("/api/v1/admin/youtube-blocklist/e1")).json()
    assert body["code"] == 0


async def test_requires_admin(monkeypatch: Any) -> None:
    # 不覆盖 get_admin_user → 真实依赖触发(无 token → 鉴权失败),验证端点受管理员保护。
    app = _make_app(monkeypatch)
    app.dependency_overrides.pop(get_admin_user, None)
    async with _client(app) as client:
        resp = await client.get("/api/v1/admin/youtube-blocklist")
    assert resp.status_code in (401, 403) or resp.json().get("code") not in (0, None)
