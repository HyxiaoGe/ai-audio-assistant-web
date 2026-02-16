from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import BaseRecord


class UserTemplateUsage(BaseRecord):
    __tablename__ = "user_template_usages"
    __table_args__ = (
        Index("ix_user_template_usages_template_id", "template_id"),
        Index("ix_user_template_usages_user_id", "user_id"),
    )

    user_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    template_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("prompt_templates.id", ondelete="CASCADE"),
        nullable=False,
    )
    used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
