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
    __table_args__ = (Index("idx_user_profiles_status", "status"),)

    # id = auth-service user_id (set explicitly, not auto-generated)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    app_settings: Mapped[dict[str, object]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    # 发布者身份(探索广场展示):发布任务时从 /auth/userinfo 捕获,快照式。NULL=未捕获→不展示。
    # name/avatar 由 auth-service 管理,这里只做本地缓存以支撑匿名公开端点的展示需求。
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Relationships
    notifications: Mapped[list[Notification]] = relationship(
        "Notification", back_populates="user", cascade="all, delete-orphan"
    )
