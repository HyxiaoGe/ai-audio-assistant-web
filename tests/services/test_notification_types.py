"""通知类型表 / 枚举 / 模板的完备性与一致性单测。"""

from __future__ import annotations


def test_notifications_package_importable() -> None:
    # 包必须可导入，作为后续 types/service/channels 的命名空间根
    import app.services.notifications  # noqa: F401


def test_enum_values_match_spec() -> None:
    from app.services.notifications.types import (
        NotificationCategory,
        NotificationPriority,
        NotificationType,
    )

    # NotificationType：spec §5.1 表五种类型，值为下划线小写
    assert {t.value for t in NotificationType} == {
        "task_completed",
        "task_failed",
        "quota_alert",
        "youtube_reauth_required",
        "visual_failed",
    }
    # 枚举是 StrEnum：成员等于其字符串值（落库/比较用裸字符串）
    assert NotificationType.TASK_COMPLETED == "task_completed"

    # Category：task / system / youtube
    assert {c.value for c in NotificationCategory} == {"task", "system", "youtube"}
    assert NotificationCategory.YOUTUBE == "youtube"

    # Priority：仅 normal / high（删掉死的 urgent/low）
    assert {p.value for p in NotificationPriority} == {"normal", "high"}
