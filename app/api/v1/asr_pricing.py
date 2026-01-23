"""ASR 定价配置 API

提供 ASR 平台定价配置的管理接口（仅限管理员）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.exceptions import BusinessError
from app.core.response import success
from app.i18n.codes import ErrorCode
from app.models.user import User
from app.schemas.asr_pricing_config import (
    AsrPricingConfigCreate,
    AsrPricingConfigListResponse,
    AsrPricingConfigResponse,
    AsrPricingConfigUpdate,
)
from app.services.asr_pricing_service import (
    create_pricing_config,
    get_all_pricing_configs,
    get_pricing_config,
    update_pricing_config,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/asr/pricing", tags=["ASR Pricing"])


def _check_admin(user: User) -> None:
    """检查用户是否为管理员"""
    if not user.is_admin:
        raise BusinessError(ErrorCode.PERMISSION_DENIED)


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
    return success(
        data=AsrPricingConfigListResponse(items=items, total=len(items)).model_dump()
    )


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


@router.put("/{provider}/{variant}", summary="更新定价配置（管理员）")
async def update_pricing_config_endpoint(
    provider: str,
    variant: str,
    data: AsrPricingConfigUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """更新指定平台和变体的定价配置

    Args:
        provider: 服务商
        variant: 服务变体
        data: 更新数据
        db: 数据库会话
        user: 当前用户（需要管理员权限）

    Returns:
        更新后的定价配置
    """
    _check_admin(user)

    config = await update_pricing_config(
        db,
        provider,
        variant,
        cost_per_hour=data.cost_per_hour,
        free_quota_seconds=data.free_quota_seconds,
        reset_period=data.reset_period,
        is_enabled=data.is_enabled,
    )

    if not config:
        raise BusinessError(ErrorCode.TASK_NOT_FOUND)

    logger.info(
        "Pricing config updated: provider=%s, variant=%s, by user=%s",
        provider,
        variant,
        user.id,
    )

    return success(data=AsrPricingConfigResponse.from_orm_with_computed(config).model_dump())


@router.post("", summary="创建定价配置（管理员）")
async def create_pricing_config_endpoint(
    data: AsrPricingConfigCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """创建新的定价配置

    Args:
        data: 配置数据
        db: 数据库会话
        user: 当前用户（需要管理员权限）

    Returns:
        创建的定价配置
    """
    _check_admin(user)

    # 检查是否已存在
    existing = await get_pricing_config(db, data.provider, data.variant)
    if existing:
        raise BusinessError(ErrorCode.TASK_ALREADY_EXISTS)

    config = await create_pricing_config(
        db,
        provider=data.provider,
        variant=data.variant,
        cost_per_hour=data.cost_per_hour,
        free_quota_seconds=data.free_quota_seconds,
        reset_period=data.reset_period,
        is_enabled=data.is_enabled,
    )

    logger.info(
        "Pricing config created: provider=%s, variant=%s, by user=%s",
        data.provider,
        data.variant,
        user.id,
    )

    return success(data=AsrPricingConfigResponse.from_orm_with_computed(config).model_dump())
