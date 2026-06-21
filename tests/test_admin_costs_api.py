"""成本可见:管理员成本看板端点 GET /admin/costs/by-user 装配契约。

聚合/计价/spend 取数各有单测(test_cost_aggregator_*、test_litellm_spend_client),本组只钉
端点的「组合」语义:双币种分列且 ¥ 合计=ASR+配图、按 ¥ 降序、含管理员自己、LiteLLM 不可用时
LLM 列降级为 None 而 ¥ 列照常。用 monkeypatch 替掉三处取数源(不起真实 DB / 不打 LiteLLM)。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import FastAPI, Request
from httpx import ASGITransport

from app.api.deps import CurrentUser, get_admin_user, get_db
from app.api.v1 import admin_costs
from app.core.exceptions import BusinessError
from app.core.response import error

_ADMIN = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_USER = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


class _FakeSpendClient:
    def __init__(self, available: bool, spend: dict[str, float]) -> None:
        self._available = available
        self._spend = spend

    @property
    def available(self) -> bool:
        return self._available

    async def spend_by_end_user(self, user_ids: Any) -> dict[str, float]:
        if not self._available:
            return {}
        return {uid: self._spend.get(uid, 0.0) for uid in user_ids}


def _make_app(monkeypatch: Any, *, llm_available: bool) -> FastAPI:
    async def fake_asr(_db: Any, _start: Any = None, _end: Any = None) -> dict[str, dict[str, float | int]]:
        return {
            _ADMIN: {"estimated_cny": 3.0, "paid_cny": 1.0, "calls": 5},
            _USER: {"estimated_cny": 1.0, "paid_cny": 0.0, "calls": 2},
        }

    async def fake_images(_db: Any, _price_fn: Any, _start: Any = None, _end: Any = None) -> dict[str, float]:
        return {_ADMIN: 0.5, _USER: 0.25}

    async def fake_names(_db: Any, _ids: Any) -> dict[str, str | None]:
        return {_ADMIN: "管理员", _USER: "小明"}

    monkeypatch.setattr(admin_costs, "asr_cost_by_user", fake_asr)
    monkeypatch.setattr(admin_costs, "image_cost_by_user", fake_images)
    monkeypatch.setattr(admin_costs, "_display_names", fake_names)
    monkeypatch.setattr(
        admin_costs,
        "LiteLLMSpendClient",
        lambda: _FakeSpendClient(llm_available, {_ADMIN: 0.0064, _USER: 0.5}),
    )

    app = FastAPI()
    app.include_router(admin_costs.router)

    @app.exception_handler(BusinessError)
    async def _handle(_req: Request, exc: BusinessError) -> Any:
        return error(int(exc.code), exc.code.name)

    async def _db() -> AsyncIterator[object]:
        yield object()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_admin_user] = lambda: CurrentUser(id=_ADMIN, email="a@ex.com")
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_costs_compose_dual_currency_and_sort(monkeypatch: Any) -> None:
    async with _client(_make_app(monkeypatch, llm_available=True)) as client:
        body = (await client.get("/admin/costs/by-user")).json()
    assert body["code"] == 0
    data = body["data"]
    assert data["llm_source"] == "litellm"
    items = data["items"]
    # 按 ¥ 合计降序:admin(3.0+0.5=3.5) 在前,user(1.0+0.25=1.25) 在后
    assert [i["user_id"] for i in items] == [_ADMIN, _USER]
    # 「本人行」标记:发起请求的管理员自己的行 is_self=True,其余 False
    assert items[0]["is_self"] is True
    assert items[1]["is_self"] is False
    admin_row = items[0]
    assert admin_row["display_name"] == "管理员"
    assert admin_row["asr_cny"] == 3.0
    assert admin_row["asr_paid_cny"] == 1.0
    assert admin_row["asr_calls"] == 5
    assert admin_row["image_cny"] == 0.5
    # ¥ 合计只合并同币种(ASR + 配图),绝不混入 $
    assert admin_row["cny_total"] == 3.5
    # $ 单列
    assert admin_row["llm_usd"] == 0.0064


async def test_costs_llm_unavailable_degrades(monkeypatch: Any) -> None:
    async with _client(_make_app(monkeypatch, llm_available=False)) as client:
        body = (await client.get("/admin/costs/by-user")).json()
    data = body["data"]
    assert data["llm_source"] == "unavailable"
    # LLM 列降级为 None,¥ 两列照常
    for item in data["items"]:
        assert item["llm_usd"] is None
        assert item["cny_total"] == item["asr_cny"] + item["image_cny"]


async def test_costs_requires_admin(monkeypatch: Any) -> None:
    # 不覆盖 get_admin_user → 真实依赖触发(无 token → 鉴权失败),验证端点受管理员保护。
    app = _make_app(monkeypatch, llm_available=True)
    app.dependency_overrides.pop(get_admin_user, None)
    async with _client(app) as client:
        resp = await client.get("/admin/costs/by-user")
    assert resp.status_code in (401, 403) or resp.json().get("code") not in (0, None)
