from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class FlaggedChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    match_field: str
    match_value: str
    channel_id: str | None
    channel_handle: str | None
    channel_name: str | None
    block_count: int
    last_video_id: str | None
    last_title: str | None
    status: str
    first_flagged_at: datetime | None = None  # 映射 created_at
    last_flagged_at: datetime | None = None


class FlaggedChannelListOut(BaseModel):
    items: list[FlaggedChannelOut]


class FlagResolveRequest(BaseModel):
    action: str  # "block" | "dismiss";非法值由 service 抛 INVALID_PARAMETER,保持统一 envelope
    note: str | None = None
