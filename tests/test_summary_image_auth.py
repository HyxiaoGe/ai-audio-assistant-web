"""鉴权测试：GET /api/v1/summaries/images/{path} 现在要求 token + 归属校验。

不依赖真实 MinIO / DB / 网络：用 fake DB 覆盖 + monkeypatch minio.Minio。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from app.api.deps import CurrentUser, get_db, get_media_user
from app.api.v1 import summaries as summaries_module
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode


class _FakeMinioResponse:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def close(self) -> None:
        pass

    def release_conn(self) -> None:
        pass


async def _fake_db() -> AsyncIterator[None]:
    yield None


def _build_app(
    monkeypatch: pytest.MonkeyPatch, user_id: str | None, calls: dict[str, int]
) -> FastAPI:
    class _FakeMinio:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def get_object(self, bucket: str, key: str) -> _FakeMinioResponse:  # noqa: ARG002
            calls["get_object"] += 1
            return _FakeMinioResponse(b"PNGDATA")

    monkeypatch.setattr("minio.Minio", _FakeMinio)

    app = FastAPI()
    app.include_router(summaries_module.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = _fake_db
    if user_id is not None:
        app.dependency_overrides[get_media_user] = lambda: CurrentUser(
            id=user_id, email=f"{user_id}@example.com"
        )

    @app.exception_handler(BusinessError)
    async def _handler(_request: Any, exc: BusinessError) -> Any:
        from fastapi.responses import JSONResponse

        status_map = {
            ErrorCode.AUTH_TOKEN_NOT_PROVIDED: 401,
            ErrorCode.RESOURCE_NOT_FOUND: 404,
            ErrorCode.FILE_STORAGE_SERVICE_ERROR: 502,
        }
        return JSONResponse({"code": int(exc.code)}, status_code=status_map.get(exc.code, 500))

    return app


@pytest.mark.asyncio
async def test_image_unauthenticated_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"get_object": 0}
    app = _build_app(monkeypatch, user_id=None, calls=calls)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/summaries/images/u1/task/img.png")

    assert resp.status_code == 401
    assert resp.json()["code"] == int(ErrorCode.AUTH_TOKEN_NOT_PROVIDED)
    assert calls["get_object"] == 0


@pytest.mark.asyncio
async def test_image_owner_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"get_object": 0}
    app = _build_app(monkeypatch, user_id="u1", calls=calls)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/summaries/images/u1/task/img.png")

    assert resp.status_code == 200
    assert resp.content == b"PNGDATA"
    assert calls["get_object"] == 1


@pytest.mark.asyncio
async def test_image_cross_tenant_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"get_object": 0}
    app = _build_app(monkeypatch, user_id="u1", calls=calls)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/summaries/images/u2/task/img.png")

    assert resp.status_code == 404
    assert resp.json()["code"] == int(ErrorCode.RESOURCE_NOT_FOUND)
    assert calls["get_object"] == 0
