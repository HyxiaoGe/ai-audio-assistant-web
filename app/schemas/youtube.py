"""YouTube OAuth and subscription schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class YouTubeAuthUrlResponse(BaseModel):
    """Response for GET /youtube/auth."""

    auth_url: str


class YouTubeConnectionStatus(BaseModel):
    """Response for GET /youtube/status."""

    connected: bool
    channel_id: str | None = None
    subscription_count: int = 0
    last_synced_at: datetime | None = None
    token_expires_at: datetime | None = None
    needs_reauth: bool = False  # True if refresh token expired and user needs to reconnect


class YouTubeDisconnectResponse(BaseModel):
    """Response for DELETE /youtube/disconnect."""

    disconnected: bool = True


class YouTubeSubscriptionItem(BaseModel):
    """Single subscription item."""

    channel_id: str
    channel_title: str
    channel_thumbnail: str | None = None
    channel_description: str | None = None
    subscribed_at: datetime | None = None
    # Customization fields
    is_hidden: bool = False
    sync_enabled: bool = True
    is_starred: bool = False
    auto_transcribe: bool = False
    video_count: int = 0

    class Config:
        from_attributes = True


class YouTubeSubscriptionListResponse(BaseModel):
    """Response for GET /youtube/subscriptions."""

    items: list[YouTubeSubscriptionItem]
    total: int
    page: int
    page_size: int


class SubscriptionSettingsUpdate(BaseModel):
    """Request for PATCH /youtube/subscriptions/{channel_id}/settings."""

    is_hidden: bool | None = None
    sync_enabled: bool | None = None
    is_starred: bool | None = None
    auto_transcribe: bool | None = None
    auto_transcribe_max_duration: int | None = Field(
        None, ge=60, le=43200, description="Max duration in seconds (1min - 12hours)"
    )
    auto_transcribe_language: str | None = Field(None, max_length=10, description="Language code for transcription")


class SubscriptionSettingsResponse(BaseModel):
    """Response for GET/PATCH /youtube/subscriptions/{channel_id}/settings."""

    channel_id: str
    channel_title: str
    is_hidden: bool
    sync_enabled: bool
    is_starred: bool
    auto_transcribe: bool
    auto_transcribe_max_duration: int | None = None
    auto_transcribe_language: str | None = None
    avg_publish_interval_hours: float | None = None
    next_sync_at: datetime | None = None


class BatchStarRequest(BaseModel):
    """Request for POST /youtube/subscriptions/batch/star."""

    channel_ids: list[str] = Field(..., min_length=1, max_length=100)
    is_starred: bool


class BatchAutoTranscribeRequest(BaseModel):
    """Request for POST /youtube/subscriptions/batch/auto-transcribe."""

    channel_ids: list[str] = Field(..., min_length=1, max_length=100)
    auto_transcribe: bool
    max_duration: int | None = Field(None, ge=60, le=43200, description="Max duration in seconds")
    language: str | None = Field(None, max_length=10)


class BatchUpdateResponse(BaseModel):
    """Response for batch update operations."""

    updated_count: int
    message: str


class YouTubeSyncResponse(BaseModel):
    """Response for POST /youtube/subscriptions/sync."""

    task_id: str
    message: str = "Sync started"


class YouTubeSyncResult(BaseModel):
    """Result of sync task."""

    synced_count: int
    message: str


class YouTubeVideoItem(BaseModel):
    """Single video item."""

    video_id: str
    channel_id: str
    title: str
    description: str | None = None
    thumbnail_url: str | None = None
    published_at: datetime
    duration_seconds: int | None = None
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    # Transcription status (populated when querying)
    transcribed: bool = False
    task_id: str | None = None

    class Config:
        from_attributes = True


class YouTubeVideoListResponse(BaseModel):
    """Response for GET /youtube/channels/{channel_id}/videos."""

    items: list[YouTubeVideoItem]
    total: int
    page: int
    page_size: int
    last_synced_at: datetime | None = None
    syncing: bool = False  # True if background sync was triggered


class YouTubeChannelSyncStatus(BaseModel):
    """Channel sync status."""

    subscribed: bool
    channel_title: str | None = None
    video_count: int = 0
    last_synced_at: datetime | None = None


class YouTubeTranscribeRequest(BaseModel):
    """Request for POST /youtube/videos/{video_id}/transcribe."""

    # Optional parameters for task creation
    language: str | None = None
    output_format: str | None = None


class YouTubeTaskStatusResponse(BaseModel):
    """Response for GET /youtube/tasks/{task_id}/status."""

    task_id: str
    status: str  # pending, started, success, failure, revoked
    result: dict | None = None
    error: str | None = None


class YouTubeSyncOverview(BaseModel):
    """Response for GET /youtube/sync-overview.

    Provides an overview of the sync status across all subscriptions.
    """

    # Subscription counts
    total_subscriptions: int
    synced_subscriptions: int  # subscriptions with videos_synced_at set
    pending_subscriptions: int  # subscriptions never synced

    # Video counts
    total_videos: int
    channels_with_videos: int  # unique channels with at least one video cached

    # Status
    fully_synced: bool  # True if all subscriptions have been synced at least once
    last_sync_at: datetime | None = None  # most recent videos_synced_at


class StarredVideosResponse(BaseModel):
    """Response for GET /youtube/videos/starred."""

    items: list[YouTubeVideoItem]
    total: int
    page: int
    page_size: int
    starred_channels_count: int  # Number of starred channels
