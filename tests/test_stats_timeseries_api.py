from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import FastAPI, Request
from httpx import ASGITransport

from app.api.deps import CurrentUser, get_current_user, get_db
from app.api.v1 import stats
from app.core.exceptions import BusinessError
from app.core.response import error

_USER = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _make_app(monkeypatch: Any, *, capture: dict[str, Any]) -> FastAPI:
    async def fake_timeseries(
        self: Any,
        time_range: Any = None,
        start_date: Any = None,
        end_date: Any = None,
        tz: Any = None,
    ) -> dict[str, Any]:
        capture["time_range"] = time_range
        capture["tz"] = tz
        return {
            "time_range": {"start": "2026-06-19T00:00:00Z", "end": "2026-06-22T00:00:00Z"},
            "timezone": tz or "Asia/Shanghai",
            "granularity": "day",
            "buckets": [
                {
                    "date": "2026-06-20",
                    "total": 1,
                    "completed": 1,
                    "failed": 0,
                    "processing": 0,
                    "pending": 0,
                    "audio_duration_seconds": 10.0,
                    "asr_cost": 0.5,
                }
            ],
        }

    monkeypatch.setattr(stats.StatsService, "get_task_timeseries", fake_timeseries)

    app = FastAPI()
    app.include_router(stats.router)

    @app.exception_handler(BusinessError)
    async def _handle(_req: Request, exc: BusinessError) -> Any:
        return error(int(exc.code), exc.code.name)

    async def _db() -> AsyncIterator[object]:
        yield object()

    app.dependency_overrides[get_db] = _db
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_timeseries_envelope_and_param_passthrough(monkeypatch: Any) -> None:
    capture: dict[str, Any] = {}
    app = _make_app(monkeypatch, capture=capture)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=_USER, email="u@e.com")
    async with _client(app) as client:
        body = (await client.get("/stats/tasks/timeseries?time_range=month&tz=Asia/Shanghai")).json()
    assert body["code"] == 0
    data = body["data"]
    assert data["granularity"] == "day"
    assert data["timezone"] == "Asia/Shanghai"
    assert data["buckets"][0]["date"] == "2026-06-20"
    # 参数透传到 service
    assert capture["time_range"] == "month"
    assert capture["tz"] == "Asia/Shanghai"


async def test_timeseries_requires_auth(monkeypatch: Any) -> None:
    capture: dict[str, Any] = {}
    app = _make_app(monkeypatch, capture=capture)  # 不覆盖 get_current_user → 真实鉴权依赖
    async with _client(app) as client:
        resp = await client.get("/stats/tasks/timeseries")
    assert resp.status_code in (401, 403) or resp.json().get("code") not in (0, None)
