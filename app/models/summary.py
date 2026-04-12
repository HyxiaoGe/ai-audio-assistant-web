from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseRecord


class Summary(BaseRecord):
    __tablename__ = "summaries"
    __table_args__ = (
        Index("idx_summaries_task", "task_id"),
        Index(
            "idx_summaries_active",
            "task_id",
            "summary_type",
            postgresql_where=text("is_active = TRUE"),
        ),
    )

    task_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )

    summary_type: Mapped[str] = mapped_column(String(50), nullable=False)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)

    content: Mapped[str] = mapped_column(Text, nullable=False)
    model_used: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comparison_id: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Visual summary fields
    visual_format: Mapped[str | None] = mapped_column(String(20), nullable=True)
    visual_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    image_format: Mapped[str | None] = mapped_column(String(10), nullable=True)
    image_model_used: Mapped[str | None] = mapped_column(String(100), nullable=True)
