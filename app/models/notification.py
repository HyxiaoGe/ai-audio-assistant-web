"""Notification model for storing user notifications."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseRecord

if TYPE_CHECKING:
    from app.models.task import Task
    from app.models.user import User


class Notification(BaseRecord):
    """
    User notification model for task-related notifications.

    Design principles:
    - Use read_at instead of read boolean (know WHEN it was read)
    - Support dismissal separate from reading
    - JSONB metadata for flexible extension
    - Partial indexes for performance
    - SET NULL on task deletion (keep notification history)
    """

    __tablename__ = "notifications"

    # Foreign Keys
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tasks.id", ondelete="SET NULL"),  # 任务删除后通知仍保留
        nullable=True,
    )

    # Core notification fields
    category: Mapped[str] = mapped_column(String(50), nullable=False)  # task, system

    action: Mapped[str] = mapped_column(String(50), nullable=False)  # completed, failed, progress

    title: Mapped[str] = mapped_column(String(255), nullable=False)  # "任务《xxx》已完成"

    message: Mapped[str] = mapped_column(Text, nullable=False)  # 详细描述

    action_url: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True
    )  # 跳转链接 /tasks/{id}

    # Status fields
    read_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # NULL = 未读，有值 = 已读时间

    dismissed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # 用户主动关闭的时间

    # Extension fields
    extra_data: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )  # 扩展数据: {"task_title": "xxx", "duration": 120, "error_code": 500}

    priority: Mapped[str] = mapped_column(
        String(10), default="normal", server_default=text("'normal'"), nullable=False
    )  # urgent, high, normal, low

    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # 过期时间（可选）

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="notifications")  # type: ignore
    task: Mapped[Optional["Task"]] = relationship(  # type: ignore
        "Task", back_populates="notifications"
    )

    # Indexes - 使用部分索引提升性能
    __table_args__ = (
        # 查询未读通知（最常用的查询）
        Index(
            "ix_notifications_unread",
            "user_id",
            "created_at",
            postgresql_where=text("read_at IS NULL AND dismissed_at IS NULL"),
        ),
        # 按分类查询
        Index("ix_notifications_category", "user_id", "category", "created_at"),
        # 清理已读通知（定时任务用）
        Index(
            "ix_notifications_cleanup",
            "read_at",
            "created_at",
            postgresql_where=text("read_at IS NOT NULL"),
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Notification(id={self.id}, user_id={self.user_id}, "
            f"category={self.category}, action={self.action}, "
            f"read={self.read_at is not None})>"
        )
