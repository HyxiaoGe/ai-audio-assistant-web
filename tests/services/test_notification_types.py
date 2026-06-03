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


def test_notification_model_columns() -> None:
    from app.models.notification import Notification

    cols = Notification.__table__.columns
    # 新增列
    assert "type" in cols
    assert "dedup_key" in cols
    assert cols["type"].nullable is False
    assert cols["dedup_key"].nullable is True
    # 删除列
    for dead in ("action", "dismissed_at", "expires_at"):
        assert dead not in cols, f"{dead} 应已删除"
    # title/message 降为可空
    assert cols["title"].nullable is True
    assert cols["message"].nullable is True
    # extra_data 保留（params 物理列）
    assert "extra_data" in cols

def test_notification_task_fk_is_cascade() -> None:
    from app.models.notification import Notification

    fk = next(
        fk
        for fk in Notification.__table__.foreign_keys
        if fk.column.table.name == "tasks"
    )
    assert fk.ondelete == "CASCADE"

def test_notification_indexes() -> None:
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex

    from app.models.notification import Notification

    ddl = {
        idx.name: str(
            CreateIndex(idx).compile(dialect=postgresql.dialect())
        ).lower()
        for idx in Notification.__table__.indexes
    }
    # 未读部分索引：保留，但条件里不再含 dismissed
    assert "ix_notifications_unread" in ddl
    assert "read_at is null" in ddl["ix_notifications_unread"]
    assert "dismissed_at" not in ddl["ix_notifications_unread"]
    # dedup_key 部分唯一索引
    assert "ix_notifications_dedup_key" in ddl
    assert "unique" in ddl["ix_notifications_dedup_key"]
    assert "dedup_key is not null" in ddl["ix_notifications_dedup_key"]
    # 删除的旧索引
    assert "ix_notifications_cleanup" not in ddl
