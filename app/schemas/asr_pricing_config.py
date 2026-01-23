"""ASR 定价配置 Schema

API 响应的数据模型（只读）。

注意：定价配置只支持查询，不提供创建/更新接口。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AsrPricingConfigResponse(BaseModel):
    """定价配置响应"""

    id: str
    provider: str = Field(..., description="服务商: tencent, aliyun, volcengine")
    variant: str = Field(..., description="服务变体: file, file_fast")
    cost_per_hour: float = Field(..., ge=0, description="单价（元/小时）")
    free_quota_seconds: float = Field(0, ge=0, description="免费额度（秒）")
    reset_period: str = Field(
        "none",
        description="刷新周期: none, monthly, yearly",
    )
    is_enabled: bool = Field(True, description="是否启用")
    created_at: datetime
    updated_at: datetime

    # 计算字段
    free_quota_hours: float = Field(0, description="免费额度（小时）")

    class Config:
        from_attributes = True

    @classmethod
    def from_orm_with_computed(cls, obj) -> "AsrPricingConfigResponse":
        """从 ORM 对象创建响应，包含计算字段"""
        return cls(
            id=str(obj.id),
            provider=obj.provider,
            variant=obj.variant,
            cost_per_hour=obj.cost_per_hour,
            free_quota_seconds=obj.free_quota_seconds,
            reset_period=obj.reset_period,
            is_enabled=obj.is_enabled,
            created_at=obj.created_at,
            updated_at=obj.updated_at,
            free_quota_hours=obj.free_quota_seconds / 3600,
        )


class AsrPricingConfigListResponse(BaseModel):
    """定价配置列表响应"""

    items: list[AsrPricingConfigResponse]
    total: int
