"""Cached YouTube summary style recommendation model."""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseRecord


class YouTubeSummaryStyleRecommendation(BaseRecord):
    """Stores derived summary style recommendations for cached YouTube videos."""

    __tablename__ = "youtube_summary_style_recommendations"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("user_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    video_id: Mapped[str] = mapped_column(String(20), nullable=False)
    metadata_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(64), nullable=False)
    style: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "video_id",
            "metadata_hash",
            "algorithm_version",
            name="uk_youtube_style_recommendation_cache",
        ),
        Index("idx_youtube_style_recommendations_user_video", "user_id", "video_id"),
    )
