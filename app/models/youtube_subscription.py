from __future__ import annotations

from datetime import datetime
from typing import Optional

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
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel_id: Mapped[str] = mapped_column(String(100), nullable=False)
    channel_title: Mapped[str] = mapped_column(String(255), nullable=False)
    channel_thumbnail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    channel_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    subscribed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Cached uploads playlist ID (for fetching channel videos)
    uploads_playlist_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # Last time videos were synced for this channel
    videos_synced_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Channel visibility and sync control
    is_hidden: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    sync_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    is_starred: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)

    # Auto-transcription settings
    auto_transcribe: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False
    )
    auto_transcribe_max_duration: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )  # Max video duration in seconds (default 7200 = 2 hours)
    auto_transcribe_language: Mapped[Optional[str]] = mapped_column(
        String(10), nullable=True
    )  # Preferred language for transcription

    # Intelligent sync frequency
    avg_publish_interval_hours: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )  # Calculated average publish interval
    last_publish_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # Most recent video publish time
    next_sync_at: Mapped[Optional[datetime]] = mapped_column(
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
    )
