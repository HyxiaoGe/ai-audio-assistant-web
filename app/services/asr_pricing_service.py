"""ASR 定价配置服务

提供 ASR 定价配置的查询功能（只读）。

注意：定价配置只能通过数据库迁移修改，不提供 API 接口修改。
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asr_pricing_config import AsrPricingConfig

logger = logging.getLogger(__name__)


async def get_pricing_config(
    db: AsyncSession,
    provider: str,
    variant: str,
) -> Optional[AsrPricingConfig]:
    """获取指定平台和变体的定价配置

    Args:
        db: 数据库会话
        provider: 服务商 (tencent, aliyun, volcengine)
        variant: 服务变体 (file, file_fast)

    Returns:
        定价配置，未找到返回 None
    """
    result = await db.execute(
        select(AsrPricingConfig)
        .where(AsrPricingConfig.provider == provider)
        .where(AsrPricingConfig.variant == variant)
    )
    return result.scalar_one_or_none()


async def get_all_pricing_configs(
    db: AsyncSession,
    enabled_only: bool = True,
) -> list[AsrPricingConfig]:
    """获取所有定价配置

    Args:
        db: 数据库会话
        enabled_only: 是否只返回已启用的配置

    Returns:
        定价配置列表
    """
    query = select(AsrPricingConfig)
    if enabled_only:
        query = query.where(AsrPricingConfig.is_enabled == True)  # noqa: E712
    query = query.order_by(AsrPricingConfig.provider, AsrPricingConfig.variant)

    result = await db.execute(query)
    return list(result.scalars().all())


async def get_enabled_providers(db: AsyncSession) -> list[tuple[str, str]]:
    """获取所有启用的 provider/variant 组合

    Args:
        db: 数据库会话

    Returns:
        (provider, variant) 元组列表
    """
    result = await db.execute(
        select(AsrPricingConfig.provider, AsrPricingConfig.variant)
        .where(AsrPricingConfig.is_enabled == True)  # noqa: E712
        .order_by(AsrPricingConfig.provider, AsrPricingConfig.variant)
    )
    return list(result.all())


async def get_pricing_configs_with_free_quota(
    db: AsyncSession,
) -> list[AsrPricingConfig]:
    """获取所有有免费额度的定价配置

    Args:
        db: 数据库会话

    Returns:
        有免费额度的配置列表
    """
    result = await db.execute(
        select(AsrPricingConfig)
        .where(AsrPricingConfig.is_enabled == True)  # noqa: E712
        .where(AsrPricingConfig.free_quota_seconds > 0)
        .order_by(AsrPricingConfig.provider, AsrPricingConfig.variant)
    )
    return list(result.scalars().all())
