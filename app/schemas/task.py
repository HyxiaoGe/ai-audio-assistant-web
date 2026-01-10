from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class TaskOptions(BaseModel):
    language: str = Field(default="auto")
    enable_speaker_diarization: bool = Field(default=True)
    summary_style: str = Field(default="meeting")
    provider: Optional[str] = Field(default=None)
    model_id: Optional[str] = Field(default=None)
    asr_provider: Optional[str] = Field(default=None)
    asr_variant: Optional[str] = Field(default=None)


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


class TaskStageResponse(BaseModel):
    """任务阶段信息"""

    stage_type: str
    status: str
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    error_code: Optional[int]
    error_message: Optional[str]
    attempt: int


class TaskDetailResponse(BaseModel):
    id: str
    title: Optional[str]
    source_type: str
    source_key: Optional[str]
    audio_url: Optional[str]  # 添加音频播放 URL
    status: str
    progress: int
    stage: Optional[str]
    duration_seconds: Optional[int]
    language: Optional[str]
    created_at: datetime
    updated_at: datetime
    error_message: Optional[str]
    stages: list[TaskStageResponse] = Field(default_factory=list)  # 阶段信息


class TaskRetryRequest(BaseModel):
    """任务重试请求"""

    mode: Literal["full", "auto", "from_transcribe", "transcribe_only", "summarize_only"] = Field(
        default="auto"
    )
    """
    重试模式：
    - full: 完整重试（清空所有阶段，从头开始）
    - auto: 智能重试（自动从失败的阶段继续，默认）
    - from_transcribe: 从转写开始（复用下载/上传）
    - transcribe_only: 仅重新转写
    - summarize_only: 仅重新生成摘要
    """


class TaskBatchDeleteRequest(BaseModel):
    """批量删除任务请求."""

    task_ids: list[str] = Field(min_length=1, max_length=50)

    @field_validator("task_ids")
    @classmethod
    def validate_task_ids(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("task_ids cannot be empty")
        if len(v) > 50:
            raise ValueError("Cannot delete more than 50 tasks at once")
        return v
