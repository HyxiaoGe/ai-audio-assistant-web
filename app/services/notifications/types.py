"""通知类型中心化定义：枚举 + 模板表（spec §5.1）。"""

from __future__ import annotations

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
