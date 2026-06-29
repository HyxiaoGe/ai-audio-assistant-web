from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AllowlistEntryCreate(BaseModel):
    value: str = Field(min_length=1, max_length=256)
    note: str | None = Field(default=None, max_length=256)


class AllowlistEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    match_field: str
    raw_value: str
    normalized_value: str
    name: str | None = Field(default=None, validation_alias="display_name")
    note: str | None
    created_at: datetime


class AllowlistListOut(BaseModel):
    items: list[AllowlistEntryOut]
