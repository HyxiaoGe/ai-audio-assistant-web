from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.api.deps import CurrentUser
from app.services.stats_service import StatsService


class _ScalarResult:
    def __init__(self, value):
        self._v = value

    def scalar_one_or_none(self):
        return self._v


class _MaxSession:
    """只服务 _resolve_default_range 里的 MAX(created_at) 查询。"""

    def __init__(self, latest):
        self._latest = latest

    async def execute(self, _stmt):
        return _ScalarResult(self._latest)


class _ListResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _QueueSession:
    """依次弹出预置结果集;支持 .all()(给 timeseries 的 task/asr 两次查询用)。"""

    def __init__(self, result_sets: list[list[Any]]) -> None:
        self._queue = list(result_sets)

    async def execute(self, _stmt: Any) -> _ListResult:
        return _ListResult(self._queue.pop(0) if self._queue else [])


def _svc(latest):
    return StatsService(_MaxSession(latest), CurrentUser(id="u-1", email="x@e.com"))


@pytest.mark.asyncio
async def test_resolve_default_week_when_recent():
    now = datetime.now(UTC)
    assert await _svc(now - timedelta(days=5))._resolve_default_range(now) == "week"


@pytest.mark.asyncio
async def test_resolve_default_month_when_within_30d():
    now = datetime.now(UTC)
    assert await _svc(now - timedelta(days=20))._resolve_default_range(now) == "month"


@pytest.mark.asyncio
async def test_resolve_default_all_when_old():
    now = datetime.now(UTC)
    assert await _svc(now - timedelta(days=100))._resolve_default_range(now) == "all"


@pytest.mark.asyncio
async def test_resolve_default_week_when_no_tasks():
    now = datetime.now(UTC)
    assert await _svc(None)._resolve_default_range(now) == "week"


@pytest.mark.asyncio
async def test_parse_time_range_explicit_passthrough():
    now = datetime.now(UTC)
    start, end, resolved = await _svc(None)._parse_time_range("month", None, None)
    assert resolved == "month"
    assert (now - start) >= timedelta(days=29)


@pytest.mark.asyncio
async def test_parse_time_range_custom_label():
    s = datetime(2026, 1, 1, tzinfo=UTC)
    e = datetime(2026, 1, 10, tzinfo=UTC)
    start, end, resolved = await _svc(None)._parse_time_range(None, s, e)
    assert resolved == "custom"


@pytest.mark.asyncio
async def test_parse_time_range_smart_default_uses_ladder():
    now = datetime.now(UTC)
    # 最新任务 5 天前 → 无参应解析为 week 窗口
    start, end, resolved = await _svc(now - timedelta(days=5))._parse_time_range(None, None, None)
    assert resolved == "week"
    assert (now - start) <= timedelta(days=8)


# ---------- Task 2:timeseries 超长跨度截断 ----------


@pytest.mark.asyncio
async def test_timeseries_clamps_long_named_range(monkeypatch: pytest.MonkeyPatch) -> None:
    """命名档(resolved!='custom')跨度>366天 → start 被截到 end-366d,不抛错。"""
    now = datetime.now(UTC)
    svc = StatsService(_QueueSession([[], []]), CurrentUser(id="u-1", email="x@e.com"))

    async def fake_parse(*_a: Any, **_k: Any):  # noqa: ANN202
        return now - timedelta(days=500), now, "all"

    monkeypatch.setattr(svc, "_parse_time_range", fake_parse)
    result = await svc.get_task_timeseries(None, None, None, "UTC")
    span = result["time_range"]["end"] - result["time_range"]["start"]
    assert span <= timedelta(days=366)


@pytest.mark.asyncio
async def test_timeseries_custom_too_long_still_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """custom 跨度>366天 → 仍抛 BusinessError(PARAMETER_ERROR)。"""
    from app.core.exceptions import BusinessError

    now = datetime.now(UTC)
    svc = StatsService(_QueueSession([]), CurrentUser(id="u-1", email="x@e.com"))

    async def fake_parse(*_a: Any, **_k: Any):  # noqa: ANN202
        return now - timedelta(days=500), now, "custom"

    monkeypatch.setattr(svc, "_parse_time_range", fake_parse)
    with pytest.raises(BusinessError):
        await svc.get_task_timeseries(None, datetime(2025, 1, 1, tzinfo=UTC), now, "UTC")
