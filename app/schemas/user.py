from __future__ import annotations

from pydantic import BaseModel, Field


class UserProfileResponse(BaseModel):
    id: str
    email: str
    name: str | None = None
    avatar_url: str | None = None
    is_admin: bool = False


class TaskDefaultsPreferences(BaseModel):
    language: str | None = Field(default=None)
    summary_style: str | None = Field(default=None)
    enable_speaker_diarization: bool | None = Field(default=None)
    enable_visual_summary: bool | None = Field(default=None)
    visual_types: list[str] | None = Field(default=None)
    asr_provider: str | None = Field(default=None)
    asr_variant: str | None = Field(default=None)
    llm_provider: str | None = Field(default=None)
    llm_model_id: str | None = Field(default=None)


class UiPreferences(BaseModel):
    locale: str | None = Field(default=None)
    timezone: str | None = Field(default=None)


class NotificationPreferences(BaseModel):
    task_completed: bool | None = Field(default=None)
    task_failed: bool | None = Field(default=None)


class UserPreferencesResponse(BaseModel):
    task_defaults: TaskDefaultsPreferences
    ui: UiPreferences
    notifications: NotificationPreferences


class UserPreferencesUpdateRequest(BaseModel):
    task_defaults: TaskDefaultsPreferences | None = Field(default=None)
    ui: UiPreferences | None = Field(default=None)
    notifications: NotificationPreferences | None = Field(default=None)
