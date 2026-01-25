"""YouTube video cache model."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseRecord


class YouTubeVideo(BaseRecord):
    """YouTube video cache model.

    Stores video metadata from subscribed channels for quick browsing.
    Synced periodically from YouTube Data API.
    """

    __tablename__ = "youtube_videos"

    # Foreign key to subscription (channel)
    subscription_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("youtube_subscriptions.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Owner (denormalized for faster queries)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # YouTube identifiers
    video_id: Mapped[str] = mapped_column(String(20), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(100), nullable=False)

    # Video metadata (from snippet)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    thumbnail_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Video details (from contentDetails)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Statistics (from statistics) - updated periodically
    view_count: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    like_count: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    comment_count: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    # Sync metadata
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # Primary uniqueness: one video per user
        UniqueConstraint("user_id", "video_id", name="uk_youtube_videos_user_video"),
        # Fast lookup by channel (for channel video listing)
        Index("idx_youtube_videos_channel_published", "user_id", "channel_id", "published_at"),
        # Fast lookup for latest videos across all subscriptions
        Index("idx_youtube_videos_user_published", "user_id", "published_at"),
        # Fast lookup by subscription
        Index("idx_youtube_videos_subscription", "subscription_id"),
    )
