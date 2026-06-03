"""app.services.notifications.events 单测：统一信封形状 + NotificationEvent 数据类。

信封是 WS 推送与 REST 返回的统一契约：
    {"kind": "notification", "data": <NotificationResponse dict>, "traceId": <str>}
task_progress 信封在任务进度已发布处构造（kind="task_progress"），此处只测 notification 侧。
"""

from __future__ import annotations

from app.services.notifications.events import (
    NotificationEvent,
    notification_envelope,
)
from app.services.notifications.types import (
    NotificationCategory,
    NotificationPriority,
    NotificationType,
)


def test_notification_envelope_shape() -> None:
    notif = {
        "id": "n1",
        "type": "task_completed",
        "category": "task",
        "priority": "normal",
        "params": {"task_title": "demo"},
        "action_url": "/tasks/t1",
        "title": None,
        "message": None,
        "created_at": "2026-06-03T00:00:00Z",
        "read_at": None,
    }
    env = notification_envelope(notif, "trace-abc")
    assert env == {"kind": "notification", "data": notif, "traceId": "trace-abc"}


def test_notification_envelope_wraps_without_polluting_input_top_level() -> None:
    notif = {"id": "n1"}
    env = notification_envelope(notif, "t")
    env["data"]["id"] = "MUTATED"
    # 信封持有同一引用是允许的；这里断言 kind/traceId 是新键，未污染原 dict 顶层
    assert "kind" not in notif
    assert "traceId" not in notif


def test_notification_event_holds_routing_metadata() -> None:
    event = NotificationEvent(
        type=NotificationType.TASK_COMPLETED,
        user_id="u1",
        params={"task_title": "demo"},
        category=NotificationCategory.TASK,
        priority=NotificationPriority.NORMAL,
        action_url="/tasks/t1",
        task_id="t1",
        dedup_key=None,
    )
    assert event.type is NotificationType.TASK_COMPLETED
    assert event.user_id == "u1"
    assert event.params == {"task_title": "demo"}
    assert event.category is NotificationCategory.TASK
    assert event.priority is NotificationPriority.NORMAL
    assert event.action_url == "/tasks/t1"
    assert event.task_id == "t1"
    assert event.dedup_key is None
