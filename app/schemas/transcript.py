from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class WordTimestamp(BaseModel):
    word: str
    start_time: float
    end_time: float
    confidence: float | None = None


class TranscriptItem(BaseModel):
    id: str
    speaker_id: str | None = None
    speaker_label: str | None = None
    content: str
    start_time: float
    end_time: float
    confidence: float | None = None
    words: list[WordTimestamp] | None = None
    sequence: int
    is_edited: bool = False
    original_content: str | None = None
    created_at: datetime
    updated_at: datetime


class TranscriptListResponse(BaseModel):
    task_id: str
    total: int
    items: list[TranscriptItem]
