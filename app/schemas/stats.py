from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class TimeRange(BaseModel):
    start: datetime
    end: datetime


class ServiceUsageBreakdown(BaseModel):
    service_type: Literal["asr", "llm"]
    provider: str | None = None
    call_count: int
    success_count: int
    failure_count: int
    pending_count: int
    processing_count: int
    success_rate: float
    failure_rate: float
    avg_stage_seconds: float
    median_stage_seconds: float
    total_audio_duration_seconds: float


class ServiceUsageOverviewResponse(BaseModel):
    time_range: TimeRange
    usage_by_service_type: list[ServiceUsageBreakdown]
    usage_by_provider: list[ServiceUsageBreakdown]
    asr_usage_by_provider: list[ServiceUsageBreakdown]
    llm_usage_by_provider: list[ServiceUsageBreakdown]


class TaskOverviewResponse(BaseModel):
    time_range: TimeRange
    total_tasks: int
    status_distribution: dict[str, int]
    success_rate: float
    failure_rate: float
    avg_processing_time_seconds: float
    median_processing_time_seconds: float
    processing_time_by_stage: dict[str, float]
    total_audio_duration_seconds: float
    total_audio_duration_formatted: str
