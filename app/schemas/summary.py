from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class SummaryItem(BaseModel):
    id: str
    summary_type: str
    version: int
    is_active: bool
    content: str
    model_used: Optional[str] = None
    prompt_version: Optional[str] = None
    token_count: Optional[int] = None
    created_at: datetime


class SummaryListResponse(BaseModel):
    task_id: str
    total: int
    items: list[SummaryItem]
