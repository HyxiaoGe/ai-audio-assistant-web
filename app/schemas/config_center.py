from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ConfigUpdateRequest(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = Field(default=True)
    note: Optional[str] = Field(default=None)


class ConfigRollbackRequest(BaseModel):
    version: Optional[int] = Field(default=None, ge=1)
    note: Optional[str] = Field(default=None)
