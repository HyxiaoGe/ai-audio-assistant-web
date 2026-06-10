"""公开探索端点(/api/v1/public/*)的出参 schema。

字段级白名单裁剪:详情不含 error_message/stages/options/source_metadata/
retry_count/request_id/providers/content_hash;转写项不含 words/confidence/is_edited/
original_content;配图项不含 model_id/error。与私有 schema 物理隔离,谁也别 import 谁。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


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
