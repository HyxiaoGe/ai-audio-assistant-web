from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class FlagBatchResolveRequest(BaseModel):
    flag_ids: list[str] = Field(min_length=1, max_length=50)
    action: str  # "block" | "dismiss";非法值由 service 抛 INVALID_PARAMETER
    note: str | None = None

    @field_validator("flag_ids")
    @classmethod
    def _dedup_nonempty(cls, v: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for x in v:
            x = x.strip()
            if x and x not in seen:
                seen.add(x)
                out.append(x)
        if not out:
            raise ValueError("flag_ids cannot be empty")
        return out


class FlagBatchResolveItem(BaseModel):
    flag_id: str
    status: str  # "succeeded" | "skipped" | "failed"
    code: int | None = None  # status == "failed" 时的错误码


class FlagBatchResolveResponse(BaseModel):
    resolved_count: int  # succeeded + skipped(已移出 pending 队列的条数)
    items: list[FlagBatchResolveItem]
