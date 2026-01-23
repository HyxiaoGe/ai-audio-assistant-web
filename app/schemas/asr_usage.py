from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ASRUsageItem(BaseModel):
    """ASR 用量详情"""

    id: str
    user_id: str
    task_id: Optional[str] = None
    provider: str
    variant: str
    external_task_id: Optional[str] = None
    duration_seconds: float
    estimated_cost: float
    actual_cost: Optional[float] = None
    audio_url: Optional[str] = None
    audio_format: Optional[str] = None
    status: str
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    processing_time_ms: Optional[int] = None
    created_at: datetime

    # 免费额度分拆字段
    free_quota_consumed: float = Field(default=0, description="本次消耗的免费额度（秒）")
    paid_duration_seconds: float = Field(default=0, description="本次付费时长（秒）")
    actual_paid_cost: float = Field(default=0, description="本次实际成本（元）")


class ASRUsageListResponse(BaseModel):
    """ASR 用量列表响应"""

    items: list[ASRUsageItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class ASRUsageSummaryItem(BaseModel):
    """ASR 用量汇总项（按提供商）"""

    provider: str
    variant: str
    total_count: int
    success_count: int
    failed_count: int
    total_duration_seconds: float
    total_estimated_cost: float
    total_actual_cost: Optional[float] = None
    avg_processing_time_ms: Optional[float] = None

    # 免费额度分拆汇总
    total_free_quota_consumed: float = Field(default=0, description="总免费额度消耗（秒）")
    total_paid_duration_seconds: float = Field(default=0, description="总付费时长（秒）")
    total_actual_paid_cost: float = Field(default=0, description="总实际成本（元）")


class ASRUsageSummaryResponse(BaseModel):
    """ASR 用量汇总响应"""

    items: list[ASRUsageSummaryItem] = Field(default_factory=list)
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    total_duration_seconds: float = 0.0
    total_estimated_cost: float = 0.0
    total_count: int = 0

    # 免费额度分拆汇总
    total_free_quota_consumed: float = Field(default=0, description="总免费额度消耗（秒）")
    total_paid_duration_seconds: float = Field(default=0, description="总付费时长（秒）")
    total_actual_paid_cost: float = Field(default=0, description="总实际成本（元）")
