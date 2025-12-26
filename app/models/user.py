from __future__ import annotations

from typing import Optional

from sqlalchemy import Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class User(BaseModel):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", "deleted_at", name="uk_users_email"),
        Index(
            "idx_users_email",
            "email",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "idx_users_phone",
            "phone",
            postgresql_where=text("deleted_at IS NULL AND phone IS NOT NULL"),
        ),
        Index("idx_users_status", "status"),
    )

    email: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    locale: Mapped[str] = mapped_column(String(10), default="zh", nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), default="Asia/Shanghai", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    settings: Mapped[dict[str, object]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
