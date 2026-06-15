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
    cover_url: str | None = None  # 首张 ready 摘要配图的 OSS 直链(无则 None);封面统一用配图,不用 YouTube 缩略图
    excerpt: str | None = None  # active overview 摘要正文前 ~80 字(剥 markdown);无则 None


class PublicTaskDetailResponse(BaseModel):
    id: str
    title: str | None
    source_type: str
    source_url: str | None = None
    audio_url: str | None
    # OSS 预签名直链(3600s,与媒体端点 307 路径同 TTL):浏览器直连 OSS 取音频字节,
    # 绕开同源代理/隧道。前端优先用它、失败回落 audio_url 代理路径;无音频/签发失败为 None。
    # 安全面:签发面从「点播放时(307)」扩大到「每次详情浏览」,同 TTL 同类残余暴露
    # (取消公开后已签出 URL 残余有效 ≤TTL),刻意接受——公开资格每次请求过 is_public DB 复核。
    audio_direct_url: str | None = None
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
    # url 为 OSS 预签名直链(600s)时的同源代理回落路径:直链过期(长开页面 >TTL)403 后,
    # 前端切到该路径走媒体票链路自愈。url 本身已是代理形态(换发直链失败回落)时为 None。
    proxy_url: str | None = None


class PublicSummaryItem(BaseModel):
    summary_type: str
    version: int
    content: str
    # 旧式单图(image_key):优先 OSS 预签名直链(600s,绕开隧道),签发失败回落
    # /api/v1/media 代理 URL;字段语义与私有 SummaryItem 对应(私有侧恒为代理 URL,刻意不动)
    image_url: str | None = None
    images: list[PublicSummaryImageItem] | None = None
    created_at: datetime


class PublicSummaryListResponse(BaseModel):
    task_id: str
    total: int
    items: list[PublicSummaryItem]
