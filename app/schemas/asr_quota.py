from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class AsrQuotaItem(BaseModel):
    provider: str
    variant: str
    window_type: Literal["day", "month", "total"]
    window_start: datetime
    window_end: datetime
    quota_seconds: float
    used_seconds: float
    status: str


class AsrQuotaListResponse(BaseModel):
    items: list[AsrQuotaItem] = Field(default_factory=list)


class AsrUserFreeQuotaResponse(BaseModel):
    """用户免费额度响应"""

    free_quota_seconds: float      # 总免费额度（秒），-1 表示无限制
    free_quota_hours: float        # 总免费额度（小时），-1 表示无限制
    used_seconds: float            # 已消耗（秒）
    used_hours: float              # 已消耗（小时）
    remaining_seconds: float       # 剩余免费额度（秒），-1 表示无限制
    remaining_hours: float         # 剩余免费额度（小时），-1 表示无限制
    is_unlimited: bool = False     # 是否不受配额限制（管理员）


class AsrQuotaUpsertRequest(BaseModel):
    provider: str = Field(min_length=1)
    variant: str = Field(default="file", min_length=1)
    window_type: Literal["day", "month", "total"]
    quota_seconds: float | None = Field(default=None, gt=0)
    quota_hours: float | None = Field(default=None, gt=0)
    window_start: datetime | None = None
    window_end: datetime | None = None
    used_seconds: float | None = Field(default=None, ge=0)
    reset: bool = Field(default=True)

    @model_validator(mode="after")
    def _ensure_quota(self) -> "AsrQuotaUpsertRequest":
        if self.quota_seconds is None and self.quota_hours is None:
            raise ValueError("quota_seconds or quota_hours is required")
        if self.window_start or self.window_end:
            if self.window_start is None or self.window_end is None:
                raise ValueError("window_start and window_end must be provided together")
            if self.window_type != "total":
                raise ValueError("window_start/window_end only supported for total quotas")
            if self.window_end <= self.window_start:
                raise ValueError("window_end must be greater than window_start")
        if self.used_seconds is not None and self.used_seconds < 0:
            raise ValueError("used_seconds must be >= 0")
        return self


class AsrQuotaUpsertResponse(BaseModel):
    item: AsrQuotaItem | None


# ============ 管理员概览 ============

class AsrFreeQuotaStatus(BaseModel):
    """免费额度状态（只关心免费额度本身）"""

    provider: str
    variant: str
    display_name: str                 # 显示名称，如"腾讯云 录音文件"
    free_quota_hours: float           # 免费额度（小时）
    used_hours: float                 # 已使用（小时）
    remaining_hours: float            # 剩余（小时）
    usage_percent: float              # 使用百分比 0-100
    reset_period: str                 # monthly/yearly
    period_start: datetime            # 当前周期开始
    period_end: datetime              # 当前周期结束


class AsrProviderUsage(BaseModel):
    """提供商付费使用统计（所有提供商）"""

    provider: str
    variant: str
    display_name: str                 # 显示名称
    cost_per_hour: float              # 单价（元/小时）
    paid_hours: float                 # 付费时长（小时）
    paid_cost: float                  # 付费金额（元）
    is_enabled: bool                  # 是否启用


class AsrUsageSummary(BaseModel):
    """ASR 使用量汇总"""

    total_used_hours: float           # 总使用量（小时）
    total_free_hours: float           # 免费额度消耗（小时）
    total_paid_hours: float           # 付费时长（小时）
    total_cost: float                 # 总成本（元）


class AsrAdminOverviewResponse(BaseModel):
    """管理员 ASR 概览响应"""

    summary: AsrUsageSummary
    free_quota_status: list[AsrFreeQuotaStatus]   # 免费额度状态
    providers_usage: list[AsrProviderUsage]       # 所有提供商的付费使用统计
