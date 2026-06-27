from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseRecord


class FlaggedChannel(BaseRecord):
    """CMS 展示态判 block 时累积的频道标记,供管理端人工复核队列。

    选 BaseRecord(无 deleted_at):用 status 生命周期管理,不软删。
    created_at = 首次标记时间;last_flagged_at = 最近 block 时间(resolve 也会动 updated_at,故二者区分)。
    去重键 (match_field, match_value) 镜像 is_channel_blocked 三级优先,确保标记身份与 filter_hits/add_entry 同源。
    channel_id/channel_handle/channel_name 存原值供展示 + 提升黑名单时取最强值。
    """

    __tablename__ = "flagged_channels"
    __table_args__ = (
        UniqueConstraint("match_field", "match_value", name="uk_flagged_channels"),
        Index("idx_flagged_channels_pending", "status", postgresql_where=text("status = 'pending'")),
    )

    match_field: Mapped[str] = mapped_column(String(16), nullable=False)
    match_value: Mapped[str] = mapped_column(String(256), nullable=False)
    channel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    channel_handle: Mapped[str | None] = mapped_column(String(128), nullable=True)
    channel_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    block_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_video_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_flagged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'pending'"))
    resolved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
