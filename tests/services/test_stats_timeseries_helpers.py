from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.core.exceptions import BusinessError
from app.services.stats_service import _daily_axis, _fold_status, _resolve_tz


def test_resolve_tz_defaults_to_shanghai_when_missing() -> None:
    assert _resolve_tz(None) == "Asia/Shanghai"
    assert _resolve_tz("") == "Asia/Shanghai"


def test_resolve_tz_passes_valid_iana() -> None:
    assert _resolve_tz("UTC") == "UTC"
    assert _resolve_tz("America/New_York") == "America/New_York"


def test_resolve_tz_rejects_invalid() -> None:
    with pytest.raises(BusinessError):
        _resolve_tz("Mars/Phobos")


def test_fold_status_maps_into_four_buckets() -> None:
    assert _fold_status("completed") == "completed"
    assert _fold_status("failed") == "failed"
    assert _fold_status("pending") == "pending"
    assert _fold_status("queued") == "pending"
    for s in ("processing", "extracting", "transcribing", "summarizing", "weird"):
        assert _fold_status(s) == "processing"


def test_daily_axis_is_continuous_and_inclusive() -> None:
    start = datetime(2026, 6, 19, 0, 0, tzinfo=UTC)
    end = datetime(2026, 6, 22, 0, 0, tzinfo=UTC)
    assert _daily_axis(start, end, "UTC") == [
        date(2026, 6, 19),
        date(2026, 6, 20),
        date(2026, 6, 21),
        date(2026, 6, 22),
    ]


def test_daily_axis_buckets_by_local_timezone() -> None:
    # UTC 17:00 = Asia/Shanghai 次日 01:00 → 本地日推到 6/21
    start = datetime(2026, 6, 20, 17, 0, tzinfo=UTC)
    end = datetime(2026, 6, 20, 18, 0, tzinfo=UTC)
    assert _daily_axis(start, end, "Asia/Shanghai") == [date(2026, 6, 21)]
    assert _daily_axis(start, end, "UTC") == [date(2026, 6, 20)]
