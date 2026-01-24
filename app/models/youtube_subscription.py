from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
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

    __table_args__ = (
        UniqueConstraint("user_id", "channel_id", name="uk_youtube_subscriptions_user_channel"),
        Index("idx_youtube_subscriptions_user_id", "user_id"),
    )
