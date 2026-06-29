"""版本头中间件:每个响应盖 X-App-Version = settings.GIT_SHA;装配 + CORS 暴露 + 配置默认。"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from app.config import Settings, settings
from app.core.middleware import VersionHeaderMiddleware


def _make_app() -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"ok": "1"}

    app.add_middleware(VersionHeaderMiddleware)
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_stamps_x_app_version_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GIT_SHA", "test-sha-123")
    async with _client(_make_app()) as client:
        resp = await client.get("/ping")
    assert resp.status_code == 200
    assert resp.headers["X-App-Version"] == "test-sha-123"


def test_config_git_sha_defaults_to_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    # CI 在镜像内跑,ENV GIT_SHA 已被烤成真 sha;删掉该 env 才能验证默认值。
    monkeypatch.delenv("GIT_SHA", raising=False)
    assert Settings().GIT_SHA == "dev"


def test_create_app_registers_version_header_middleware() -> None:
    from app.main import create_app

    app = create_app()
    assert any(m.cls is VersionHeaderMiddleware for m in app.user_middleware)


def test_create_app_cors_exposes_x_app_version() -> None:
    from fastapi.middleware.cors import CORSMiddleware

    from app.main import create_app

    app = create_app()
    cors = [m for m in app.user_middleware if m.cls is CORSMiddleware]
    assert len(cors) == 1
    assert "X-App-Version" in cors[0].kwargs["expose_headers"]
