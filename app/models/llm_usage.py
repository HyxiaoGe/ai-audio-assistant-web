from __future__ import annotations

from typing import Optional

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseRecord


class LLMUsage(BaseRecord):
    __tablename__ = "llm_usages"
    __table_args__ = (
        Index("idx_llm_usages_user", "user_id"),
        Index("idx_llm_usages_provider", "provider"),
        Index("idx_llm_usages_created_at", "created_at"),
        Index("idx_llm_usages_task", "task_id"),
        Index("idx_llm_usages_user_created_at", "user_id", "created_at"),
    )

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    call_type: Mapped[str] = mapped_column(String(50), nullable=False)
    summary_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(20), server_default=text("'success'"))
