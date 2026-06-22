from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
