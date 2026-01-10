from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class WordTimestamp(BaseModel):
    word: str
    start_time: float
    end_time: float
    confidence: Optional[float] = None


class TranscriptItem(BaseModel):
    id: str
    speaker_id: Optional[str] = None
    speaker_label: Optional[str] = None
    content: str
    start_time: float
    end_time: float
    confidence: Optional[float] = None
    words: Optional[list[WordTimestamp]] = None
    sequence: int
    is_edited: bool = False
    original_content: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class TranscriptListResponse(BaseModel):
    task_id: str
    total: int
    items: list[TranscriptItem]
