"""ASR 定价配置 API

提供 ASR 平台定价配置的只读查询接口。

注意：定价配置不提供修改接口，只能通过数据库迁移或管理后台修改。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.exceptions import BusinessError
from app.core.response import success
from app.i18n.codes import ErrorCode
from app.models.user import User
from app.schemas.asr_pricing_config import (
    AsrPricingConfigListResponse,
    AsrPricingConfigResponse,
)
from app.services.asr_pricing_service import (
    get_all_pricing_configs,
    get_pricing_config,
)

router = APIRouter(prefix="/asr/pricing", tags=["ASR Pricing"])


@router.get("", summary="获取所有定价配置")
async def list_pricing_configs(
    enabled_only: bool = False,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """获取所有 ASR 定价配置

    Args:
        enabled_only: 是否只返回已启用的配置
        db: 数据库会话
        user: 当前用户

    Returns:
        定价配置列表
    """
    configs = await get_all_pricing_configs(db, enabled_only=enabled_only)
    items = [AsrPricingConfigResponse.from_orm_with_computed(c) for c in configs]
    return success(data=AsrPricingConfigListResponse(items=items, total=len(items)).model_dump())


@router.get("/{provider}/{variant}", summary="获取指定定价配置")
async def get_pricing_config_detail(
    provider: str,
    variant: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """获取指定平台和变体的定价配置

    Args:
        provider: 服务商
        variant: 服务变体
        db: 数据库会话
        user: 当前用户

    Returns:
        定价配置
    """
    config = await get_pricing_config(db, provider, variant)
    if not config:
        raise BusinessError(ErrorCode.TASK_NOT_FOUND)
    return success(data=AsrPricingConfigResponse.from_orm_with_computed(config).model_dump())
