"""通知事件与统一推送信封。

- ``NotificationEvent``：NotificationService 内部传给各渠道的事件载荷（含路由元数据）。
- ``notification_envelope``：把一条 NotificationResponse dict 包成 ``kind="notification"`` 的
  统一信封；``task_progress`` 信封在任务进度已发布处（worker）就地构造，保持加法式不破坏老前端。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.notifications.types import (
    NotificationCategory,
    NotificationPriority,
    NotificationType,
)


@dataclass
class NotificationEvent:
    type: NotificationType
    user_id: str
    params: dict
    category: NotificationCategory
    priority: NotificationPriority
    action_url: str | None
    task_id: str | None
    dedup_key: str | None


def notification_envelope(notif_response_dict: dict, trace_id: str) -> dict:
    """把 NotificationResponse dict 包成统一推送信封。

    返回 ``{"kind": "notification", "data": <dict>, "traceId": trace_id}``。
    """
    return {
        "kind": "notification",
        "data": notif_response_dict,
        "traceId": trace_id,
    }
