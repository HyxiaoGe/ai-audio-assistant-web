from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class UserProfileResponse(BaseModel):
    id: str
    email: str
    name: str | None = None
    avatar_url: str


class TaskDefaultsPreferences(BaseModel):
    language: Optional[str] = Field(default=None)
    summary_style: Optional[str] = Field(default=None)
    enable_speaker_diarization: Optional[bool] = Field(default=None)
    enable_visual_summary: Optional[bool] = Field(default=None)
    visual_types: Optional[list[str]] = Field(default=None)
    asr_provider: Optional[str] = Field(default=None)
    asr_variant: Optional[str] = Field(default=None)
    llm_provider: Optional[str] = Field(default=None)
    llm_model_id: Optional[str] = Field(default=None)


class UiPreferences(BaseModel):
    locale: Optional[str] = Field(default=None)
    timezone: Optional[str] = Field(default=None)


class NotificationPreferences(BaseModel):
    task_completed: Optional[bool] = Field(default=None)
    task_failed: Optional[bool] = Field(default=None)


class UserPreferencesResponse(BaseModel):
    task_defaults: TaskDefaultsPreferences
    ui: UiPreferences
    notifications: NotificationPreferences


class UserPreferencesUpdateRequest(BaseModel):
    task_defaults: Optional[TaskDefaultsPreferences] = Field(default=None)
    ui: Optional[UiPreferences] = Field(default=None)
    notifications: Optional[NotificationPreferences] = Field(default=None)
