from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.notification import Notification
    from app.models.task_stage import TaskStage


class Task(BaseModel):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("user_id", "content_hash", "deleted_at", name="uk_tasks_hash"),
        Index(
            "idx_tasks_user",
            "user_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_tasks_status",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_tasks_created",
            text("created_at DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_tasks_hash",
            "content_hash",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_metadata: Mapped[dict[str, object]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )

    options: Mapped[dict[str, object]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(20), server_default=text("'pending'"), nullable=False
    )
    progress: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    stage: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    detected_language: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    error_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)

    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    asr_provider: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    llm_provider: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Relationships
    notifications: Mapped[list["Notification"]] = relationship(
        "Notification", back_populates="task", cascade="all, delete-orphan"
    )
    stages: Mapped[list["TaskStage"]] = relationship(
        "TaskStage",
        back_populates="task",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="TaskStage.created_at",
    )
