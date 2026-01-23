"""ASR 免费额度相关 Schema"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class FreeQuotaStatusResponse(BaseModel):
    """免费额度状态响应"""

    provider: str = Field(..., description="服务商")
    variant: str = Field(..., description="服务变体")
    free_quota_seconds: float = Field(..., description="总免费额度（秒）")
    used_seconds: float = Field(..., description="已使用量（秒）")
    remaining_seconds: float = Field(..., description="剩余免费额度（秒）")
    reset_period: str = Field(..., description="刷新周期: monthly, yearly, none")
    period_start: datetime = Field(..., description="当前周期开始时间")
    period_end: datetime = Field(..., description="当前周期结束时间")
    cost_per_hour: float = Field(..., description="超出后单价（元/小时）")

    # 格式化字段
    free_quota_hours: float = Field(default=0, description="总免费额度（小时）")
    used_hours: float = Field(default=0, description="已使用量（小时）")
    remaining_hours: float = Field(default=0, description="剩余免费额度（小时）")
    usage_percent: float = Field(default=0, description="使用率（百分比）")

    class Config:
        from_attributes = True


class FreeQuotaListResponse(BaseModel):
    """免费额度列表响应"""

    providers: list[FreeQuotaStatusResponse] = Field(
        default_factory=list, description="有免费额度的服务列表"
    )


class CostEstimateRequest(BaseModel):
    """成本预估请求"""

    duration_seconds: float = Field(..., gt=0, description="预计时长（秒）")
    variant: str = Field(default="file", description="服务变体: file, file_fast")


class ProviderCostEstimate(BaseModel):
    """单个提供商的成本预估"""

    provider: str = Field(..., description="服务商")
    variant: str = Field(..., description="服务变体")
    total_duration: float = Field(..., description="总时长（秒）")
    free_consumed: float = Field(..., description="消耗免费额度（秒）")
    paid_duration: float = Field(..., description="付费时长（秒）")
    estimated_cost: float = Field(..., description="预估成本（元）")
    full_cost: float = Field(..., description="全价成本（元）")
    remaining_free_quota: float = Field(default=0, description="剩余免费额度（秒）")
    cost_per_hour: float = Field(default=0, description="单价（元/小时）")


class CostEstimateResponse(BaseModel):
    """成本预估响应"""

    estimates: list[ProviderCostEstimate] = Field(
        default_factory=list, description="各提供商成本预估"
    )
    recommended_provider: Optional[str] = Field(None, description="推荐的提供商")
    recommendation_reason: Optional[str] = Field(None, description="推荐原因")


class ProviderScoreResponse(BaseModel):
    """提供商评分响应"""

    provider: str = Field(..., description="服务商")
    variant: str = Field(..., description="服务变体")
    free_quota_score: float = Field(..., description="免费额度得分 (0-1)")
    health_score: float = Field(..., description="健康得分 (0-1)")
    cost_score: float = Field(..., description="成本得分 (0-1)")
    quota_score: float = Field(..., description="用户配额得分 (0-1)")
    total_score: float = Field(..., description="综合得分")
    remaining_free_seconds: float = Field(..., description="剩余免费额度（秒）")


class ProviderScoresResponse(BaseModel):
    """提供商评分列表响应"""

    scores: list[ProviderScoreResponse] = Field(
        default_factory=list, description="提供商评分列表（按总分降序）"
    )
    weights: dict = Field(default_factory=dict, description="权重配置")
