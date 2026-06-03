"""NotificationResponse 新契约形状 + 死 schema 删除锁定。

新形状：{id, type, category, priority, params, action_url, title, message, created_at, read_at}
params 来自 ORM 的 extra_data 列（物理名 extra_data，API 暴露为 params）。
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.schemas.notification import NotificationResponse


def test_notification_list_request_is_deleted() -> None:
    import app.schemas.notification as mod

    assert not hasattr(mod, "NotificationListRequest")


def test_notification_response_new_shape_from_attributes() -> None:
    class _Row:
        id = "11111111-1111-1111-1111-111111111111"
        type = "task_completed"
        category = "task"
        priority = "normal"
        extra_data = {"task_title": "周会", "duration": 120}
        action_url = "/tasks/abc"
        title = "任务已完成"
        message = "《周会》已处理完成"
        created_at = datetime(2026, 6, 3, tzinfo=UTC)
        read_at = None

    resp = NotificationResponse.model_validate(_Row())
    assert resp.id == "11111111-1111-1111-1111-111111111111"
    assert resp.type == "task_completed"
    assert resp.category == "task"
    assert resp.priority == "normal"
    assert resp.params == {"task_title": "周会", "duration": 120}
    assert resp.action_url == "/tasks/abc"
    assert resp.title == "任务已完成"
    assert resp.read_at is None


def test_notification_response_allows_null_title_message() -> None:
    class _Row:
        id = "22222222-2222-2222-2222-222222222222"
        type = "visual_failed"
        category = "task"
        priority = "normal"
        extra_data = {}
        action_url = None
        title = None
        message = None
        created_at = datetime(2026, 6, 3, tzinfo=UTC)
        read_at = None

    resp = NotificationResponse.model_validate(_Row())
    assert resp.title is None
    assert resp.message is None
    assert resp.params == {}


def test_notification_response_no_legacy_fields() -> None:
    fields = set(NotificationResponse.model_fields)
    for dead in ("action", "dismissed_at", "expires_at", "extra_data", "updated_at", "user_id"):
        assert dead not in fields, f"NotificationResponse 不应再含遗留字段 {dead}"


def test_notification_stats_dropped_dismissed() -> None:
    from app.schemas.notification import NotificationStatsResponse

    assert "dismissed" not in NotificationStatsResponse.model_fields
    s = NotificationStatsResponse(total=3, unread=1)
    assert s.total == 3 and s.unread == 1
