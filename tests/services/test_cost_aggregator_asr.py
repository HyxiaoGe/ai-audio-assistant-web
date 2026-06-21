"""成本可见 PR-2:ASR 按用户聚合(¥)。

ASR 走厂商直连 SDK(非 LiteLLM),故 app 侧 ASRUsage 账本(user_id FK + estimated_cost +
actual_paid_cost)就是 ASR 成本的权威来源。此前只有「按 provider、自筛当前用户」的汇总
端点(前端无入口=死代码),管理员无法看到「每个用户各花了多少 ASR」。本组钉住按 user_id
聚合的语句形状与映射:GROUP BY user_id、求和 estimated/paid、可选时间窗。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from sqlalchemy.dialects import postgresql

from app.services.cost import aggregator


def _csql(stmt: Any) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).lower()


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self.rows = rows or []
        self.last_sql: str | None = None

    async def execute(self, stmt: Any) -> _FakeResult:
        self.last_sql = _csql(stmt)
        return _FakeResult(self.rows)


def test_asr_statement_groups_by_user_and_sums_cost() -> None:
    sql = _csql(aggregator.build_asr_cost_by_user_statement())
    assert "group by" in sql
    assert "asr_usages.user_id" in sql
    assert "sum(asr_usages.estimated_cost)" in sql
    assert "sum(asr_usages.actual_paid_cost)" in sql


def test_asr_statement_applies_time_window() -> None:
    from datetime import datetime

    sql = _csql(
        aggregator.build_asr_cost_by_user_statement(
            start=datetime(2026, 6, 1),
            end=datetime(2026, 6, 30),
        )
    )
    assert "asr_usages.created_at >=" in sql
    assert "asr_usages.created_at <=" in sql


async def test_asr_cost_by_user_maps_rows() -> None:
    session = _FakeSession(
        rows=[
            SimpleNamespace(user_id="u-1", estimated_cny=1.5, paid_cny=0.5, call_count=3),
            SimpleNamespace(user_id="u-2", estimated_cny=2.0, paid_cny=2.0, call_count=1),
        ]
    )
    out = await aggregator.asr_cost_by_user(session)
    assert out["u-1"]["estimated_cny"] == 1.5
    assert out["u-1"]["paid_cny"] == 0.5
    assert out["u-1"]["calls"] == 3
    assert out["u-2"]["estimated_cny"] == 2.0


async def test_asr_cost_by_user_coerces_none_to_zero() -> None:
    session = _FakeSession(rows=[SimpleNamespace(user_id="u-3", estimated_cny=None, paid_cny=None, call_count=0)])
    out = await aggregator.asr_cost_by_user(session)
    assert out["u-3"]["estimated_cny"] == 0.0
    assert out["u-3"]["paid_cny"] == 0.0
