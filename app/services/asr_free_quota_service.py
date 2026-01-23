"""ASR 免费额度服务

提供免费额度的查询和消耗功能：
1. 查询剩余免费额度
2. 消耗配额（自动区分免费/付费）
3. 自动处理周期刷新

注意：定价配置从数据库 asr_pricing_configs 表读取，不再硬编码。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.asr_free_quota import (
    get_current_period_bounds,
    get_period_type,
)
from app.models.asr_pricing_config import AsrPricingConfig
from app.models.asr_usage_period import AsrUsagePeriod
from app.services.asr_pricing_service import (
    get_pricing_config,
    get_pricing_configs_with_free_quota,
)

logger = logging.getLogger(__name__)


@dataclass
class QuotaConsumptionResult:
    """配额消耗结果"""

    free_consumed: float  # 本次消耗的免费额度（秒）
    paid_consumed: float  # 本次付费时长（秒）
    cost: float  # 本次成本（元）
    remaining_free: float  # 剩余免费额度（秒）


@dataclass
class FreeQuotaStatus:
    """免费额度状态"""

    provider: str
    variant: str
    free_quota_seconds: float  # 总免费额度
    used_seconds: float  # 已使用量
    remaining_seconds: float  # 剩余免费额度
    reset_period: str  # monthly | yearly | none
    period_start: datetime
    period_end: datetime
    cost_per_hour: float


class AsrFreeQuotaService:
    """ASR 免费额度服务"""

    @classmethod
    async def get_pricing_config(
        cls,
        db: AsyncSession,
        provider: str,
        variant: str,
    ) -> Optional[AsrPricingConfig]:
        """获取定价配置

        Args:
            db: 数据库会话
            provider: 服务商
            variant: 服务变体

        Returns:
            定价配置，未找到返回 None
        """
        return await get_pricing_config(db, provider, variant)

    @classmethod
    async def get_or_create_period(
        cls,
        db: AsyncSession,
        provider: str,
        variant: str,
        user_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> AsrUsagePeriod:
        """获取或创建当前周期的用量记录

        如果当前周期不存在记录，会自动创建一条新记录。

        Args:
            db: 数据库会话
            provider: 服务商
            variant: 服务变体
            user_id: 用户ID（NULL 表示全局）
            now: 当前时间

        Returns:
            用量周期记录
        """
        now = now or datetime.now(timezone.utc)
        config = await get_pricing_config(db, provider, variant)

        if not config:
            raise ValueError(f"Unknown provider/variant: {provider}/{variant}")

        period_type = get_period_type(config.reset_period)
        period_start, period_end = get_current_period_bounds(config.reset_period, now)

        # 查询是否存在当前周期的记录
        result = await db.execute(
            select(AsrUsagePeriod).where(
                and_(
                    AsrUsagePeriod.owner_user_id == user_id,
                    AsrUsagePeriod.provider == provider,
                    AsrUsagePeriod.variant == variant,
                    AsrUsagePeriod.period_type == period_type,
                    AsrUsagePeriod.period_start == period_start,
                )
            )
        )
        period = result.scalar_one_or_none()

        if period:
            return period

        # 创建新记录
        period = AsrUsagePeriod(
            owner_user_id=user_id,
            provider=provider,
            variant=variant,
            period_type=period_type,
            period_start=period_start,
            period_end=period_end,
            used_seconds=0,
            free_quota_used=0,
            paid_seconds=0,
            total_cost=0,
        )
        db.add(period)
        await db.flush()

        logger.info(
            "Created new usage period: provider=%s, variant=%s, period=%s, start=%s",
            provider,
            variant,
            period_type,
            period_start.isoformat(),
        )

        return period

    @classmethod
    async def get_remaining_free_quota(
        cls,
        db: AsyncSession,
        provider: str,
        variant: str,
        user_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> float:
        """获取剩余免费额度（秒）

        自动处理周期刷新：
        - 月度周期：每月1日刷新
        - 年度周期：每年1月1日刷新
        - 无周期：返回 0

        Args:
            db: 数据库会话
            provider: 服务商
            variant: 服务变体
            user_id: 用户ID（NULL 表示全局）
            now: 当前时间

        Returns:
            剩余免费额度（秒），无免费额度则返回 0
        """
        config = await get_pricing_config(db, provider, variant)

        if not config or config.free_quota_seconds <= 0:
            return 0

        period = await cls.get_or_create_period(db, provider, variant, user_id, now)
        remaining = max(0, config.free_quota_seconds - period.free_quota_used)

        return remaining

    @classmethod
    async def get_free_quota_status(
        cls,
        db: AsyncSession,
        provider: str,
        variant: str,
        user_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> Optional[FreeQuotaStatus]:
        """获取免费额度完整状态

        Args:
            db: 数据库会话
            provider: 服务商
            variant: 服务变体
            user_id: 用户ID
            now: 当前时间

        Returns:
            免费额度状态，无免费额度的服务返回 None
        """
        config = await get_pricing_config(db, provider, variant)

        if not config or config.free_quota_seconds <= 0:
            return None

        period = await cls.get_or_create_period(db, provider, variant, user_id, now)
        remaining = max(0, config.free_quota_seconds - period.free_quota_used)

        return FreeQuotaStatus(
            provider=provider,
            variant=variant,
            free_quota_seconds=config.free_quota_seconds,
            used_seconds=period.free_quota_used,
            remaining_seconds=remaining,
            reset_period=config.reset_period,
            period_start=period.period_start,
            period_end=period.period_end,
            cost_per_hour=config.cost_per_hour,
        )

    @classmethod
    async def get_all_free_quota_status(
        cls,
        db: AsyncSession,
        user_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> list[FreeQuotaStatus]:
        """获取所有有免费额度的服务状态

        Args:
            db: 数据库会话
            user_id: 用户ID
            now: 当前时间

        Returns:
            免费额度状态列表
        """
        statuses = []

        configs = await get_pricing_configs_with_free_quota(db)
        for config in configs:
            status = await cls.get_free_quota_status(
                db, config.provider, config.variant, user_id, now
            )
            if status:
                statuses.append(status)

        return statuses

    @classmethod
    async def consume_quota(
        cls,
        db: AsyncSession,
        provider: str,
        variant: str,
        duration_seconds: float,
        user_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> QuotaConsumptionResult:
        """消耗配额

        自动区分免费和付费用量：
        1. 优先消耗免费额度
        2. 免费额度用完后计入付费用量
        3. 更新周期统计

        Args:
            db: 数据库会话
            provider: 服务商
            variant: 服务变体
            duration_seconds: 消耗时长（秒）
            user_id: 用户ID
            now: 当前时间

        Returns:
            配额消耗结果
        """
        now = now or datetime.now(timezone.utc)
        config = await get_pricing_config(db, provider, variant)

        if not config:
            raise ValueError(f"Unknown provider/variant: {provider}/{variant}")

        period = await cls.get_or_create_period(db, provider, variant, user_id, now)

        # 计算免费额度剩余量
        remaining_free = max(0, config.free_quota_seconds - period.free_quota_used)

        # 分拆免费和付费
        free_consumed = min(duration_seconds, remaining_free)
        paid_consumed = duration_seconds - free_consumed
        cost = (paid_consumed / 3600) * config.cost_per_hour

        # 更新周期统计
        period.used_seconds += duration_seconds
        period.free_quota_used += free_consumed
        period.paid_seconds += paid_consumed
        period.total_cost += cost

        await db.flush()

        logger.info(
            "Quota consumed: provider=%s, variant=%s, duration=%.1fs, "
            "free=%.1fs, paid=%.1fs, cost=%.4f",
            provider,
            variant,
            duration_seconds,
            free_consumed,
            paid_consumed,
            cost,
        )

        return QuotaConsumptionResult(
            free_consumed=free_consumed,
            paid_consumed=paid_consumed,
            cost=cost,
            remaining_free=remaining_free - free_consumed,
        )

    @classmethod
    async def estimate_cost(
        cls,
        db: AsyncSession,
        provider: str,
        variant: str,
        duration_seconds: float,
        user_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> dict:
        """估算成本（不实际消耗配额）

        Args:
            db: 数据库会话
            provider: 服务商
            variant: 服务变体
            duration_seconds: 预计时长（秒）
            user_id: 用户ID
            now: 当前时间

        Returns:
            成本估算结果
        """
        config = await get_pricing_config(db, provider, variant)

        if not config:
            return {
                "provider": provider,
                "variant": variant,
                "total_duration": duration_seconds,
                "free_consumed": 0,
                "paid_duration": duration_seconds,
                "estimated_cost": 0,
                "full_cost": 0,
                "error": f"Unknown provider/variant: {provider}/{variant}",
            }

        remaining_free = await cls.get_remaining_free_quota(db, provider, variant, user_id, now)

        free_consumed = min(duration_seconds, remaining_free)
        paid_duration = duration_seconds - free_consumed
        estimated_cost = (paid_duration / 3600) * config.cost_per_hour
        full_cost = (duration_seconds / 3600) * config.cost_per_hour

        return {
            "provider": provider,
            "variant": variant,
            "total_duration": duration_seconds,
            "free_consumed": free_consumed,
            "paid_duration": paid_duration,
            "estimated_cost": estimated_cost,
            "full_cost": full_cost,
            "remaining_free_quota": remaining_free,
            "cost_per_hour": config.cost_per_hour,
        }
