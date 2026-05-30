from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import EncryptedString
from app.models.base import BaseRecord


class Account(BaseRecord):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("provider", "provider_account_id", name="uk_accounts_provider"),
        Index("idx_accounts_user", "user_id"),
    )

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # 落库透明加密（见 app/core/crypto.py）。底层仍为 TEXT，无需 schema 迁移；
    # 存量明文行通过 decrypt 的明文回退路径继续可读，下次写入时自动加密。
    access_token: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Flag indicating refresh token is invalid and user needs to re-authorize
    needs_reauth: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
