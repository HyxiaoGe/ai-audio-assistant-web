from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseRecord


class YouTubeSearchQuery(BaseRecord):
    """YouTube 关键词搜索的「查询→结果」缓存,兼作热门搜索底料。

    一行 = 一个归一化查询词。results_json 缓存该词最近一次 ytsearch 结果;
    fetched_at 决定 6h TTL;search_count/last_searched_at 供热门聚合。
    """

    __tablename__ = "youtube_search_queries"
    __table_args__ = (
        UniqueConstraint("normalized_query", name="uk_youtube_search_queries_normalized"),
        Index(
            "idx_youtube_search_queries_trending",
            text("last_searched_at DESC"),
            postgresql_where=text("is_blocked = FALSE"),
        ),
    )

    normalized_query: Mapped[str] = mapped_column(String(128), nullable=False)
    display_query: Mapped[str] = mapped_column(String(128), nullable=False)
    results_json: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    search_count: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    last_searched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_blocked: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
