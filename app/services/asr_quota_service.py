from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.asr_quota import AsrQuota


@dataclass(frozen=True)
class QuotaWindow:
    start: datetime
    end: datetime


def _window_bounds(now: datetime, window_type: str) -> QuotaWindow:
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
    raise ValueError(f"Unsupported window_type: {window_type}")


def _is_available(quota: AsrQuota) -> bool:
    if quota.status == "exhausted":
        return False
    if quota.quota_seconds <= 0:
        return False
    return quota.used_seconds < quota.quota_seconds


def _active_window_clause(now: datetime) -> object:
    return and_(AsrQuota.window_start <= now, AsrQuota.window_end >= now)


def _effective_quotas(
    rows: list[AsrQuota],
    providers: list[str],
    owner_user_id: Optional[str],
) -> dict[str, list[AsrQuota]]:
    user_map: dict[str, list[AsrQuota]] = {}
    global_map: dict[str, list[AsrQuota]] = {}
    for row in rows:
        if row.owner_user_id:
            user_map.setdefault(row.provider, []).append(row)
        else:
            global_map.setdefault(row.provider, []).append(row)

    effective: dict[str, list[AsrQuota]] = {}
    for provider in providers:
        if owner_user_id and provider in user_map:
            effective[provider] = user_map[provider]
        elif provider in global_map:
            effective[provider] = global_map[provider]
    return effective


def select_available_provider_sync(
    session: Session,
    providers: Iterable[str],
    owner_user_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> list[str]:
    now = now or datetime.now(timezone.utc)
    provider_list = [p for p in providers if isinstance(p, str)]
    if not provider_list:
        return []

    rows = (
        session.execute(
            select(AsrQuota)
            .where(AsrQuota.provider.in_(provider_list))
            .where(_active_window_clause(now))
            .where(
                or_(AsrQuota.owner_user_id.is_(None), AsrQuota.owner_user_id == owner_user_id)
            )
        )
        .scalars()
        .all()
    )

    if not rows:
        return []

    quotas_by_provider = _effective_quotas(rows, provider_list, owner_user_id)

    available: list[str] = []
    for provider, quotas in quotas_by_provider.items():
        if all(_is_available(q) for q in quotas):
            available.append(provider)

    return available


def get_quota_providers_sync(
    session: Session,
    providers: Iterable[str],
    owner_user_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> set[str]:
    now = now or datetime.now(timezone.utc)
    provider_list = [p for p in providers if isinstance(p, str)]
    if not provider_list:
        return set()

    rows = (
        session.execute(
            select(AsrQuota)
            .where(AsrQuota.provider.in_(provider_list))
            .where(_active_window_clause(now))
            .where(
                or_(AsrQuota.owner_user_id.is_(None), AsrQuota.owner_user_id == owner_user_id)
            )
        )
        .scalars()
        .all()
    )
    effective = _effective_quotas(rows, provider_list, owner_user_id)
    return set(effective.keys())


def record_usage_sync(
    session: Session,
    provider: str,
    duration_seconds: int,
    owner_user_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> None:
    if not provider or duration_seconds <= 0:
        return

    now = now or datetime.now(timezone.utc)
    rows = (
        session.execute(
            select(AsrQuota)
            .where(AsrQuota.provider == provider)
            .where(_active_window_clause(now))
            .where(or_(AsrQuota.owner_user_id.is_(None), AsrQuota.owner_user_id == owner_user_id))
        )
        .scalars()
        .all()
    )

    if not rows:
        return

    effective = _effective_quotas(rows, [provider], owner_user_id).get(provider, [])
    for row in effective:
        new_used = row.used_seconds + duration_seconds
        status = "exhausted" if new_used >= row.quota_seconds else row.status
        session.execute(
            update(AsrQuota)
            .where(AsrQuota.id == row.id)
            .values(used_seconds=new_used, status=status)
        )

    session.commit()


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
    providers = sorted({row.provider for row in rows})
    effective = _effective_quotas(rows, providers, owner_user_id)
    merged: list[AsrQuota] = []
    for provider in providers:
        merged.extend(effective.get(provider, []))
    return merged


async def list_global_quotas(
    db: AsyncSession,
    now: Optional[datetime] = None,
) -> list[AsrQuota]:
    now = now or datetime.now(timezone.utc)
    result = await db.execute(
        select(AsrQuota)
        .where(_active_window_clause(now))
        .where(AsrQuota.owner_user_id.is_(None))
    )
    return result.scalars().all()


async def upsert_quota(
    db: AsyncSession,
    provider: str,
    window_type: str,
    quota_seconds: int,
    reset: bool,
    owner_user_id: Optional[str],
    now: Optional[datetime] = None,
) -> AsrQuota:
    now = now or datetime.now(timezone.utc)
    window = _window_bounds(now, window_type)

    stmt = select(AsrQuota).where(
        AsrQuota.provider == provider,
        AsrQuota.window_type == window_type,
        AsrQuota.window_start == window.start,
        AsrQuota.owner_user_id == owner_user_id,
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()

    if existing:
        used = 0 if reset else existing.used_seconds
        status = "active" if reset else existing.status
        existing.quota_seconds = quota_seconds
        existing.used_seconds = used
        existing.status = status
        existing.window_end = window.end
        await db.commit()
        await db.refresh(existing)
        return existing

    new_row = AsrQuota(
        owner_user_id=owner_user_id,
        provider=provider,
        window_type=window_type,
        window_start=window.start,
        window_end=window.end,
        quota_seconds=quota_seconds,
        used_seconds=0,
        status="active",
    )
    db.add(new_row)
    await db.commit()
    await db.refresh(new_row)
    return new_row
