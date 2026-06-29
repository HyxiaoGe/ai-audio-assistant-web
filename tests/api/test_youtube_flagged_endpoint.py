from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_admin_user, get_current_user, get_db
from app.api.v1 import youtube_flagged
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.services.youtube import channel_flag_service


def _make_app(monkeypatch: pytest.MonkeyPatch, *, admin: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(youtube_flagged.router, prefix="/api/v1")

    async def _no_db() -> Any:
        yield None

    user = SimpleNamespace(id="admin-1", email="a@x", scopes=["admin"] if admin else [])

    async def _user() -> Any:
        return user

    app.dependency_overrides[get_db] = _no_db
    app.dependency_overrides[get_current_user] = _user
    if admin:
        app.dependency_overrides[get_admin_user] = _user  # 跳过 scope 检查

    async def _handle(_req: Request, exc: BusinessError) -> Any:
        from app.core.response import error

        return error(exc.code.value, "err")

    app.add_exception_handler(BusinessError, _handle)
    return app


@pytest.mark.asyncio
async def test_list_pending_returns_items(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch)

    async def _list(_db: Any) -> list[Any]:
        return [
            SimpleNamespace(
                id="f1",
                match_field="channel_id",
                match_value="UCx",
                channel_id="UCx",
                channel_handle=None,
                channel_name="Evil",
                block_count=3,
                last_video_id="v9",
                last_title="bad",
                status="pending",
                created_at=None,
                last_flagged_at=None,
            )
        ]

    monkeypatch.setattr(channel_flag_service, "list_pending", _list)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        body = (await client.get("/api/v1/admin/flagged-channels")).json()
    assert body["code"] == 0
    assert body["data"]["items"][0]["id"] == "f1"
    assert body["data"]["items"][0]["block_count"] == 3


@pytest.mark.asyncio
async def test_resolve_calls_service(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch)
    captured: dict[str, Any] = {}

    async def _resolve(_db: Any, *, flag_id: str, action: str, admin_id: str, note: Any = None) -> Any:
        captured.update(flag_id=flag_id, action=action, admin_id=admin_id)
        return (
            SimpleNamespace(
                id=flag_id,
                match_field="channel_id",
                match_value="UCx",
                channel_id="UCx",
                channel_handle=None,
                channel_name="Evil",
                block_count=3,
                last_video_id="v9",
                last_title="bad",
                status="blocked",
                created_at=None,
                last_flagged_at=None,
            ),
            True,
        )

    monkeypatch.setattr(channel_flag_service, "resolve", _resolve)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        body = (await client.post("/api/v1/admin/flagged-channels/f1/resolve", json={"action": "block"})).json()
    assert body["code"] == 0
    assert captured == {"flag_id": "f1", "action": "block", "admin_id": "admin-1"}


@pytest.mark.asyncio
async def test_resolve_propagates_already_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch)

    async def _resolve(_db: Any, **k: Any) -> Any:
        raise BusinessError(ErrorCode.FLAG_ALREADY_RESOLVED)

    monkeypatch.setattr(channel_flag_service, "resolve", _resolve)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        body = (await client.post("/api/v1/admin/flagged-channels/f1/resolve", json={"action": "dismiss"})).json()
    assert body["code"] == ErrorCode.FLAG_ALREADY_RESOLVED.value


@pytest.mark.asyncio
async def test_non_admin_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch, admin=False)  # 不覆盖 get_admin_user → 真 scope 检查
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        body = (await client.get("/api/v1/admin/flagged-channels")).json()
    assert body["code"] == ErrorCode.PERMISSION_DENIED.value


@pytest.mark.asyncio
async def test_batch_resolve_returns_per_item(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch)
    captured: dict[str, Any] = {}

    async def _batch(_db: Any, *, flag_ids, action, admin_id, note=None):
        captured.update(flag_ids=flag_ids, action=action, admin_id=admin_id, note=note)
        return [("a", "succeeded", None), ("b", "skipped", None), ("c", "failed", 40906)]

    monkeypatch.setattr(channel_flag_service, "batch_resolve", _batch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        body = (
            await client.post(
                "/api/v1/admin/flagged-channels/batch-resolve",
                json={"flag_ids": ["a", "b", "c"], "action": "block", "note": "批量"},
            )
        ).json()
    assert body["code"] == 0
    assert body["data"]["resolved_count"] == 2  # succeeded + skipped
    assert len(body["data"]["items"]) == 3
    assert body["data"]["items"][2] == {"flag_id": "c", "status": "failed", "code": 40906}
    assert captured == {"flag_ids": ["a", "b", "c"], "action": "block", "admin_id": "admin-1", "note": "批量"}


@pytest.mark.asyncio
async def test_batch_resolve_empty_ids_422(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.post(
            "/api/v1/admin/flagged-channels/batch-resolve", json={"flag_ids": [], "action": "block"}
        )
    assert resp.status_code == 422  # Field(min_length=1) 校验失败


@pytest.mark.asyncio
async def test_batch_resolve_non_admin_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch, admin=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        body = (
            await client.post(
                "/api/v1/admin/flagged-channels/batch-resolve", json={"flag_ids": ["a"], "action": "block"}
            )
        ).json()
    assert body["code"] == ErrorCode.PERMISSION_DENIED.value
