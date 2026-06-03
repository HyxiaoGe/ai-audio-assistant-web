from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.services import asr_quota_alert
from app.services.asr_quota_alert import QuotaAlertInfo
from app.services.notifications.types import NotificationType


@pytest.mark.asyncio
async def test_send_quota_alert_calls_notify_with_dedup_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """配额预警改调 notify(QUOTA_ALERT)，dedup_key=quota:{provider}:{variant}:{threshold}:{utc_date}。"""
    alert = QuotaAlertInfo(
        provider="volcengine",
        variant="file",
        window_type="monthly",
        quota_seconds=3600.0,
        used_seconds=3600.0,
        usage_percent=100.0,
        threshold=100,
        owner_user_id="user-1",
    )
    now = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)

    calls: list[dict[str, Any]] = []

    def _spy_notify(sess: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(asr_quota_alert.NotificationService, "notify", staticmethod(_spy_notify))

    await asr_quota_alert.send_quota_alert_notification(db=None, user_id="user-1", alert=alert, now=now)

    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["type"] == NotificationType.QUOTA_ALERT
    assert kwargs["user_id"] == "user-1"
    assert kwargs["dedup_key"] == "quota:volcengine:file:100:2026-06-03"
    assert kwargs["params"]["provider"] == "volcengine"
    assert kwargs["params"]["variant"] == "file"
    assert kwargs["params"]["threshold"] == 100
