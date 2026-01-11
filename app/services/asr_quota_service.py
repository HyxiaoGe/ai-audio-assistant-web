from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import inspect
from typing import Iterable, Optional

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.asr_quota import AsrQuota


@dataclass(frozen=True)
class QuotaWindow:
    start: datetime
    end: datetime


def _extract_scalars(result: object) -> list[AsrQuota]:
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


def _is_available(quota: AsrQuota) -> bool:
    if quota.status == "exhausted":
        return False
    if quota.quota_seconds <= 0:
        return False
    return quota.used_seconds < quota.quota_seconds


def _active_window_clause(now: datetime) -> object:
    return and_(AsrQuota.window_start <= now, AsrQuota.window_end >= now)


QuotaKey = tuple[str, str]


def _effective_quotas(
    rows: list[AsrQuota],
    keys: list[QuotaKey],
    owner_user_id: Optional[str],
) -> dict[QuotaKey, list[AsrQuota]]:
    user_map: dict[QuotaKey, list[AsrQuota]] = {}
    global_map: dict[QuotaKey, list[AsrQuota]] = {}
    for row in rows:
        key = (row.provider, row.variant)
        if row.owner_user_id:
            user_map.setdefault(key, []).append(row)
        else:
            global_map.setdefault(key, []).append(row)

    effective: dict[QuotaKey, list[AsrQuota]] = {}
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
            select(AsrQuota)
            .where(AsrQuota.provider.in_(provider_list))
            .where(AsrQuota.variant == variant)
            .where(_active_window_clause(now))
            .where(or_(AsrQuota.owner_user_id.is_(None), AsrQuota.owner_user_id == owner_user_id))
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
            select(AsrQuota)
            .where(AsrQuota.provider.in_(provider_list))
            .where(AsrQuota.variant == variant)
            .where(_active_window_clause(now))
            .where(or_(AsrQuota.owner_user_id.is_(None), AsrQuota.owner_user_id == owner_user_id))
        )
    )
    keys = [(provider, variant) for provider in provider_list]
    effective = _effective_quotas(rows, keys, owner_user_id)
    return {provider for (provider, _variant) in effective.keys()}


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
            select(AsrQuota)
            .where(AsrQuota.provider == provider)
            .where(AsrQuota.variant == variant)
            .where(_active_window_clause(now))
            .where(or_(AsrQuota.owner_user_id.is_(None), AsrQuota.owner_user_id == owner_user_id))
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
            update(AsrQuota)
            .where(AsrQuota.id == row.id)
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
        select(AsrQuota)
        .where(AsrQuota.provider.in_(provider_list))
        .where(AsrQuota.variant == variant)
        .where(_active_window_clause(now))
        .where(or_(AsrQuota.owner_user_id.is_(None), AsrQuota.owner_user_id == owner_user_id)),
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
        select(AsrQuota)
        .where(AsrQuota.provider.in_(provider_list))
        .where(AsrQuota.variant == variant)
        .where(_active_window_clause(now))
        .where(or_(AsrQuota.owner_user_id.is_(None), AsrQuota.owner_user_id == owner_user_id)),
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
        select(AsrQuota)
        .where(AsrQuota.provider == provider)
        .where(AsrQuota.variant == variant)
        .where(_active_window_clause(now))
        .where(or_(AsrQuota.owner_user_id.is_(None), AsrQuota.owner_user_id == owner_user_id)),
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
            update(AsrQuota)
            .where(AsrQuota.id == row.id)
            .values(used_seconds=new_used, status=status),
        )

    await _commit(session)


async def list_effective_quotas(
    db: AsyncSession,
    owner_user_id: Optional[str],
    now: Optional[datetime] = None,
) -> list[AsrQuota]:
    now = now or datetime.now(timezone.utc)
    result = await db.execute(
        select(AsrQuota)
        .where(_active_window_clause(now))
        .where(or_(AsrQuota.owner_user_id.is_(None), AsrQuota.owner_user_id == owner_user_id))
    )
    rows = result.scalars().all()
    keys = sorted({(row.provider, row.variant) for row in rows})
    effective = _effective_quotas(rows, keys, owner_user_id)
    merged: list[AsrQuota] = []
    for key in keys:
        merged.extend(effective.get(key, []))
    return merged


async def list_global_quotas(
    db: AsyncSession,
    now: Optional[datetime] = None,
) -> list[AsrQuota]:
    now = now or datetime.now(timezone.utc)
    result = await db.execute(
        select(AsrQuota).where(_active_window_clause(now)).where(AsrQuota.owner_user_id.is_(None))
    )
    return result.scalars().all()


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
) -> AsrQuota:
    now = now or datetime.now(timezone.utc)
    window = _window_bounds(now, window_type, window_start=window_start, window_end=window_end)

    stmt = select(AsrQuota).where(
        AsrQuota.provider == provider,
        AsrQuota.variant == variant,
        AsrQuota.window_type == window_type,
        AsrQuota.window_start == window.start,
        AsrQuota.owner_user_id == owner_user_id,
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
    new_row = AsrQuota(
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
