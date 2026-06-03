"""Notification schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class NotificationResponse(BaseModel):
    """Response schema for a single notification (type + 语言无关 params 形状)。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    type: str
    category: str
    priority: str
    # ORM 物理列名为 extra_data；API/前端暴露为 params。
    params: dict = Field(default_factory=dict, validation_alias="extra_data")
    action_url: str | None = None
    title: str | None = None
    message: str | None = None
    created_at: datetime
    read_at: datetime | None = None


class NotificationStatsResponse(BaseModel):
    """Response schema for notification statistics（纯未读/已读，无 dismissed）。"""

    total: int
    unread: int
