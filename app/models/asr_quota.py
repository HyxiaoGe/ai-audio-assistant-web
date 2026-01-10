from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseRecord


class AsrQuota(BaseRecord):
    __tablename__ = "asr_quotas"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "variant",
            "window_type",
            "window_start",
            "owner_user_id",
            name="uk_asr_quotas_provider_window",
        ),
    )

    owner_user_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    variant: Mapped[str] = mapped_column(String(30), nullable=False, server_default=text("'file'"))
    window_type: Mapped[str] = mapped_column(String(10), nullable=False)  # day | month | total
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quota_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    used_seconds: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'active'"))
