from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseRecord


class YouTubeSubscription(BaseRecord):
    """YouTube subscription cache model.

    Stores user's YouTube subscriptions locally for faster access.
    Synced periodically from YouTube Data API.
    """

    __tablename__ = "youtube_subscriptions"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("user_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel_id: Mapped[str] = mapped_column(String(100), nullable=False)
    channel_title: Mapped[str] = mapped_column(String(255), nullable=False)
    channel_thumbnail: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    subscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Cached uploads playlist ID (for fetching channel videos)
    uploads_playlist_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Last time videos were synced for this channel
    videos_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Channel visibility and sync control
    is_hidden: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    # sync_enabled 是「用户手动开关」(PATCH settings 可写);sync_channel_videos 见其为 false 会 skip。
    sync_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    is_starred: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)

    # Tier 2 懒加载:系统管理的冷/热位(独立于用户的 sync_enabled)。
    # is_warm=false=冷(懒加载,不周期同步/不预热);用户真有行为(打开/转写/星标)才提级转热。
    # 调度真正资格 = is_warm AND sync_enabled AND NOT needs_reauth。冷频道仍保持 sync_enabled=true,
    # 故点开时的按需拉取不会被 sync_channel_videos 的 sync_enabled 守卫 skip。
    is_warm: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    # 最近一次真实用户行为(打开/转写/星标)时间,供将来降级/观测用。
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Auto-transcription settings
    auto_transcribe: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    auto_transcribe_max_duration: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # Max video duration in seconds (default 7200 = 2 hours)
    auto_transcribe_language: Mapped[str | None] = mapped_column(
        String(10), nullable=True
    )  # Preferred language for transcription

    # Intelligent sync frequency
    avg_publish_interval_hours: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )  # Calculated average publish interval
    last_publish_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # Most recent video publish time
    next_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # Calculated next optimal sync time

    __table_args__ = (
        UniqueConstraint("user_id", "channel_id", name="uk_youtube_subscriptions_user_channel"),
        Index("idx_youtube_subscriptions_user_id", "user_id"),
        Index(
            "idx_youtube_subscriptions_starred",
            "user_id",
            "is_starred",
            postgresql_where=text("is_starred = true"),
        ),
        Index(
            "idx_youtube_subscriptions_next_sync",
            "next_sync_at",
            postgresql_where=text("sync_enabled = true"),
        ),
        # Tier 2 调度热路径:check_scheduled_syncs 将来按 (is_warm AND sync_enabled) 选行。
        Index(
            "idx_youtube_subscriptions_warm",
            "next_sync_at",
            postgresql_where=text("is_warm = true AND sync_enabled = true"),
        ),
    )
