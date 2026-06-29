from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.models.youtube_allowlist import YouTubeAllowlistEntry
from app.models.youtube_blocklist import YouTubeBlocklistEntry
from app.services.youtube import blocklist_service
from app.services.youtube.search_cache import normalize_query
from app.services.youtube.search_service import VideoHit


@dataclass(frozen=True)
class Allowlist:
    channel_ids: frozenset[str]  # 精确频道 ID(原样)
    channel_handles: frozenset[str]  # 归一化 @handle(无 @、casefold)
    channel_names: frozenset[str]  # 归一化频道名


# ---- 进程内 TTL 缓存(独立于 blocklist 缓存)----
_cache: Allowlist | None = None
_cache_expires_at: datetime | None = None


def invalidate_cache() -> None:
    """admin 写操作后调用,清当前进程缓存(同进程即时;跨副本 ≤TTL 传播)。"""
    global _cache, _cache_expires_at
    _cache = None
    _cache_expires_at = None


_ALLOWLIST_STMT = select(YouTubeAllowlistEntry.match_field, YouTubeAllowlistEntry.normalized_value).where(
    YouTubeAllowlistEntry.deleted_at.is_(None)
)


def _build_allowlist(rows: Iterable[tuple[str, str]]) -> Allowlist:
    channel_ids: set[str] = set()
    channel_handles: set[str] = set()
    channel_names: set[str] = set()
    for match_field, normalized_value in rows:
        if match_field == "channel_id":
            channel_ids.add(normalized_value)
        elif match_field == "channel_handle":
            channel_handles.add(normalized_value)
        elif match_field == "channel_name":
            channel_names.add(normalized_value)
    return Allowlist(
        channel_ids=frozenset(channel_ids),
        channel_handles=frozenset(channel_handles),
        channel_names=frozenset(channel_names),
    )


async def get_allowlist(db: AsyncSession) -> Allowlist:
    """加载放行表(DB 活跃行),复用 BLOCKLIST_CACHE_TTL_SECONDS 做进程缓存。"""
    global _cache, _cache_expires_at
    now = datetime.now(UTC)
    if _cache is not None and _cache_expires_at is not None and now < _cache_expires_at:
        return _cache
    rows = (await db.execute(_ALLOWLIST_STMT)).all()
    al = _build_allowlist(rows)
    _cache = al
    _cache_expires_at = now + timedelta(seconds=settings.BLOCKLIST_CACHE_TTL_SECONDS)
    return al


def is_channel_allowed(hit: VideoHit, al: Allowlist) -> bool:
    """三级身份,镜像 blocklist_service.is_channel_blocked:id > normalize_handle(handle) > normalize_query(channel)。"""
    if hit.channel_id and hit.channel_id in al.channel_ids:
        return True
    if hit.handle and blocklist_service.normalize_handle(hit.handle) in al.channel_handles:
        return True
    if hit.channel and normalize_query(hit.channel) in al.channel_names:
        return True
    return False


async def list_entries(db: AsyncSession) -> list[YouTubeAllowlistEntry]:
    rows = (
        (
            await db.execute(
                select(YouTubeAllowlistEntry)
                .where(YouTubeAllowlistEntry.deleted_at.is_(None))
                .order_by(YouTubeAllowlistEntry.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def _blocklist_has_active(db: AsyncSession, match_field: str, normalized_value: str) -> bool:
    """同身份是否已在黑名单活跃存在(跨表互斥用)。独立函数便于测试 monkeypatch。"""
    row = (
        await db.execute(
            select(YouTubeBlocklistEntry.id).where(
                YouTubeBlocklistEntry.kind == "channel",
                YouTubeBlocklistEntry.match_field == match_field,
                YouTubeBlocklistEntry.normalized_value == normalized_value,
                YouTubeBlocklistEntry.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    return row is not None


async def add_entry(
    db: AsyncSession,
    *,
    value: str,
    note: str | None,
    created_by: str | None,
    name: str | None = None,
) -> tuple[YouTubeAllowlistEntry, bool]:
    """加放行条目(频道专用):归一化 + 判型 + handle 解析成 channel_id + 跨表互斥 + 幂等/复活去重。

    name 优先级:显式传入(加白用 flag.channel_name)> @handle yt-dlp 解析名 > 裸名输入本身。
    跨表互斥:同身份已在黑名单活跃 → 抛 CHANNEL_BLOCKLIST_ALLOWLIST_CONFLICT(黑名单恒赢)。
    """
    raw = value.strip()
    if not raw:
        raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="value")

    display_name = name.strip() if name and name.strip() else None
    match_field, normalized_value = blocklist_service.classify_channel_input(raw)
    if match_field == "channel_handle":
        resolved_id, resolved_name = await blocklist_service.resolve_channel_meta(normalized_value)
        if resolved_id:
            match_field, normalized_value = "channel_id", resolved_id
            if display_name is None:
                display_name = resolved_name
    elif display_name is None and match_field == "channel_name":
        display_name = raw  # 裸频道名输入本身就是名字

    if await _blocklist_has_active(db, match_field, normalized_value):
        raise BusinessError(ErrorCode.CHANNEL_BLOCKLIST_ALLOWLIST_CONFLICT)

    existing = (
        await db.execute(
            select(YouTubeAllowlistEntry).where(
                YouTubeAllowlistEntry.match_field == match_field,
                YouTubeAllowlistEntry.normalized_value == normalized_value,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        if existing.deleted_at is None:
            return existing, False  # 已活跃 → 幂等
        existing.deleted_at = None
        existing.raw_value = raw
        existing.note = note
        existing.created_by = created_by
        if display_name is not None:
            existing.display_name = display_name
        await db.commit()
        return existing, True  # 软删复活算重新放行

    entry = YouTubeAllowlistEntry(
        match_field=match_field,
        raw_value=raw,
        normalized_value=normalized_value,
        note=note,
        created_by=created_by,
        display_name=display_name,
    )
    db.add(entry)
    await db.commit()
    return entry, True


async def delete_entry(db: AsyncSession, entry_id: str) -> bool:
    entry = (
        await db.execute(
            select(YouTubeAllowlistEntry).where(
                YouTubeAllowlistEntry.id == entry_id,
                YouTubeAllowlistEntry.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if entry is None:
        return False
    entry.deleted_at = datetime.now(UTC)
    await db.commit()
    return True
