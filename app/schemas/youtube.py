"""YouTube OAuth and subscription schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class YouTubeAuthUrlResponse(BaseModel):
    """Response for GET /youtube/auth."""

    auth_url: str


class YouTubeConnectionStatus(BaseModel):
    """Response for GET /youtube/status."""

    connected: bool
    channel_id: Optional[str] = None
    subscription_count: int = 0
    last_synced_at: Optional[datetime] = None
    token_expires_at: Optional[datetime] = None


class YouTubeDisconnectResponse(BaseModel):
    """Response for DELETE /youtube/disconnect."""

    disconnected: bool = True


class YouTubeSubscriptionItem(BaseModel):
    """Single subscription item."""

    channel_id: str
    channel_title: str
    channel_thumbnail: Optional[str] = None
    channel_description: Optional[str] = None
    subscribed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class YouTubeSubscriptionListResponse(BaseModel):
    """Response for GET /youtube/subscriptions."""

    items: list[YouTubeSubscriptionItem]
    total: int
    page: int
    page_size: int


class YouTubeSyncResponse(BaseModel):
    """Response for POST /youtube/subscriptions/sync."""

    task_id: str
    message: str = "Sync started"


class YouTubeSyncResult(BaseModel):
    """Result of sync task."""

    synced_count: int
    message: str
