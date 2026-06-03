"""通知类型中心化定义：枚举 + 模板表（spec §5.1）。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class NotificationType(StrEnum):
    """规范化通知类型 = i18n key 选择器 + 模板选择器。"""

    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    QUOTA_ALERT = "quota_alert"
    YOUTUBE_REAUTH_REQUIRED = "youtube_reauth_required"
    VISUAL_FAILED = "visual_failed"


class NotificationCategory(StrEnum):
    """粗分组，用于筛选/索引。"""

    TASK = "task"
    SYSTEM = "system"
    YOUTUBE = "youtube"


class NotificationPriority(StrEnum):
    """优先级，仅保留实际产出的两档。"""

    NORMAL = "normal"
    HIGH = "high"


@dataclass(frozen=True)
class NotificationTemplate:
    """单个通知类型的中心化元数据。"""

    category: NotificationCategory
    priority: NotificationPriority
    i18n_key: str  # e.g. "notif.task_completed"
    channels: tuple[str, ...]  # 允许渠道，e.g. ("in_app",) / ("in_app", "feishu")


# spec §5.1 通知类型表：一处中心化定义，每个 type 恰好一条。
NOTIFICATION_TEMPLATES: dict[NotificationType, NotificationTemplate] = {
    NotificationType.TASK_COMPLETED: NotificationTemplate(
        category=NotificationCategory.TASK,
        priority=NotificationPriority.NORMAL,
        i18n_key="notif.task_completed",
        channels=("in_app",),
    ),
    NotificationType.TASK_FAILED: NotificationTemplate(
        category=NotificationCategory.TASK,
        priority=NotificationPriority.HIGH,
        i18n_key="notif.task_failed",
        channels=("in_app", "feishu"),
    ),
    NotificationType.QUOTA_ALERT: NotificationTemplate(
        category=NotificationCategory.SYSTEM,
        priority=NotificationPriority.HIGH,
        i18n_key="notif.quota_alert",
        channels=("in_app", "feishu"),
    ),
    NotificationType.YOUTUBE_REAUTH_REQUIRED: NotificationTemplate(
        category=NotificationCategory.YOUTUBE,
        priority=NotificationPriority.HIGH,
        i18n_key="notif.youtube_reauth_required",
        channels=("in_app",),
    ),
    NotificationType.VISUAL_FAILED: NotificationTemplate(
        category=NotificationCategory.TASK,
        priority=NotificationPriority.NORMAL,
        i18n_key="notif.visual_failed",
        channels=("in_app",),
    ),
}
