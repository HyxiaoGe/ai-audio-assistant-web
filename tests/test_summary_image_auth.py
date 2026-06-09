"""鉴权测试：GET /api/v1/summaries/images/{path} 要求 token + 归属校验。

统一存储后图片经 media.serve_media_object（allow_redirect=False，服务端代理统一存储 OSS）
返回，不再直连 minio.Minio。这里 mock SmartFactory storage + httpx 传输，验证「鉴权门在触达
存储前短路」以及 owner 正常取图。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from app.api.deps import CurrentUser, get_db, get_media_user
from app.api.v1 import media as media_module
from app.api.v1 import summaries as summaries_module
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode


class _FakeStorage:
    """SmartFactory.get_service("storage") 的最小存根：只签 URL，不触网。"""

    def generate_presigned_url(self, object_name: str, expires_in: int) -> str:  # noqa: ARG002
        return f"https://oss.example/bucket/{object_name}?sig=fake"


async def _fake_db() -> AsyncIterator[None]:
    yield None


def _build_app(
    monkeypatch: pytest.MonkeyPatch,
    user_id: str | None,
    calls: dict[str, int],
    upstream_cache_control: str | None = None,
) -> FastAPI:
    async def fake_get_service(service_type: str, **kwargs: Any) -> _FakeStorage:  # noqa: ARG001
        calls["storage"] += 1
        return _FakeStorage()

    # 代理逻辑（serve_media_object → _proxy_media）的依赖都挂在 media_module 上
    monkeypatch.setattr(media_module.SmartFactory, "get_service", fake_get_service)

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["upstream"] += 1
        headers = {"Content-Type": "image/png", "Content-Length": "7"}
        if upstream_cache_control is not None:
            headers["Cache-Control"] = upstream_cache_control
        return httpx.Response(200, content=b"PNGDATA", headers=headers)

    transport = httpx.MockTransport(handler)
    original_init = httpx.AsyncClient.__init__

    def patched_init(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> None:
        if "transport" not in kwargs:  # 不覆盖测试客户端的 ASGITransport
            kwargs["transport"] = transport
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    app = FastAPI()
    app.include_router(summaries_module.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = _fake_db
    if user_id is not None:
        app.dependency_overrides[get_media_user] = lambda: CurrentUser(id=user_id, email=f"{user_id}@example.com")

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
    calls = {"storage": 0, "upstream": 0}
    app = _build_app(monkeypatch, user_id=None, calls=calls)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/summaries/images/u1/task/img.png")

    assert resp.status_code == 401
    assert resp.json()["code"] == int(ErrorCode.AUTH_TOKEN_NOT_PROVIDED)
    assert calls["storage"] == 0  # 鉴权门在触达存储前短路


@pytest.mark.asyncio
async def test_image_owner_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"storage": 0, "upstream": 0}
    app = _build_app(monkeypatch, user_id="u1", calls=calls)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/summaries/images/u1/task/img.png")

    assert resp.status_code == 200
    assert resp.content == b"PNGDATA"
    assert resp.headers["content-type"] == "image/png"
    # 图片走服务端代理（allow_redirect=False）以保留同源长缓存
    assert resp.headers.get("cache-control") == "private, max-age=2592000, immutable"
    assert calls["storage"] >= 1
    assert calls["upstream"] == 1


@pytest.mark.asyncio
async def test_image_cache_control_forced_over_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    """回归：_proxy_media 会转发上游 Cache-Control，端点必须强制覆盖为 private+immutable，
    不能被上游（OSS bucket 默认策略 / 迁移元数据）回传的 public 缓存头污染——否则带 media token
    的私有图会带可共享缓存头穿过 CF/共享代理。"""
    calls = {"storage": 0, "upstream": 0}
    app = _build_app(monkeypatch, user_id="u1", calls=calls, upstream_cache_control="public, max-age=10")
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/summaries/images/u1/task/img.png")

    assert resp.status_code == 200
    assert resp.content == b"PNGDATA"
    # 上游回 public,max-age=10，但端点必须单点掌控、强制覆盖
    assert resp.headers.get("cache-control") == "private, max-age=2592000, immutable"


@pytest.mark.asyncio
async def test_image_cross_tenant_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"storage": 0, "upstream": 0}
    app = _build_app(monkeypatch, user_id="u1", calls=calls)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/summaries/images/u2/task/img.png")

    assert resp.status_code == 404
    assert resp.json()["code"] == int(ErrorCode.RESOURCE_NOT_FOUND)
    assert calls["storage"] == 0  # 归属校验在触达存储前短路
