from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ConfigUpdateRequest(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = Field(default=True)
    note: str | None = Field(default=None)


class ConfigRollbackRequest(BaseModel):
    version: int | None = Field(default=None, ge=1)
    note: str | None = Field(default=None)
