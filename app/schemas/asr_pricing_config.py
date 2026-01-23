"""ASR 定价配置 Schema

API 请求和响应的数据模型。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class AsrPricingConfigBase(BaseModel):
    """定价配置基础字段"""

    provider: str = Field(..., description="服务商: tencent, aliyun, volcengine")
    variant: str = Field(..., description="服务变体: file, file_fast")
    cost_per_hour: float = Field(..., ge=0, description="单价（元/小时）")
    free_quota_seconds: float = Field(0, ge=0, description="免费额度（秒）")
    reset_period: str = Field(
        "none",
        description="刷新周期: none, monthly, yearly",
        pattern="^(none|monthly|yearly)$",
    )
    is_enabled: bool = Field(True, description="是否启用")


class AsrPricingConfigCreate(AsrPricingConfigBase):
    """创建定价配置请求"""

    pass


class AsrPricingConfigUpdate(BaseModel):
    """更新定价配置请求（所有字段可选）"""

    cost_per_hour: Optional[float] = Field(None, ge=0, description="单价（元/小时）")
    free_quota_seconds: Optional[float] = Field(None, ge=0, description="免费额度（秒）")
    reset_period: Optional[str] = Field(
        None,
        description="刷新周期: none, monthly, yearly",
        pattern="^(none|monthly|yearly)$",
    )
    is_enabled: Optional[bool] = Field(None, description="是否启用")


class AsrPricingConfigResponse(AsrPricingConfigBase):
    """定价配置响应"""

    id: str
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


class AsrPricingConfigBatchUpdate(BaseModel):
    """批量更新定价配置请求"""

    configs: list[AsrPricingConfigCreate] = Field(
        ..., description="定价配置列表", min_length=1
    )


class AsrPricingConfigListResponse(BaseModel):
    """定价配置列表响应"""

    items: list[AsrPricingConfigResponse]
    total: int
