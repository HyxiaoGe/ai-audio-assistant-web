from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.notification import Notification


class UserProfile(BaseModel):
    __tablename__ = "user_profiles"
    __table_args__ = (
        Index("idx_user_profiles_status", "status"),
    )

    # id = auth-service user_id (set explicitly, not auto-generated)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    app_settings: Mapped[dict[str, object]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )

    # Relationships
    notifications: Mapped[list["Notification"]] = relationship(
        "Notification", back_populates="user", cascade="all, delete-orphan"
    )
