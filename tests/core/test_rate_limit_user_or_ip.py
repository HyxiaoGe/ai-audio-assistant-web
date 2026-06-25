from typing import Any

import httpx
import pytest
from fastapi import Depends, FastAPI, Request
from httpx import ASGITransport

from app.api.deps import CurrentUser
from app.core import rate_limit as rate_limit_module
from app.core.exceptions import BusinessError
from app.core.response import error
from app.i18n.codes import ErrorCode


class _FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, ttl: int) -> None:
        return None


def _make_app(viewer: CurrentUser | None) -> FastAPI:
    app = FastAPI()
    dep = rate_limit_module.rate_limit_user_or_ip(user_limit=5, ip_limit=2, scope="probe")

    async def _fake_viewer() -> CurrentUser | None:
        return viewer

    app.dependency_overrides[rate_limit_module.get_public_viewer] = _fake_viewer

    @app.get("/probe")
    async def probe(_rl: None = Depends(dep)) -> dict[str, int]:
        return {"ok": 1}

    @app.exception_handler(BusinessError)
    async def _handle(_req: Request, exc: BusinessError) -> Any:
        return error(int(exc.code), exc.code.name)

    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_anonymous_keyed_by_ip_uses_ip_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit_module, "get_redis_client", lambda: fake)
    async with _client(_make_app(viewer=None)) as client:
        assert (await client.get("/probe")).json()["ok"] == 1
        assert (await client.get("/probe")).json()["ok"] == 1
        third = (await client.get("/probe")).json()
        assert third["code"] == int(ErrorCode.RATE_LIMIT_EXCEEDED)  # ip_limit=2 第三次超限


async def test_logged_in_keyed_by_user_uses_user_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit_module, "get_redis_client", lambda: fake)
    viewer = CurrentUser(id="user-1", email="")  # CurrentUser 是 dataclass(id/email/scopes,scopes 有默认)
    async with _client(_make_app(viewer=viewer)) as client:
        for _ in range(5):
            assert (await client.get("/probe")).json()["ok"] == 1  # user_limit=5 内全通过
        sixth = (await client.get("/probe")).json()
        assert sixth["code"] == int(ErrorCode.RATE_LIMIT_EXCEEDED)
