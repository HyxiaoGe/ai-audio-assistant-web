from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.youtube_allowlist import YouTubeAllowlistEntry
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
