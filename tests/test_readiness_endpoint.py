"""P0-4:/readiness 真探活端点(Postgres/Redis/Celery 任一不可达 → 503)。

/health 是静态 stub(进程活即绿),部署门 curl 它形同虚设。新增 /readiness 探三依赖,
部署门改指它才是真 smoke test。本测试钉:聚合 200/503 逻辑 + 每个探针真实行为 +
/health 仍 liveness 不变。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from app.api.deps import get_db
from app.api.v1 import health as health_module


class _OkSession:
    async def execute(self, _stmt: Any) -> None:
        return None


class _BadSession:
    async def execute(self, _stmt: Any) -> None:
        raise RuntimeError("db down")


def _make_app(session: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(health_module.router)

    async def _db() -> AsyncIterator[Any]:
        yield session

    app.dependency_overrides[get_db] = _db
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _patch_checks(
    monkeypatch: pytest.MonkeyPatch, *, postgres: bool = True, redis: bool = True, celery: bool = True
) -> None:
    async def _pg(_db: Any) -> bool:
        return postgres

    async def _redis() -> bool:
        return redis

    async def _celery() -> bool:
        return celery

    monkeypatch.setattr(health_module, "_check_postgres", _pg)
    monkeypatch.setattr(health_module, "_check_redis", _redis)
    monkeypatch.setattr(health_module, "_check_celery", _celery)


# ---- 路由聚合(patch 三探针,只验 200/503 + checks 明细) ----


async def test_readiness_all_healthy_returns_200(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_checks(monkeypatch)
    async with _client(_make_app(_OkSession())) as client:
        resp = await client.get("/readiness")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"postgres": True, "redis": True, "celery": True}


@pytest.mark.parametrize("down", ["postgres", "redis", "celery"])
async def test_readiness_any_dep_down_returns_503(monkeypatch: pytest.MonkeyPatch, down: str) -> None:
    _patch_checks(monkeypatch, **{down: False})
    async with _client(_make_app(_OkSession())) as client:
        resp = await client.get("/readiness")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["checks"][down] is False


# ---- /health 保持 liveness-only(永远 200) ----


async def test_health_still_liveness_200() -> None:
    async with _client(_make_app(_OkSession())) as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "ok"


# ---- 探针真实行为 ----


async def test_check_postgres_ok_and_fail() -> None:
    assert await health_module._check_postgres(_OkSession()) is True
    assert await health_module._check_postgres(_BadSession()) is False


async def test_check_redis_ok_and_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    class _OkRedis:
        async def ping(self) -> bool:
            return True

    class _BadRedis:
        async def ping(self) -> bool:
            raise RuntimeError("redis down")

    monkeypatch.setattr(health_module, "get_redis_client", lambda: _OkRedis())
    assert await health_module._check_redis() is True
    monkeypatch.setattr(health_module, "get_redis_client", lambda: _BadRedis())
    assert await health_module._check_redis() is False


async def test_check_celery_short_timeout_and_maps_replies(monkeypatch: pytest.MonkeyPatch) -> None:
    from worker.celery_app import celery_app

    captured: dict[str, Any] = {}

    def _ping_no_worker(timeout: Any = None) -> list[Any]:
        captured["timeout"] = timeout
        return []  # 无 worker

    monkeypatch.setattr(celery_app.control, "ping", _ping_no_worker)
    assert await health_module._check_celery() is False
    assert captured["timeout"] == 1  # 短超时,无 worker 不卡住部署门

    monkeypatch.setattr(celery_app.control, "ping", lambda timeout=None: [{"w1": {"ok": "pong"}}])
    assert await health_module._check_celery() is True
