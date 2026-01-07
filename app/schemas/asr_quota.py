from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


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
    quota_seconds: int | None = Field(default=None, gt=0)
    quota_hours: float | None = Field(default=None, gt=0)
    reset: bool = Field(default=True)

    @model_validator(mode="after")
    def _ensure_quota(self) -> "AsrQuotaUpsertRequest":
        if self.quota_seconds is None and self.quota_hours is None:
            raise ValueError("quota_seconds or quota_hours is required")
        return self


class AsrQuotaUpsertResponse(BaseModel):
    item: AsrQuotaItem | None
