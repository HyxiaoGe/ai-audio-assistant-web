"""Summary style schemas for API responses."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class SummaryStyleItem(BaseModel):
    """Single summary style item."""

    id: str = Field(description="Style identifier")
    name: str = Field(description="Display name (i18n)")
    description: str = Field(description="Style description (i18n)")
    focus: str = Field(description="Summary focus points (i18n)")
    icon: Optional[str] = Field(default=None, description="Icon identifier")
    recommended_visual_types: list[str] = Field(
        default_factory=list,
        description="Recommended visual summary types",
    )


class SummaryStyleListResponse(BaseModel):
    """Summary styles list response."""

    version: str = Field(description="Configuration version")
    styles: list[SummaryStyleItem]
