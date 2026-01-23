"""ASR 定价配置服务

提供 ASR 定价配置的查询和管理功能。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

from sqlalchemy import select, update
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


async def update_pricing_config(
    db: AsyncSession,
    provider: str,
    variant: str,
    cost_per_hour: Optional[float] = None,
    free_quota_seconds: Optional[float] = None,
    reset_period: Optional[str] = None,
    is_enabled: Optional[bool] = None,
) -> Optional[AsrPricingConfig]:
    """更新定价配置

    Args:
        db: 数据库会话
        provider: 服务商
        variant: 服务变体
        cost_per_hour: 新单价（可选）
        free_quota_seconds: 新免费额度（可选）
        reset_period: 新刷新周期（可选）
        is_enabled: 是否启用（可选）

    Returns:
        更新后的配置，未找到返回 None
    """
    # 构建更新字段
    update_data: dict[str, float | str | bool] = {}
    if cost_per_hour is not None:
        update_data["cost_per_hour"] = cost_per_hour
    if free_quota_seconds is not None:
        update_data["free_quota_seconds"] = free_quota_seconds
    if reset_period is not None:
        if reset_period not in ("none", "monthly", "yearly"):
            raise ValueError(f"Invalid reset_period: {reset_period}")
        update_data["reset_period"] = reset_period
    if is_enabled is not None:
        update_data["is_enabled"] = is_enabled

    if not update_data:
        # 没有需要更新的字段
        return await get_pricing_config(db, provider, variant)

    # 执行更新
    await db.execute(
        update(AsrPricingConfig)
        .where(AsrPricingConfig.provider == provider)
        .where(AsrPricingConfig.variant == variant)
        .values(**update_data)
    )
    await db.commit()

    # 返回更新后的配置
    return await get_pricing_config(db, provider, variant)


async def create_pricing_config(
    db: AsyncSession,
    provider: str,
    variant: str,
    cost_per_hour: float,
    free_quota_seconds: float = 0,
    reset_period: str = "none",
    is_enabled: bool = True,
) -> AsrPricingConfig:
    """创建新的定价配置

    Args:
        db: 数据库会话
        provider: 服务商
        variant: 服务变体
        cost_per_hour: 单价
        free_quota_seconds: 免费额度
        reset_period: 刷新周期
        is_enabled: 是否启用

    Returns:
        新创建的配置
    """
    if reset_period not in ("none", "monthly", "yearly"):
        raise ValueError(f"Invalid reset_period: {reset_period}")

    config = AsrPricingConfig(
        provider=provider,
        variant=variant,
        cost_per_hour=cost_per_hour,
        free_quota_seconds=free_quota_seconds,
        reset_period=reset_period,
        is_enabled=is_enabled,
    )
    db.add(config)
    await db.commit()
    await db.refresh(config)

    logger.info(
        "Created ASR pricing config: provider=%s, variant=%s, cost=%.2f/h",
        provider,
        variant,
        cost_per_hour,
    )

    return config


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


# 用于缓存定价配置的工具函数
@lru_cache(maxsize=32)
def _get_cost_per_hour_cached(provider: str, variant: str) -> Optional[float]:
    """缓存的定价查询（用于同步上下文）

    注意：这个缓存需要在配置更新时清理
    """
    # 这个函数只是占位符，实际使用时应该从数据库获取
    # 缓存在启动时填充
    return None


def clear_pricing_cache() -> None:
    """清理定价缓存

    在更新定价配置后调用此函数
    """
    _get_cost_per_hour_cached.cache_clear()
    logger.info("ASR pricing cache cleared")
