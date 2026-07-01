from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import BaseRecord


class YouTubeRecommendedVideo(BaseRecord):
    """/discover「热门推荐」的当前快照(harvest 每 6h 全量替换)。

    存的是抓取时已过审的干净子集;serve 时再套一次缓存黑名单保即时。rank=view_count 降序位次(0 起)。
    v1 只按绝对 view_count 排;趋势/velocity(需跨次快照)留给 v2。
    """

    __tablename__ = "youtube_recommended_videos"
    __table_args__ = (Index("idx_youtube_recommended_rank", "rank"),)

    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    video_id: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    channel: Mapped[str | None] = mapped_column(String(256), nullable=True)
    channel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    handle: Mapped[str | None] = mapped_column(String(128), nullable=True)
    thumbnail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    view_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    harvested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
