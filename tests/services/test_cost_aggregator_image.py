"""成本可见 PR-4:配图按用户聚合(¥)。

远端 image-service 只回 image_url、不回成本(estimate_cost 恒 0),且其 LiteLLM key 混了
所有消费方、无 end-user 归因 → 配图成本只能 app 侧按「ready 图片数 × 每模型价」估算。
app 完全知道每用户配了几张图(Summary.images[].model_id / image_model_used,经 Summary→Task→user)。

本组钉住:① 计价 price_for_image_model(每模型价,缺省回落默认价);② 计数只算 ready 项
(pending/failed 从未产出真实图,不计费);③ legacy 单图(image_key)计 1 张;④ 脏数据安全跳过。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services.cost import aggregator
from app.services.cost.pricing import price_for_image_model


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self.rows = rows or []

    async def execute(self, stmt: Any) -> _FakeResult:
        return _FakeResult(self.rows)


def _row(user_id: str, images=None, image_key=None, image_model=None) -> SimpleNamespace:
    return SimpleNamespace(user_id=user_id, images=images, image_key=image_key, image_model_used=image_model)


def _ready(model: str | None = None) -> dict:
    return {"status": "ready", "model_id": model}


def test_price_for_image_model_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.cost import pricing as pricing_module

    monkeypatch.setattr(pricing_module.settings, "IMAGE_COST_CNY_DEFAULT", 0.25)
    monkeypatch.setattr(pricing_module.settings, "IMAGE_COST_CNY_BY_MODEL", {"seedream-4.5": 0.3})
    assert price_for_image_model("seedream-4.5") == 0.3
    assert price_for_image_model("unknown-model") == 0.25
    assert price_for_image_model(None) == 0.25


async def test_image_cost_counts_only_ready() -> None:
    session = _FakeSession(
        rows=[
            _row("u-1", images=[_ready(), _ready(), {"status": "pending", "model_id": None}]),
        ]
    )
    out = await aggregator.image_cost_by_user(session, price_fn=lambda m: 0.25)
    assert out["u-1"] == pytest.approx(0.5)  # 2 ready × 0.25;pending 不计


async def test_image_cost_per_model_price() -> None:
    session = _FakeSession(rows=[_row("u-2", images=[_ready("cheap"), _ready("pricey")])])
    prices = {"cheap": 0.1, "pricey": 1.0}
    out = await aggregator.image_cost_by_user(session, price_fn=lambda m: prices.get(m, 0.0))
    assert out["u-2"] == pytest.approx(1.1)


async def test_image_cost_band_model_falls_back_to_summary_model() -> None:
    # band 内某张未带 model_id → 回落 summary 的 image_model_used。
    session = _FakeSession(rows=[_row("u-3", images=[_ready(None)], image_model="seedream-4.5")])
    seen: list[str | None] = []

    def price_fn(m: str | None) -> float:
        seen.append(m)
        return 0.25

    out = await aggregator.image_cost_by_user(session, price_fn=price_fn)
    assert out["u-3"] == pytest.approx(0.25)
    assert seen == ["seedream-4.5"]


async def test_image_cost_legacy_single_image() -> None:
    # 无 band,仅 legacy image_key → 计 1 张,用 image_model_used 计价。
    session = _FakeSession(rows=[_row("u-4", images=None, image_key="img/abc.webp", image_model="seedream-4.5")])
    out = await aggregator.image_cost_by_user(session, price_fn=lambda m: 0.25)
    assert out["u-4"] == pytest.approx(0.25)


async def test_image_cost_skips_dirty_payload() -> None:
    # images 不是 list / 项不是 dict → 安全跳过,不崩。
    session = _FakeSession(rows=[_row("u-5", images="garbage"), _row("u-6", images=[123, "x"])])
    out = await aggregator.image_cost_by_user(session, price_fn=lambda m: 0.25)
    assert out == {}
