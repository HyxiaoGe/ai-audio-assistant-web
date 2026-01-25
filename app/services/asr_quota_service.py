from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.asr_pricing_config import AsrPricingConfig
from app.models.asr_usage import ASRUsage
from app.models.asr_usage_period import AsrUsagePeriod
from app.models.asr_user_quota import AsrUserQuota


@dataclass(frozen=True)
class QuotaWindow:
    start: datetime
    end: datetime


"""ASR 用户配额服务

管理用户的 ASR 使用配额限制。

注意：这是用户配额限制服务，与平台定价（AsrPricingConfig）是独立的概念。
用户配额用于限制用户在某个时间窗口内的 ASR 使用量。
"""


def _extract_scalars(result: object) -> list[AsrUserQuota]:
    scalars = getattr(result, "scalars", None)
    if scalars is None:
        return []
    return scalars().all()


async def _execute(session: Session | AsyncSession, stmt: object) -> object:
    result = session.execute(stmt)
    if inspect.isawaitable(result):
        return await result
    return result


async def _commit(session: Session | AsyncSession) -> None:
    result = session.commit()
    if inspect.isawaitable(result):
        await result


def _window_bounds(
    now: datetime,
    window_type: str,
    window_start: Optional[datetime] = None,
    window_end: Optional[datetime] = None,
) -> QuotaWindow:
    if window_type == "day":
        start = datetime(now.year, now.month, now.day, tzinfo=now.tzinfo or timezone.utc)
        end = start.replace(hour=23, minute=59, second=59, microsecond=999999)
        return QuotaWindow(start=start, end=end)
    if window_type == "month":
        start = datetime(now.year, now.month, 1, tzinfo=now.tzinfo or timezone.utc)
        if now.month == 12:
            end = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc) - timedelta(microseconds=1)
        else:
            end = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc) - timedelta(
                microseconds=1
            )
        return QuotaWindow(start=start, end=end)
    if window_type == "total":
        if window_start and window_end:
            return QuotaWindow(start=window_start, end=window_end)
        start = datetime(1970, 1, 1, tzinfo=timezone.utc)
        end = datetime(2099, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)
        return QuotaWindow(start=start, end=end)
    raise ValueError(f"Unsupported window_type: {window_type}")


def _is_available(quota: AsrUserQuota) -> bool:
    if quota.status == "exhausted":
        return False
    if quota.quota_seconds <= 0:
        return False
    return quota.used_seconds < quota.quota_seconds


def _active_window_clause(now: datetime) -> object:
    return and_(AsrUserQuota.window_start <= now, AsrUserQuota.window_end >= now)


QuotaKey = tuple[str, str]


def _effective_quotas(
    rows: list[AsrUserQuota],
    keys: list[QuotaKey],
    owner_user_id: Optional[str],
) -> dict[QuotaKey, list[AsrUserQuota]]:
    user_map: dict[QuotaKey, list[AsrUserQuota]] = {}
    global_map: dict[QuotaKey, list[AsrUserQuota]] = {}
    for row in rows:
        key = (row.provider, row.variant)
        if row.owner_user_id:
            user_map.setdefault(key, []).append(row)
        else:
            global_map.setdefault(key, []).append(row)

    effective: dict[QuotaKey, list[AsrUserQuota]] = {}
    for key in keys:
        if owner_user_id and key in user_map:
            effective[key] = user_map[key]
        elif key in global_map:
            effective[key] = global_map[key]
    return effective


def select_available_provider_sync(
    session: Session,
    providers: Iterable[str],
    owner_user_id: Optional[str] = None,
    variant: str = "file",
    now: Optional[datetime] = None,
) -> list[str]:
    now = now or datetime.now(timezone.utc)
    provider_list = [p for p in providers if isinstance(p, str)]
    if not provider_list:
        return []

    rows = _extract_scalars(
        session.execute(
            select(AsrUserQuota)
            .where(AsrUserQuota.provider.in_(provider_list))
            .where(AsrUserQuota.variant == variant)
            .where(_active_window_clause(now))
            .where(
                or_(
                    AsrUserQuota.owner_user_id.is_(None),
                    AsrUserQuota.owner_user_id == owner_user_id,
                )
            )
        )
    )

    if not rows:
        return []

    keys = [(provider, variant) for provider in provider_list]
    quotas_by_key = _effective_quotas(rows, keys, owner_user_id)

    available: list[str] = []
    for (provider, _variant), quotas in quotas_by_key.items():
        if all(_is_available(q) for q in quotas):
            available.append(provider)

    return available


def get_quota_providers_sync(
    session: Session,
    providers: Iterable[str],
    owner_user_id: Optional[str] = None,
    variant: str = "file",
    now: Optional[datetime] = None,
) -> set[str]:
    now = now or datetime.now(timezone.utc)
    provider_list = [p for p in providers if isinstance(p, str)]
    if not provider_list:
        return set()

    rows = _extract_scalars(
        session.execute(
            select(AsrUserQuota)
            .where(AsrUserQuota.provider.in_(provider_list))
            .where(AsrUserQuota.variant == variant)
            .where(_active_window_clause(now))
            .where(
                or_(
                    AsrUserQuota.owner_user_id.is_(None),
                    AsrUserQuota.owner_user_id == owner_user_id,
                )
            )
        )
    )
    keys = [(provider, variant) for provider in provider_list]
    effective = _effective_quotas(rows, keys, owner_user_id)
    return {provider for (provider, _variant) in effective.keys()}


def check_any_provider_available_sync(
    session: Session,
    providers: Iterable[str],
    owner_user_id: Optional[str] = None,
    variant: str = "file",
    now: Optional[datetime] = None,
) -> tuple[bool, list[str]]:
    """Check if any ASR provider has available quota (sync version).

    For use in Celery worker tasks.

    Args:
        session: Database session (sync)
        providers: List of provider names to check
        owner_user_id: User ID
        variant: ASR variant (file, file_fast)
        now: Current time

    Returns:
        (is_available, list of available providers)
    """
    provider_list = [p for p in providers if isinstance(p, str)]
    if not provider_list:
        return False, []

    available = select_available_provider_sync(
        session, provider_list, owner_user_id, variant=variant, now=now
    )

    # If no quota records, all providers are available (no quota limits)
    quota_providers = get_quota_providers_sync(
        session, provider_list, owner_user_id, variant=variant, now=now
    )

    if not quota_providers:
        # No quota records, all providers are available
        return True, provider_list

    if available:
        return True, available

    # Check for providers without quota configured (unlimited)
    unlimited_providers = [p for p in provider_list if p not in quota_providers]
    if unlimited_providers:
        return True, unlimited_providers

    return False, []


def record_usage_sync(
    session: Session,
    provider: str,
    duration_seconds: float,
    owner_user_id: Optional[str] = None,
    variant: str = "file",
    now: Optional[datetime] = None,
) -> None:
    if not provider or duration_seconds <= 0:
        return

    now = now or datetime.now(timezone.utc)
    rows = _extract_scalars(
        session.execute(
            select(AsrUserQuota)
            .where(AsrUserQuota.provider == provider)
            .where(AsrUserQuota.variant == variant)
            .where(_active_window_clause(now))
            .where(
                or_(
                    AsrUserQuota.owner_user_id.is_(None),
                    AsrUserQuota.owner_user_id == owner_user_id,
                )
            )
        )
    )

    if not rows:
        return

    key = (provider, variant)
    effective = _effective_quotas(rows, [key], owner_user_id).get(key, [])
    for row in effective:
        new_used = row.used_seconds + duration_seconds
        status = "exhausted" if new_used >= row.quota_seconds else row.status
        session.execute(
            update(AsrUserQuota)
            .where(AsrUserQuota.id == row.id)
            .values(used_seconds=new_used, status=status)
        )

    session.commit()


async def select_available_provider(
    session: Session | AsyncSession,
    providers: Iterable[str],
    owner_user_id: Optional[str] = None,
    variant: str = "file",
    now: Optional[datetime] = None,
) -> list[str]:
    now = now or datetime.now(timezone.utc)
    provider_list = [p for p in providers if isinstance(p, str)]
    if not provider_list:
        return []

    result = await _execute(
        session,
        select(AsrUserQuota)
        .where(AsrUserQuota.provider.in_(provider_list))
        .where(AsrUserQuota.variant == variant)
        .where(_active_window_clause(now))
        .where(
            or_(AsrUserQuota.owner_user_id.is_(None), AsrUserQuota.owner_user_id == owner_user_id)
        ),
    )
    rows = _extract_scalars(result)

    if not rows:
        return []

    keys = [(provider, variant) for provider in provider_list]
    quotas_by_key = _effective_quotas(rows, keys, owner_user_id)

    available: list[str] = []
    for (provider, _variant), quotas in quotas_by_key.items():
        if all(_is_available(q) for q in quotas):
            available.append(provider)

    return available


async def get_quota_providers(
    session: Session | AsyncSession,
    providers: Iterable[str],
    owner_user_id: Optional[str] = None,
    variant: str = "file",
    now: Optional[datetime] = None,
) -> set[str]:
    now = now or datetime.now(timezone.utc)
    provider_list = [p for p in providers if isinstance(p, str)]
    if not provider_list:
        return set()

    result = await _execute(
        session,
        select(AsrUserQuota)
        .where(AsrUserQuota.provider.in_(provider_list))
        .where(AsrUserQuota.variant == variant)
        .where(_active_window_clause(now))
        .where(
            or_(AsrUserQuota.owner_user_id.is_(None), AsrUserQuota.owner_user_id == owner_user_id)
        ),
    )
    rows = _extract_scalars(result)
    keys = [(provider, variant) for provider in provider_list]
    effective = _effective_quotas(rows, keys, owner_user_id)
    return {provider for (provider, _variant) in effective.keys()}


async def record_usage(
    session: Session | AsyncSession,
    provider: str,
    duration_seconds: float,
    owner_user_id: Optional[str] = None,
    variant: str = "file",
    now: Optional[datetime] = None,
) -> None:
    if not provider or duration_seconds <= 0:
        return

    now = now or datetime.now(timezone.utc)
    result = await _execute(
        session,
        select(AsrUserQuota)
        .where(AsrUserQuota.provider == provider)
        .where(AsrUserQuota.variant == variant)
        .where(_active_window_clause(now))
        .where(
            or_(AsrUserQuota.owner_user_id.is_(None), AsrUserQuota.owner_user_id == owner_user_id)
        ),
    )
    rows = _extract_scalars(result)

    if not rows:
        return

    key = (provider, variant)
    effective = _effective_quotas(rows, [key], owner_user_id).get(key, [])
    for row in effective:
        new_used = row.used_seconds + duration_seconds
        status = "exhausted" if new_used >= row.quota_seconds else row.status
        await _execute(
            session,
            update(AsrUserQuota)
            .where(AsrUserQuota.id == row.id)
            .values(used_seconds=new_used, status=status),
        )

    await _commit(session)


async def list_effective_quotas(
    db: AsyncSession,
    owner_user_id: Optional[str],
    now: Optional[datetime] = None,
) -> list[AsrUserQuota]:
    now = now or datetime.now(timezone.utc)
    result = await db.execute(
        select(AsrUserQuota)
        .where(_active_window_clause(now))
        .where(
            or_(AsrUserQuota.owner_user_id.is_(None), AsrUserQuota.owner_user_id == owner_user_id)
        )
    )
    rows = result.scalars().all()
    keys = sorted({(row.provider, row.variant) for row in rows})
    effective = _effective_quotas(rows, keys, owner_user_id)
    merged: list[AsrUserQuota] = []
    for key in keys:
        merged.extend(effective.get(key, []))
    return merged


async def list_global_quotas(
    db: AsyncSession,
    now: Optional[datetime] = None,
) -> list[AsrUserQuota]:
    now = now or datetime.now(timezone.utc)
    result = await db.execute(
        select(AsrUserQuota)
        .where(_active_window_clause(now))
        .where(AsrUserQuota.owner_user_id.is_(None))
    )
    return result.scalars().all()


async def check_any_provider_available(
    session: Session | AsyncSession,
    owner_user_id: Optional[str] = None,
    variant: str = "file",
    now: Optional[datetime] = None,
) -> tuple[bool, list[str]]:
    """检查是否有任意可用的 ASR 提供商

    用于任务创建时的预检机制。

    Args:
        session: 数据库会话
        owner_user_id: 用户 ID（可选）
        variant: ASR 变体 (file, file_fast)
        now: 当前时间

    Returns:
        (是否有可用提供商, 可用提供商列表)
    """
    from app.core.registry import ServiceRegistry

    all_providers = ServiceRegistry.list_services("asr")
    if not all_providers:
        return False, []

    available = await select_available_provider(
        session, all_providers, owner_user_id, variant=variant, now=now
    )

    # 如果没有配额记录，则所有提供商都可用（无配额限制）
    quota_providers = await get_quota_providers(
        session, all_providers, owner_user_id, variant=variant, now=now
    )

    if not quota_providers:
        # 没有配额记录，所有提供商都可用
        return True, all_providers

    if available:
        return True, available

    # 检查是否有未配置配额的提供商（这些提供商无限制）
    unlimited_providers = [p for p in all_providers if p not in quota_providers]
    if unlimited_providers:
        return True, unlimited_providers

    return False, []


async def upsert_quota(
    db: AsyncSession,
    provider: str,
    variant: str,
    window_type: str,
    quota_seconds: float,
    reset: bool,
    owner_user_id: Optional[str],
    window_start: Optional[datetime] = None,
    window_end: Optional[datetime] = None,
    used_seconds: Optional[float] = None,
    now: Optional[datetime] = None,
) -> AsrUserQuota:
    now = now or datetime.now(timezone.utc)
    window = _window_bounds(now, window_type, window_start=window_start, window_end=window_end)

    stmt = select(AsrUserQuota).where(
        AsrUserQuota.provider == provider,
        AsrUserQuota.variant == variant,
        AsrUserQuota.window_type == window_type,
        AsrUserQuota.window_start == window.start,
        AsrUserQuota.owner_user_id == owner_user_id,
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()

    if existing:
        if used_seconds is not None:
            used = used_seconds
            status = "exhausted" if used >= quota_seconds else "active"
        else:
            used = 0 if reset else existing.used_seconds
            status = "active" if reset else existing.status
        existing.quota_seconds = quota_seconds
        existing.used_seconds = used
        existing.status = status
        existing.window_end = window.end
        await db.commit()
        await db.refresh(existing)
        return existing

    used = used_seconds or 0
    status = "exhausted" if used >= quota_seconds else "active"
    new_row = AsrUserQuota(
        owner_user_id=owner_user_id,
        provider=provider,
        variant=variant,
        window_type=window_type,
        window_start=window.start,
        window_end=window.end,
        quota_seconds=quota_seconds,
        used_seconds=used,
        status=status,
    )
    db.add(new_row)
    await db.commit()
    await db.refresh(new_row)
    return new_row


@dataclass
class UsageSummaryByVariant:
    """按 variant 汇总的使用量"""

    variant: str
    used_seconds: float
    estimated_cost: float


# 已知的 ASR variant 类型
KNOWN_VARIANTS = ["file", "file_fast"]


async def get_user_usage_summary(
    db: AsyncSession,
    user_id: str,
) -> list[UsageSummaryByVariant]:
    """获取用户的 ASR 使用量汇总（按 variant 聚合所有平台）

    Args:
        db: 数据库会话
        user_id: 用户 ID

    Returns:
        按 variant 汇总的使用量列表，包含所有已知 variant（即使消耗为 0）
    """
    result = await db.execute(
        select(
            ASRUsage.variant,
            func.sum(ASRUsage.duration_seconds).label("total_seconds"),
            func.sum(ASRUsage.estimated_cost).label("total_cost"),
        )
        .where(ASRUsage.user_id == user_id)
        .where(ASRUsage.status == "success")
        .group_by(ASRUsage.variant)
    )

    rows = result.all()
    usage_map = {
        row.variant: UsageSummaryByVariant(
            variant=row.variant,
            used_seconds=float(row.total_seconds or 0),
            estimated_cost=float(row.total_cost or 0),
        )
        for row in rows
    }

    # 返回所有已知 variant，未使用的显示为 0
    return [
        usage_map.get(
            variant,
            UsageSummaryByVariant(variant=variant, used_seconds=0, estimated_cost=0),
        )
        for variant in KNOWN_VARIANTS
    ]


async def get_user_total_usage(
    db: AsyncSession,
    user_id: str,
) -> float:
    """获取用户的 ASR 总消耗（所有 variant 合计）

    Args:
        db: 数据库会话
        user_id: 用户 ID

    Returns:
        总消耗秒数
    """
    result = await db.execute(
        select(func.sum(ASRUsage.duration_seconds).label("total_seconds"))
        .where(ASRUsage.user_id == user_id)
        .where(ASRUsage.status == "success")
    )
    row = result.one_or_none()
    return float(row.total_seconds or 0) if row else 0


# 提供商显示名称映射
PROVIDER_DISPLAY_NAMES = {
    "tencent": "腾讯云",
    "aliyun": "阿里云",
    "volcengine": "火山引擎",
}

VARIANT_DISPLAY_NAMES = {
    "file": "录音文件",
    "file_fast": "录音文件(极速)",
}


def _get_display_name(provider: str, variant: str) -> str:
    """生成提供商显示名称"""
    provider_name = PROVIDER_DISPLAY_NAMES.get(provider, provider)
    variant_name = VARIANT_DISPLAY_NAMES.get(variant, variant)
    return f"{provider_name} {variant_name}"


@dataclass
class FreeQuotaStatusData:
    """免费额度状态数据"""

    provider: str
    variant: str
    display_name: str
    free_quota_seconds: float
    used_seconds: float
    reset_period: str
    period_start: datetime
    period_end: datetime


@dataclass
class ProviderUsageData:
    """提供商付费使用数据"""

    provider: str
    variant: str
    display_name: str
    cost_per_hour: float
    paid_seconds: float
    paid_cost: float
    is_enabled: bool


@dataclass
class AdminOverviewResult:
    """管理员概览结果"""

    free_quota_status: list[FreeQuotaStatusData]
    providers_usage: list[ProviderUsageData]
    summary: dict


async def get_admin_asr_overview(
    db: AsyncSession,
    now: Optional[datetime] = None,
) -> AdminOverviewResult:
    """获取管理员 ASR 概览数据

    分离两个关注点：
    1. free_quota_status - 免费额度状态（只关心有免费额度的提供商的额度使用情况）
    2. providers_usage - 所有提供商的付费使用统计

    Args:
        db: 数据库会话
        now: 当前时间

    Returns:
        AdminOverviewResult
    """
    from app.core.asr_free_quota import get_current_period_bounds

    now = now or datetime.now(timezone.utc)

    # 1. 获取所有定价配置
    result = await db.execute(
        select(AsrPricingConfig).order_by(AsrPricingConfig.provider, AsrPricingConfig.variant)
    )
    configs = result.scalars().all()

    # 2. 获取全局用量周期数据（owner_user_id IS NULL）
    result = await db.execute(select(AsrUsagePeriod).where(AsrUsagePeriod.owner_user_id.is_(None)))
    periods = result.scalars().all()

    # 按 (provider, variant) 索引，取最新的周期
    period_map: dict[tuple[str, str], AsrUsagePeriod] = {}
    for period in periods:
        key = (period.provider, period.variant)
        if key not in period_map or period.period_start > period_map[key].period_start:
            period_map[key] = period

    # 3. 构建数据
    free_quota_status: list[FreeQuotaStatusData] = []
    providers_usage: list[ProviderUsageData] = []

    total_used = 0.0
    total_free = 0.0
    total_paid = 0.0
    total_cost = 0.0

    for config in configs:
        key = (config.provider, config.variant)
        period = period_map.get(key)
        display_name = _get_display_name(config.provider, config.variant)

        free_quota_used = period.free_quota_used if period else 0.0
        used_seconds = period.used_seconds if period else 0.0
        paid_seconds = period.paid_seconds if period else 0.0
        cost = period.total_cost if period else 0.0

        # 累计汇总
        total_used += used_seconds
        total_free += free_quota_used
        total_paid += paid_seconds
        total_cost += cost

        # 判断是否有免费额度配置
        has_free_quota = (
            config.free_quota_seconds > 0 and config.reset_period and config.reset_period != "none"
        )

        # 免费额度状态（只有配置了免费额度的才显示）
        if has_free_quota:
            try:
                period_start, period_end = get_current_period_bounds(config.reset_period, now)
            except Exception:
                period_start = now
                period_end = now

            free_quota_status.append(
                FreeQuotaStatusData(
                    provider=config.provider,
                    variant=config.variant,
                    display_name=display_name,
                    free_quota_seconds=config.free_quota_seconds,
                    used_seconds=free_quota_used,
                    reset_period=config.reset_period,
                    period_start=period_start,
                    period_end=period_end,
                )
            )

        # 所有提供商的付费使用统计
        providers_usage.append(
            ProviderUsageData(
                provider=config.provider,
                variant=config.variant,
                display_name=display_name,
                cost_per_hour=config.cost_per_hour,
                paid_seconds=paid_seconds,
                paid_cost=cost,
                is_enabled=config.is_enabled,
            )
        )

    summary = {
        "total_used_hours": round(total_used / 3600, 2),
        "total_free_hours": round(total_free / 3600, 2),
        "total_paid_hours": round(total_paid / 3600, 2),
        "total_cost": round(total_cost, 4),
    }

    return AdminOverviewResult(
        free_quota_status=free_quota_status,
        providers_usage=providers_usage,
        summary=summary,
    )
