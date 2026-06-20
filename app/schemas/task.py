from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class TaskOptions(BaseModel):
    language: str = Field(default="auto")
    enable_speaker_diarization: bool = Field(default=True)
    summary_style: str = Field(default="auto")
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
    is_public: bool = False


class TaskStatusCountsResponse(BaseModel):
    """任务状态计数（列表页筛选 tab 的角标）。

    与 list_tasks 共用同一伞形分桶规则（processing 覆盖 PROCESSING_STATUSES），
    一次 GROUP BY 返回全部，替代前端为四个 tab 各发一次 page_size=1 查询。
    """

    all: int
    processing: int
    completed: int
    failed: int


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
    # 仅当本次摘要风格由后台自动识别得到时(options.summary_style_auto_detected=True)，
    # 暴露识别出的具体风格供前端展示「AI 识别为：X」；用户显式选风格时为 None。
    detected_summary_style: str | None = None
    # 公开可见性(探索广场);默认值兜底使未触及该特性的旧构造点零改动
    is_public: bool = False
    published_at: datetime | None = None
    # 全链路溯源:本次转写/摘要由哪个 provider/引擎/变体支持。NULL=未捕获(旧任务),前端不显示徽章。
    asr_provider: str | None = None
    asr_engine: str | None = None
    asr_variant: str | None = None
    llm_provider: str | None = None

    @staticmethod
    def detected_summary_style_from_options(options: dict | None) -> str | None:
        """从 task.options 推导只读的 detected_summary_style（None 表示非自动识别）。"""
        opts = options or {}
        if opts.get("summary_style_auto_detected") is True:
            value = opts.get("summary_style")
            return value if isinstance(value, str) else None
        return None


class TaskVisibilityUpdateRequest(BaseModel):
    """更新任务公开可见性(仅管理员,且只能操作本人任务)。"""

    is_public: bool


class TaskVisibilityResponse(BaseModel):
    id: str
    is_public: bool
    published_at: datetime | None


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


class TaskSearchHit(BaseModel):
    """转写全文搜索命中:某任务里命中的一段转写 + 可跳转的时间戳。"""

    task_id: str
    title: str | None = None
    snippet: str  # 含 <mark> 高亮的片段
    start_time: float  # 命中段起始秒,前端用于跳播
    rank: float


class TaskSearchResponse(BaseModel):
    query: str
    hits: list[TaskSearchHit]
