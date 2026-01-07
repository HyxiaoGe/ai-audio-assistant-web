from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AsrQuotaItem(BaseModel):
    provider: str
    window_type: Literal["day", "month"]
    window_start: datetime
    window_end: datetime
    quota_seconds: int
    used_seconds: int
    status: str


class AsrQuotaListResponse(BaseModel):
    items: list[AsrQuotaItem] = Field(default_factory=list)


class AsrQuotaUpsertRequest(BaseModel):
    provider: str = Field(min_length=1)
    window_type: Literal["day", "month"]
    quota_seconds: int = Field(gt=0)
    reset: bool = Field(default=True)


class AsrQuotaUpsertResponse(BaseModel):
    item: AsrQuotaItem
