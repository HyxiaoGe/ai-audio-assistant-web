"""公开探索端点(/api/v1/public/*)的出参 schema。

字段级白名单裁剪:详情不含 error_message/stages/options/source_metadata/
retry_count/request_id/providers/content_hash;转写项不含 words/confidence/is_edited/
original_content;配图项不含 model_id/error。与私有 schema 物理隔离,谁也别 import 谁。

PublicYouTubeInfo 仅透出 YouTube 公开元数据(视频 ID/标题/封面/时长/频道名等),
不含任何用户绑定字段(user_id/subscription_id/last_synced_at/账号凭据)。
封面 URL 由 video_id 推算(YouTube 标准格式),无需调 API。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class PublicYouTubeInfo(BaseModel):
    """公开 YouTube 视频元数据——白名单字段,无任何内部/用户私有信息。

    安全叙事:
    - 透出:video_id/title/thumbnail_url/duration_seconds/channel_id/channel_title
      均为 YouTube 公开内容,封面 URL 由 video_id 推算(i.ytimg.com 标准格式)。
    - 裁掉(私有/无来源):user_id/subscription_id/last_synced_at/access_token/
      refresh_token/description(未缓存)/view_count/like_count/comment_count(未缓存)/
      channel_thumbnail(未缓存)。公开端点无用户上下文,只从 source_url 提取 video_id。
    """

    video_id: str
    title: str | None = None
    thumbnail_url: str | None = None
    duration_seconds: int | None = None
    channel_id: str | None = None
    channel_title: str | None = None


class PublicTaskListItem(BaseModel):
    id: str
    title: str | None
    source_type: str
    duration_seconds: int | None
    detected_language: str | None
    detected_summary_style: str | None = None
    published_at: datetime | None


class PublicTaskDetailResponse(BaseModel):
    id: str
    title: str | None
    source_type: str
    source_url: str | None = None
    audio_url: str | None
    duration_seconds: int | None
    detected_language: str | None
    detected_summary_style: str | None = None
    published_at: datetime | None
    created_at: datetime
    youtube_info: PublicYouTubeInfo | None = None


class PublicTranscriptItem(BaseModel):
    sequence: int
    speaker_id: str | None
    speaker_label: str | None
    content: str
    start_time: float
    end_time: float


class PublicTranscriptListResponse(BaseModel):
    task_id: str
    total: int
    items: list[PublicTranscriptItem]


class PublicSummaryImageItem(BaseModel):
    placeholder: str
    status: str
    url: str | None
    alt: str


class PublicSummaryItem(BaseModel):
    summary_type: str
    version: int
    content: str
    image_url: str | None = None  # 旧式单图(image_key)的代理 URL,与私有 SummaryItem 同语义
    images: list[PublicSummaryImageItem] | None = None
    created_at: datetime


class PublicSummaryListResponse(BaseModel):
    task_id: str
    total: int
    items: list[PublicSummaryItem]
