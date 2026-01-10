from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class AsrQuotaItem(BaseModel):
    provider: str
    variant: str
    window_type: Literal["day", "month", "total"]
    window_start: datetime
    window_end: datetime
    quota_seconds: float
    used_seconds: float
    status: str


class AsrQuotaListResponse(BaseModel):
    items: list[AsrQuotaItem] = Field(default_factory=list)


class AsrQuotaUpsertRequest(BaseModel):
    provider: str = Field(min_length=1)
    variant: str = Field(default="file", min_length=1)
    window_type: Literal["day", "month", "total"]
    quota_seconds: float | None = Field(default=None, gt=0)
    quota_hours: float | None = Field(default=None, gt=0)
    window_start: datetime | None = None
    window_end: datetime | None = None
    used_seconds: float | None = Field(default=None, ge=0)
    reset: bool = Field(default=True)

    @model_validator(mode="after")
    def _ensure_quota(self) -> "AsrQuotaUpsertRequest":
        if self.quota_seconds is None and self.quota_hours is None:
            raise ValueError("quota_seconds or quota_hours is required")
        if self.window_start or self.window_end:
            if self.window_start is None or self.window_end is None:
                raise ValueError("window_start and window_end must be provided together")
            if self.window_type != "total":
                raise ValueError("window_start/window_end only supported for total quotas")
            if self.window_end <= self.window_start:
                raise ValueError("window_end must be greater than window_start")
        if self.used_seconds is not None and self.used_seconds < 0:
            raise ValueError("used_seconds must be >= 0")
        return self


class AsrQuotaUpsertResponse(BaseModel):
    item: AsrQuotaItem | None
