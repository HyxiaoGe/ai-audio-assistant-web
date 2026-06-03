"""Notification model for storing user notifications."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseRecord

if TYPE_CHECKING:
    from app.models.task import Task
    from app.models.user import UserProfile


class Notification(BaseRecord):
    """
    User notification model.

    Design principles (after refactor):
    - Pure unread/read lifecycle (read_at only; no dismiss/delete).
    - `type` is the canonical selector for i18n key + template metadata.
    - `extra_data` holds language-agnostic params (exposed as "params" at schema/API layer).
    - `title`/`message` are nullable transition fallbacks (zh rendered by InAppChannel).
    - `dedup_key` enables atomic dedup via partial unique index.
    - Deleting a task cascades to its notifications.
    """

    __tablename__ = "notifications"

    # Foreign Keys
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tasks.id", ondelete="CASCADE"),  # 删任务连带删通知
        nullable=True,
    )

    # Core notification fields
    type: Mapped[str] = mapped_column(String(50), nullable=False)  # 规范化类型 = i18n key 选择器

    category: Mapped[str] = mapped_column(String(50), nullable=False)  # task, system, youtube

    title: Mapped[str | None] = mapped_column(String(255), nullable=True)  # 过渡期 zh 兜底串

    message: Mapped[str | None] = mapped_column(Text, nullable=True)  # 过渡期 zh 兜底串

    action_url: Mapped[str | None] = mapped_column(String(500), nullable=True)  # 跳转链接 /tasks/{id}

    # Dedup
    dedup_key: Mapped[str | None] = mapped_column(String(255), nullable=True)  # 原子去重键

    # Status field（唯一状态位）
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # NULL = 未读，有值 = 已读时间

    # Extension fields
    extra_data: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )  # 语言无关渲染参数: {"task_title": "xxx", "duration": 120, "error_code": 500}

    priority: Mapped[str] = mapped_column(
        String(10), default="normal", server_default=text("'normal'"), nullable=False
    )  # normal, high

    # Relationships
    user: Mapped[UserProfile] = relationship("UserProfile", back_populates="notifications")  # type: ignore
    task: Mapped[Task | None] = relationship(  # type: ignore
        "Task", back_populates="notifications"
    )

    # Indexes - 部分索引提升性能
    __table_args__ = (
        # 查询未读通知（最常用）；纯未读/已读，不再含 dismissed 条件
        Index(
            "ix_notifications_unread",
            "user_id",
            "created_at",
            postgresql_where=text("read_at IS NULL"),
        ),
        # 按分类查询
        Index("ix_notifications_category", "user_id", "category", "created_at"),
        # 原子去重：dedup_key 部分唯一索引
        Index(
            "ix_notifications_dedup_key",
            "dedup_key",
            unique=True,
            postgresql_where=text("dedup_key IS NOT NULL"),
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Notification(id={self.id}, user_id={self.user_id}, "
            f"type={self.type}, read={self.read_at is not None})>"
        )
