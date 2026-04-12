from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class TaskOptions(BaseModel):
    language: str = Field(default="auto")
    enable_speaker_diarization: bool = Field(default=True)
    summary_style: str = Field(default="meeting")
    enable_visual_summary: bool = Field(
        default=False,
        description="是否自动生成可视化摘要（思维导图/时间轴/流程图）",
    )
    visual_types: list[str] = Field(
        default_factory=lambda: ["mindmap"],
        description="要生成的可视化类型列表：mindmap(思维导图), timeline(时间轴), flowchart(流程图)",
    )
    provider: str | None = Field(default=None)
    model_id: str | None = Field(default=None)
    asr_provider: str | None = Field(default=None)
    asr_variant: str | None = Field(default=None)


class TaskCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    source_type: str = Field(min_length=1)
    file_key: str | None = Field(default=None)
    source_url: str | None = Field(default=None)
    content_hash: str | None = Field(default=None)
    options: TaskOptions = Field(default_factory=TaskOptions)


class TaskCreateResponse(BaseModel):
    id: str
    status: str
    progress: int
    created_at: datetime


class TaskListItem(BaseModel):
    id: str
    title: str | None
    source_type: str
    status: str
    progress: int
    duration_seconds: int | None
    created_at: datetime
    updated_at: datetime


class TaskStageResponse(BaseModel):
    """任务阶段信息"""

    stage_type: str
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    error_code: int | None
    error_message: str | None
    attempt: int


class YouTubeVideoInfo(BaseModel):
    """YouTube 视频信息（用于任务详情展示）"""

    video_id: str
    channel_id: str
    channel_title: str | None = None
    channel_thumbnail: str | None = None
    title: str
    description: str | None = None
    thumbnail_url: str | None = None
    published_at: datetime | None = None
    duration_seconds: int | None = None
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None


class TaskDetailResponse(BaseModel):
    id: str
    title: str | None
    source_type: str
    source_key: str | None
    source_url: str | None = None  # YouTube URL
    audio_url: str | None  # 添加音频播放 URL
    status: str
    progress: int
    stage: str | None
    duration_seconds: int | None
    language: str | None
    created_at: datetime
    updated_at: datetime
    error_message: str | None
    stages: list[TaskStageResponse] = Field(default_factory=list)  # 阶段信息
    youtube_info: YouTubeVideoInfo | None = None  # YouTube 视频元数据


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
