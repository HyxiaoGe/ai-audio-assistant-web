"""rate_limit_by_ip(匿名公开端点按 IP 固定窗口限流)单元测试。"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import Depends, FastAPI, Request
from httpx import ASGITransport

import app.core.rate_limit as rate_limit_module
from app.core.exceptions import BusinessError
from app.core.response import error
from app.i18n.codes import ErrorCode


class _FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expirations: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, ttl: int) -> None:
        self.expirations[key] = ttl


def _make_app(limit: int) -> FastAPI:
    app = FastAPI()
    dep = rate_limit_module.rate_limit_by_ip(limit=limit, scope="probe")

    @app.get("/probe")
    async def probe(_rl: None = Depends(dep)) -> dict[str, int]:
        return {"ok": 1}

    @app.exception_handler(BusinessError)
    async def _handle(_req: Request, exc: BusinessError) -> Any:
        return error(int(exc.code), exc.code.name)

    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_blocks_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit_module, "get_redis_client", lambda: fake)
    async with _client(_make_app(limit=2)) as client:
        assert (await client.get("/probe")).json()["ok"] == 1
        assert (await client.get("/probe")).json()["ok"] == 1
        third = (await client.get("/probe")).json()
        assert third["code"] == int(ErrorCode.RATE_LIMIT_EXCEEDED)


async def test_buckets_by_forwarded_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit_module, "get_redis_client", lambda: fake)
    async with _client(_make_app(limit=1)) as client:
        ok_a = (await client.get("/probe", headers={"x-forwarded-for": "1.1.1.1"})).json()
        ok_b = (await client.get("/probe", headers={"x-forwarded-for": "2.2.2.2, 10.0.0.1"})).json()
        assert ok_a["ok"] == 1 and ok_b["ok"] == 1  # 不同 IP 各自独立窗口
        blocked = (await client.get("/probe", headers={"x-forwarded-for": "1.1.1.1"})).json()
        assert blocked["code"] == int(ErrorCode.RATE_LIMIT_EXCEEDED)
    assert any(":ip:1.1.1.1:" in k for k in fake.counts)
    assert any(":ip:2.2.2.2:" in k for k in fake.counts)  # XFF 只取第一跳


async def test_fail_open_on_redis_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> Any:
        raise ConnectionError("redis down")

    monkeypatch.setattr(rate_limit_module, "get_redis_client", _boom)
    async with _client(_make_app(limit=1)) as client:
        for _ in range(3):
            assert (await client.get("/probe")).json()["ok"] == 1
