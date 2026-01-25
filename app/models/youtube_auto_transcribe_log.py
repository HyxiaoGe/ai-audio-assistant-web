"""YouTube auto-transcription log model."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseRecord


class YouTubeAutoTranscribeLog(BaseRecord):
    """Track auto-transcription attempts for YouTube videos.

    Prevents duplicate processing and provides audit trail.
    """

    __tablename__ = "youtube_auto_transcribe_logs"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    video_id: Mapped[str] = mapped_column(String(20), nullable=False)
    subscription_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("youtube_subscriptions.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Status: pending, created, skipped, failed
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # Reason for skipping (e.g., duration_exceeded:3700>3600, no_quota, already_exists)
    skip_reason: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "video_id", name="uk_youtube_auto_transcribe_user_video"),
        Index("idx_youtube_auto_transcribe_user_status", "user_id", "status"),
    )
