from __future__ import annotations

from sqlalchemy import Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class YouTubeBlocklistEntry(BaseModel):
    """运行时可维护的 /discover 黑名单条目(软删)。

    BaseModel(非 BaseRecord)才带 deleted_at 软删列。
    kind='term' 时 match_field 固定 'query';kind='channel' 时为 'channel_id' 或 'channel_name'。
    match_field 全部 NOT NULL —— 唯一键含它,Postgres 唯一约束里 NULL 互不相等会击穿去重。
    """

    __tablename__ = "youtube_blocklist"
    __table_args__ = (
        UniqueConstraint("kind", "match_field", "normalized_value", name="uk_youtube_blocklist_entry"),
        Index("idx_youtube_blocklist_active", "kind", postgresql_where=text("deleted_at IS NULL")),
    )

    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    match_field: Mapped[str] = mapped_column(String(16), nullable=False)
    raw_value: Mapped[str] = mapped_column(String(256), nullable=False)
    normalized_value: Mapped[str] = mapped_column(String(256), nullable=False)
    note: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
