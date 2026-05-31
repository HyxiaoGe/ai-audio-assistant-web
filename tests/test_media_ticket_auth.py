"""鉴权测试：媒体/SSE 端点改用「短期作用域票据」(scoped ticket)。

覆盖三层：
  1. get_media_user / get_stream_user 依赖本身（用最小探针路由隔离测试）；
  2. 签票端点（/media/ticket、/summaries/{task_id}/stream-ticket）；
  3. 已接线的真实端点（图片/媒体流）仍正确执行归属校验。

不依赖真实 MinIO / DB / 网络：fake DB 覆盖 + monkeypatch。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport

from app.api.deps import (
    CurrentUser,
    get_current_user,
    get_db,
    get_media_user,
    get_stream_user,
)
from app.api.v1 import media as media_module
from app.api.v1 import summaries as summaries_module
from app.config import settings
from app.core.exceptions import BusinessError
from app.core.security import issue_scoped_token, verify_scoped_token
from app.i18n.codes import ErrorCode

_TEST_SECRET = "integration-test-secret-please-ignore-0123456789"


@pytest.fixture(autouse=True)
def _force_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "JWT_SECRET", _TEST_SECRET)
    monkeypatch.setattr(settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(settings, "MEDIA_TOKEN_TTL", 300, raising=False)


async def _fake_db() -> AsyncIterator[None]:
    yield None


def _register_error_handler(app: FastAPI) -> None:
    @app.exception_handler(BusinessError)
    async def _handler(_request: Any, exc: BusinessError) -> Any:
        from fastapi.responses import JSONResponse

        status_map = {
            ErrorCode.AUTH_TOKEN_NOT_PROVIDED: 401,
            ErrorCode.AUTH_TOKEN_INVALID: 401,
            ErrorCode.AUTH_TOKEN_EXPIRED: 401,
            ErrorCode.PERMISSION_DENIED: 403,
            ErrorCode.RESOURCE_NOT_FOUND: 404,
            ErrorCode.TASK_NOT_FOUND: 404,
            ErrorCode.FILE_STORAGE_SERVICE_ERROR: 502,
        }
        return JSONResponse({"code": int(exc.code)}, status_code=status_map.get(exc.code, 500))


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --------------------------------------------------------------------------- #
# 1. get_media_user 依赖（最小探针路由）
# --------------------------------------------------------------------------- #


def _media_probe_app() -> FastAPI:
    app = FastAPI()

    @app.get("/probe-media")
    async def _probe(user: CurrentUser = Depends(get_media_user)) -> dict[str, str]:
        return {"id": user.id}

    app.dependency_overrides[get_db] = _fake_db
    _register_error_handler(app)
    return app


@pytest.mark.asyncio
async def test_media_ticket_resolves_user() -> None:
    token = issue_scoped_token(sub="u1", scope="media", ttl=300)
    async with _client(_media_probe_app()) as client:
        resp = await client.get("/probe-media", params={"token": token})
    assert resp.status_code == 200
    assert resp.json()["id"] == "u1"


@pytest.mark.asyncio
async def test_media_endpoint_rejects_stream_scoped_ticket() -> None:
    token = issue_scoped_token(sub="u1", scope="stream", ttl=300)
    async with _client(_media_probe_app()) as client:
        resp = await client.get("/probe-media", params={"token": token})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_media_endpoint_requires_token() -> None:
    async with _client(_media_probe_app()) as client:
        resp = await client.get("/probe-media")
    assert resp.status_code == 401
    assert resp.json()["code"] == int(ErrorCode.AUTH_TOKEN_NOT_PROVIDED)


@pytest.mark.asyncio
async def test_media_dual_accept_falls_back_to_legacy_jwt(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_resolve(_db: Any, _token: str) -> CurrentUser:
        return CurrentUser(id="legacy-user", email="legacy@example.com")

    monkeypatch.setattr("app.api.deps._resolve_user", _fake_resolve)
    async with _client(_media_probe_app()) as client:
        resp = await client.get("/probe-media", params={"token": "a-legacy-rs256-jwt"})
    assert resp.status_code == 200
    assert resp.json()["id"] == "legacy-user"


# --------------------------------------------------------------------------- #
# 2. get_stream_user 依赖（资源绑定：task_id + summary_type）
# --------------------------------------------------------------------------- #


def _stream_probe_app() -> FastAPI:
    app = FastAPI()

    @app.get("/probe-stream/{task_id}/stream")
    async def _probe(task_id: str, user: CurrentUser = Depends(get_stream_user)) -> dict[str, str]:
        return {"id": user.id}

    app.dependency_overrides[get_db] = _fake_db
    _register_error_handler(app)
    return app


def _stream_ticket(sub: str, task_id: str, summary_type: str) -> str:
    return issue_scoped_token(
        sub=sub,
        scope="stream",
        ttl=300,
        resource={"task_id": task_id, "summary_type": summary_type},
    )


@pytest.mark.asyncio
async def test_stream_ticket_matching_resource_ok() -> None:
    token = _stream_ticket("u1", "t1", "overview")
    async with _client(_stream_probe_app()) as client:
        resp = await client.get("/probe-stream/t1/stream", params={"token": token, "summary_type": "overview"})
    assert resp.status_code == 200
    assert resp.json()["id"] == "u1"


@pytest.mark.asyncio
async def test_stream_ticket_wrong_summary_type_rejected() -> None:
    token = _stream_ticket("u1", "t1", "overview")
    async with _client(_stream_probe_app()) as client:
        resp = await client.get("/probe-stream/t1/stream", params={"token": token, "summary_type": "keypoints"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_stream_ticket_wrong_task_rejected() -> None:
    token = _stream_ticket("u1", "t1", "overview")
    async with _client(_stream_probe_app()) as client:
        resp = await client.get("/probe-stream/t2/stream", params={"token": token, "summary_type": "overview"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_stream_endpoint_rejects_media_scoped_ticket() -> None:
    token = issue_scoped_token(sub="u1", scope="media", ttl=300)
    async with _client(_stream_probe_app()) as client:
        resp = await client.get("/probe-stream/t1/stream", params={"token": token, "summary_type": "overview"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_stream_endpoint_requires_token() -> None:
    async with _client(_stream_probe_app()) as client:
        resp = await client.get("/probe-stream/t1/stream", params={"summary_type": "overview"})
    assert resp.status_code == 401
    assert resp.json()["code"] == int(ErrorCode.AUTH_TOKEN_NOT_PROVIDED)


@pytest.mark.asyncio
async def test_stream_dual_accept_falls_back_to_legacy_jwt(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_resolve(_db: Any, _token: str) -> CurrentUser:
        return CurrentUser(id="legacy-user", email="legacy@example.com")

    monkeypatch.setattr("app.api.deps._resolve_user", _fake_resolve)
    async with _client(_stream_probe_app()) as client:
        resp = await client.get(
            "/probe-stream/t1/stream", params={"token": "a-legacy-rs256-jwt", "summary_type": "overview"}
        )
    assert resp.status_code == 200
    assert resp.json()["id"] == "legacy-user"


# --------------------------------------------------------------------------- #
# 3. 签票端点
# --------------------------------------------------------------------------- #


class _FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _FakeDB:
    def __init__(self, task: Any) -> None:
        self._task = task

    async def execute(self, *_args: Any, **_kwargs: Any) -> _FakeResult:
        return _FakeResult(self._task)


def _media_mint_app(user_id: str) -> FastAPI:
    app = FastAPI()
    app.include_router(media_module.router, prefix="/media")
    app.dependency_overrides[get_db] = _fake_db
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=user_id, email=f"{user_id}@ex.com")
    _register_error_handler(app)
    return app


@pytest.mark.asyncio
async def test_media_mint_returns_usable_ticket() -> None:
    async with _client(_media_mint_app("u1")) as client:
        resp = await client.post("/media/ticket")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["expires_in"] == 300
    claims = verify_scoped_token(data["token"])
    assert claims["sub"] == "u1"
    assert claims["scope"] == "media"


def _stream_mint_app(user_id: str, task: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(summaries_module.router, prefix="/api/v1")

    async def _db() -> AsyncIterator[Any]:
        yield _FakeDB(task)

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=user_id, email=f"{user_id}@ex.com")
    _register_error_handler(app)
    return app


@pytest.mark.asyncio
async def test_stream_mint_returns_ticket_bound_to_resource() -> None:
    app = _stream_mint_app("u1", task=object())
    async with _client(app) as client:
        resp = await client.post("/api/v1/summaries/t1/stream-ticket", params={"summary_type": "overview"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    claims = verify_scoped_token(data["token"])
    assert claims["scope"] == "stream"
    assert claims["sub"] == "u1"
    assert claims["resource"] == {"task_id": "t1", "summary_type": "overview"}


@pytest.mark.asyncio
async def test_stream_mint_rejects_non_owned_task() -> None:
    app = _stream_mint_app("u1", task=None)  # DB returns no owned task
    async with _client(app) as client:
        resp = await client.post("/api/v1/summaries/t1/stream-ticket", params={"summary_type": "overview"})
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# 4. 真实端点接线：媒体票据通过后仍执行归属校验
# --------------------------------------------------------------------------- #


def _media_stream_app() -> FastAPI:
    app = FastAPI()
    app.include_router(media_module.router, prefix="/media")
    app.dependency_overrides[get_db] = _fake_db
    _register_error_handler(app)
    return app


@pytest.mark.asyncio
async def test_media_stream_cross_tenant_denied_with_valid_ticket() -> None:
    # 票据归属 u1，却请求 u2 的对象：鉴权应通过、归属校验应 404（证明依赖确实解析了用户）。
    token = issue_scoped_token(sub="u1", scope="media", ttl=300)
    async with _client(_media_stream_app()) as client:
        resp = await client.get("/media/upload/u2/file.mp3", params={"token": token})
    assert resp.status_code == 404
    assert resp.json()["code"] == int(ErrorCode.RESOURCE_NOT_FOUND)


def _images_app(monkeypatch: pytest.MonkeyPatch, calls: dict[str, int]) -> FastAPI:
    class _FakeMinioResponse:
        def read(self) -> bytes:
            return b"PNGDATA"

        def close(self) -> None:
            pass

        def release_conn(self) -> None:
            pass

    class _FakeMinio:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def get_object(self, _bucket: str, _key: str) -> _FakeMinioResponse:
            calls["get_object"] += 1
            return _FakeMinioResponse()

    monkeypatch.setattr("minio.Minio", _FakeMinio)

    app = FastAPI()
    app.include_router(summaries_module.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = _fake_db
    _register_error_handler(app)
    return app


@pytest.mark.asyncio
async def test_image_owner_ok_with_media_ticket(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"get_object": 0}
    token = issue_scoped_token(sub="u1", scope="media", ttl=300)
    async with _client(_images_app(monkeypatch, calls)) as client:
        resp = await client.get("/api/v1/summaries/images/u1/task/img.png", params={"token": token})
    assert resp.status_code == 200
    assert resp.content == b"PNGDATA"
    assert calls["get_object"] == 1


@pytest.mark.asyncio
async def test_image_cross_tenant_denied_with_media_ticket(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"get_object": 0}
    token = issue_scoped_token(sub="u1", scope="media", ttl=300)
    async with _client(_images_app(monkeypatch, calls)) as client:
        resp = await client.get("/api/v1/summaries/images/u2/task/img.png", params={"token": token})
    assert resp.status_code == 404
    assert calls["get_object"] == 0
