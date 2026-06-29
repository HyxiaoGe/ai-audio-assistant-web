from __future__ import annotations

from sqlalchemy import Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class YouTubeAllowlistEntry(BaseModel):
    """运行时可维护的 /discover 频道放行表(软删,频道专用)。

    命中放行表的频道在搜索展示态绕过 CMS(filter_display)直接保留 —— 用于把被
    误杀的合法频道恢复可搜。与 youtube_blocklist 是镜像孪生,但无 term、无 kind 列。
    BaseModel(非 BaseRecord)才带 deleted_at 软删列。
    match_field 全部 NOT NULL —— 唯一键含它,Postgres 唯一约束里 NULL 互不相等会击穿去重。
    """

    __tablename__ = "youtube_allowlist"
    __table_args__ = (
        UniqueConstraint("match_field", "normalized_value", name="uk_youtube_allowlist_entry"),
        Index("idx_youtube_allowlist_active", "match_field", postgresql_where=text("deleted_at IS NULL")),
    )

    match_field: Mapped[str] = mapped_column(String(16), nullable=False)  # channel_id | channel_handle | channel_name
    raw_value: Mapped[str] = mapped_column(String(256), nullable=False)
    normalized_value: Mapped[str] = mapped_column(String(256), nullable=False)
    note: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
