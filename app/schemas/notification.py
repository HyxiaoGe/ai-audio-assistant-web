"""Notification schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ============================================================================
# Request Schemas
# ============================================================================


class NotificationListRequest(BaseModel):
    """Request schema for listing notifications."""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    unread_only: bool = Field(default=False)
    category: Optional[str] = Field(default=None)  # task, system


# ============================================================================
# Response Schemas
# ============================================================================


class NotificationResponse(BaseModel):
    """Response schema for a single notification."""

    id: str
    user_id: str
    task_id: Optional[str] = None

    # Core fields
    category: str  # task, system
    action: str    # completed, failed, progress
    title: str
    message: str
    action_url: Optional[str] = None

    # Status fields
    read_at: Optional[datetime] = None
    dismissed_at: Optional[datetime] = None

    # Extension fields
    extra_data: dict = Field(default_factory=dict)
    priority: str = "normal"
    expires_at: Optional[datetime] = None

    # Timestamps
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class NotificationStatsResponse(BaseModel):
    """Response schema for notification statistics."""

    total: int
    unread: int
    dismissed: int
