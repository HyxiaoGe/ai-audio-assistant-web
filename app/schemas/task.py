from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class TaskOptions(BaseModel):
    language: str = Field(default="auto")
    enable_speaker_diarization: bool = Field(default=True)
    summary_style: str = Field(default="meeting")


class TaskCreateRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=500)
    source_type: str = Field(min_length=1)
    file_key: Optional[str] = Field(default=None)
    source_url: Optional[str] = Field(default=None)
    content_hash: Optional[str] = Field(default=None)
    options: TaskOptions = Field(default_factory=TaskOptions)


class TaskCreateResponse(BaseModel):
    id: str
    status: str
    progress: int
    created_at: datetime


class TaskListItem(BaseModel):
    id: str
    title: Optional[str]
    source_type: str
    status: str
    progress: int
    duration_seconds: Optional[int]
    created_at: datetime
    updated_at: datetime


class TaskDetailResponse(BaseModel):
    id: str
    title: Optional[str]
    source_type: str
    source_key: Optional[str]
    status: str
    progress: int
    stage: Optional[str]
    duration_seconds: Optional[int]
    language: Optional[str]
    created_at: datetime
    updated_at: datetime
    error_message: Optional[str]
