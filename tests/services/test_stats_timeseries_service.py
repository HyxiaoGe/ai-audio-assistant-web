from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from app.api.deps import CurrentUser
from app.core.exceptions import BusinessError
from app.services.stats_service import (
    StatsService,
    _build_asr_daily_cost_stmt,
    _build_task_daily_stmt,
)


def _csql(stmt: Any) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).lower()


_START = datetime(2026, 6, 19, 0, 0, tzinfo=UTC)
_END = datetime(2026, 6, 22, 0, 0, tzinfo=UTC)


def test_task_stmt_shape() -> None:
    sql = _csql(_build_task_daily_stmt("u-1", _START, _END, "Asia/Shanghai"))
    assert "date_trunc" in sql
    assert "timezone(" in sql
    assert "group by" in sql
    assert "count(" in sql
    assert "sum(tasks.duration_seconds)" in sql
    assert "tasks.user_id" in sql
    assert "tasks.deleted_at is null" in sql
    assert "tasks.created_at >=" in sql
    assert "tasks.created_at <=" in sql


def test_asr_stmt_shape() -> None:
    sql = _csql(_build_asr_daily_cost_stmt("u-1", _START, _END, "Asia/Shanghai"))
    assert "date_trunc" in sql
    assert "timezone(" in sql
    assert "sum(asr_usages.estimated_cost)" in sql
    assert "asr_usages.user_id" in sql
    assert "asr_usages.status =" in sql
    assert "asr_usages.created_at >=" in sql


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _QueueSession:
    """每次 execute() 依次弹出预置结果集（方法会先查 task、再查 asr）。"""

    def __init__(self, result_sets: list[list[Any]]) -> None:
        self._queue = list(result_sets)

    async def execute(self, _stmt: Any) -> _Result:
        return _Result(self._queue.pop(0) if self._queue else [])


def _svc(result_sets: list[list[Any]]) -> StatsService:
    return StatsService(_QueueSession(result_sets), CurrentUser(id="u-1", email="x@e.com"))


async def test_aggregates_folds_and_zero_fills() -> None:
    # 本地日轴(Asia/Shanghai)覆盖 6/19..6/22。task 行在 6/20、6/22;6/19、6/21 应补零。
    task_rows = [
        SimpleNamespace(day=datetime(2026, 6, 20), status="completed", count=3, duration=120.0),
        SimpleNamespace(day=datetime(2026, 6, 20), status="failed", count=1, duration=0.0),
        SimpleNamespace(day=datetime(2026, 6, 20), status="transcribing", count=1, duration=30.0),
        SimpleNamespace(day=datetime(2026, 6, 22), status="completed", count=2, duration=None),
    ]
    asr_rows = [
        SimpleNamespace(day=datetime(2026, 6, 20), cost=1.5),
    ]
    svc = _svc([task_rows, asr_rows])
    out = await svc.get_task_timeseries(start_date=_START, end_date=_END, tz="Asia/Shanghai")

    assert out["timezone"] == "Asia/Shanghai"
    assert out["granularity"] == "day"
    buckets = out["buckets"]
    dates = [b["date"] for b in buckets]
    assert dates == ["2026-06-19", "2026-06-20", "2026-06-21", "2026-06-22"]

    b20 = buckets[1]
    assert b20["completed"] == 3
    assert b20["failed"] == 1
    assert b20["processing"] == 1  # transcribing 折叠进 processing
    assert b20["pending"] == 0
    assert b20["total"] == 5  # 不变量 total == 四桶之和
    assert b20["audio_duration_seconds"] == 150.0
    assert b20["asr_cost"] == 1.5

    b19, b21 = buckets[0], buckets[2]
    for empty in (b19, b21):
        assert empty["total"] == 0
        assert empty["audio_duration_seconds"] == 0.0
        assert empty["asr_cost"] == 0.0

    b22 = buckets[3]
    assert b22["completed"] == 2
    assert b22["total"] == 2
    assert b22["audio_duration_seconds"] == 0.0  # duration None → 0
    assert b22["asr_cost"] == 0.0  # 该日无 ASRUsage


async def test_range_guard_rejects_over_366_days() -> None:
    far_start = datetime(2024, 1, 1, tzinfo=UTC)
    far_end = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(BusinessError):
        await _svc([]).get_task_timeseries(start_date=far_start, end_date=far_end, tz="UTC")


async def test_invalid_tz_rejected_through_method() -> None:
    with pytest.raises(BusinessError):
        await _svc([]).get_task_timeseries(start_date=_START, end_date=_END, tz="Mars/Phobos")
