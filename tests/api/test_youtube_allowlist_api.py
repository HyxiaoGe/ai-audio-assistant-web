from __future__ import annotations

import datetime
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import httpx
from fastapi import FastAPI, Request
from httpx import ASGITransport

from app.api.deps import CurrentUser, get_admin_user, get_db
from app.api.v1 import youtube_allowlist
from app.core.exceptions import BusinessError
from app.core.response import error
from app.i18n.codes import ErrorCode
from app.services.youtube import allowlist_service

_ADMIN = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


class _Entry:
    """AllowlistEntryOut.model_validate(...) 用的鸭子类型行(from_attributes)。"""

    def __init__(self, *, id, match_field, raw_value, normalized_value="", display_name=None, note=None, created_at):
        self.id = id
        self.match_field = match_field
        self.raw_value = raw_value
        self.normalized_value = normalized_value
        self.display_name = display_name
        self.note = note
        self.created_at = created_at


def _entry(eid: str, match_field: str, raw_value: str, normalized_value: str = "") -> _Entry:
    return _Entry(
        id=eid,
        match_field=match_field,
        raw_value=raw_value,
        normalized_value=normalized_value,
        display_name=None,
        note=None,
        created_at=datetime.datetime(2026, 6, 26, tzinfo=datetime.UTC),
    )


def _make_app(monkeypatch: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(youtube_allowlist.router, prefix="/api/v1")

    @app.exception_handler(BusinessError)
    async def _handle(_req: Request, exc: BusinessError) -> Any:
        return error(int(exc.code), exc.code.name)

    async def _db() -> AsyncIterator[object]:
        yield object()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_admin_user] = lambda: CurrentUser(id=_ADMIN, email="a@ex.com", scopes=["admin"])
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_list_returns_entries(monkeypatch: Any) -> None:
    async def _list(_db: Any) -> list[_Entry]:
        return [
            _entry("e1", "channel_id", "UCabc", "UCabc"),
            _entry("e2", "channel_name", "BBC News", "bbc news"),
        ]

    monkeypatch.setattr(allowlist_service, "list_entries", _list)
    async with _client(_make_app(monkeypatch)) as client:
        body = (await client.get("/api/v1/admin/youtube-allowlist")).json()
    assert body["code"] == 0
    raws = [i["raw_value"] for i in body["data"]["items"]]
    assert raws == ["UCabc", "BBC News"]


async def test_add_returns_entry_and_invalidates(monkeypatch: Any) -> None:
    invalidated = {"v": False}

    async def _add(db: Any, *, value: str, note: Any, created_by: Any, name: Any = None) -> tuple[_Entry, bool]:
        return SimpleNamespace(
            id="a1",
            match_field="channel_id",
            raw_value=value,
            normalized_value=value,
            display_name=None,
            note=note,
            created_at=datetime.datetime.now(datetime.UTC),
        ), True

    monkeypatch.setattr(allowlist_service, "add_entry", _add)
    monkeypatch.setattr(allowlist_service, "invalidate_cache", lambda: invalidated.__setitem__("v", True))
    async with _client(_make_app(monkeypatch)) as client:
        body = (await client.post("/api/v1/admin/youtube-allowlist", json={"value": "UCabc"})).json()
    assert body["code"] == 0
    assert body["data"]["match_field"] == "channel_id"
    assert invalidated["v"] is True


async def test_add_duplicate_returns_conflict(monkeypatch: Any) -> None:
    invalidated = {"v": False}

    async def _add(db: Any, *, value: str, note: Any, created_by: Any, name: Any = None) -> tuple[_Entry, bool]:
        return _entry("a1", "channel_id", value, value), False  # 已存在活跃行

    monkeypatch.setattr(allowlist_service, "add_entry", _add)
    monkeypatch.setattr(allowlist_service, "invalidate_cache", lambda: invalidated.__setitem__("v", True))
    async with _client(_make_app(monkeypatch)) as client:
        body = (await client.post("/api/v1/admin/youtube-allowlist", json={"value": "UCabc"})).json()
    assert body["code"] == int(ErrorCode.ALLOWLIST_ENTRY_EXISTS)  # 40908
    assert invalidated["v"] is False  # 重复时不写缓存失效


async def test_delete_success_and_invalidates(monkeypatch: Any) -> None:
    invalidated = {"v": False}

    async def _del(_db: Any, _id: str) -> bool:
        return True

    monkeypatch.setattr(allowlist_service, "delete_entry", _del)
    monkeypatch.setattr(allowlist_service, "invalidate_cache", lambda: invalidated.__setitem__("v", True))
    async with _client(_make_app(monkeypatch)) as client:
        body = (await client.delete("/api/v1/admin/youtube-allowlist/a1")).json()
    assert body["code"] == 0
    assert invalidated["v"] is True


async def test_delete_missing_returns_not_found(monkeypatch: Any) -> None:
    async def _del(_db: Any, _id: str) -> bool:
        return False

    monkeypatch.setattr(allowlist_service, "delete_entry", _del)
    async with _client(_make_app(monkeypatch)) as client:
        body = (await client.delete("/api/v1/admin/youtube-allowlist/nope")).json()
    assert body["code"] == int(ErrorCode.RESOURCE_NOT_FOUND)
