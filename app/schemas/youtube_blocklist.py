from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BlocklistEntryCreate(BaseModel):
    kind: Literal["term", "channel"]
    value: str = Field(min_length=1, max_length=256)
    note: str | None = Field(default=None, max_length=256)


class BlocklistEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    match_field: str
    raw_value: str
    note: str | None
    created_at: datetime


class BlocklistListOut(BaseModel):
    items: list[BlocklistEntryOut]
