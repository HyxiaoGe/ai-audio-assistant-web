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


def test_template_table_matches_spec() -> None:
    from app.services.notifications.types import (
        NOTIFICATION_TEMPLATES,
        NotificationCategory,
        NotificationPriority,
        NotificationType,
    )

    # 每个 type 必须恰好一条模板（无遗漏、无多余）
    assert set(NOTIFICATION_TEMPLATES.keys()) == set(NotificationType)

    tmpl = NOTIFICATION_TEMPLATES[NotificationType.TASK_COMPLETED]
    assert tmpl.category == NotificationCategory.TASK
    assert tmpl.priority == NotificationPriority.NORMAL
    assert tmpl.i18n_key == "notif.task_completed"
    assert tmpl.channels == ("in_app",)

    # task_failed: task / high
    tf = NOTIFICATION_TEMPLATES[NotificationType.TASK_FAILED]
    assert (tf.category, tf.priority) == (
        NotificationCategory.TASK,
        NotificationPriority.HIGH,
    )
    assert tf.i18n_key == "notif.task_failed"

    # quota_alert: system / high / 允许飞书
    qa = NOTIFICATION_TEMPLATES[NotificationType.QUOTA_ALERT]
    assert qa.category == NotificationCategory.SYSTEM
    assert qa.priority == NotificationPriority.HIGH
    assert "feishu" in qa.channels and "in_app" in qa.channels

    # youtube_reauth_required: youtube / high
    yr = NOTIFICATION_TEMPLATES[NotificationType.YOUTUBE_REAUTH_REQUIRED]
    assert yr.category == NotificationCategory.YOUTUBE
    assert yr.priority == NotificationPriority.HIGH

    # visual_failed: task / normal
    vf = NOTIFICATION_TEMPLATES[NotificationType.VISUAL_FAILED]
    assert (vf.category, vf.priority) == (
        NotificationCategory.TASK,
        NotificationPriority.NORMAL,
    )

def test_template_invariants() -> None:
    from app.services.notifications.types import (
        NOTIFICATION_TEMPLATES,
        NotificationTemplate,
    )

    for ntype, tmpl in NOTIFICATION_TEMPLATES.items():
        assert isinstance(tmpl, NotificationTemplate)
        # i18n_key 规范：notif.<type 值>
        assert tmpl.i18n_key == f"notif.{ntype.value}"
        # channels 非空、in_app 永远在内（in-app 是本期唯一实做渠道）
        assert tmpl.channels and "in_app" in tmpl.channels
        # 冻结 dataclass：不可变（防止运行期被改）
        import dataclasses

        with __import__("pytest").raises(dataclasses.FrozenInstanceError):
            tmpl.priority = tmpl.priority  # type: ignore[misc]
