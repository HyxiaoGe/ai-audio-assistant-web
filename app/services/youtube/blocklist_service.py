from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.youtube_blocklist import YouTubeBlocklistEntry
from app.services.youtube.search_cache import normalize_query
from app.services.youtube.search_service import VideoHit

# YouTube 频道 ID:UC + 22 个 [A-Za-z0-9_-]。大小写敏感,判型/存储一律原样。
_CHANNEL_ID_RE = re.compile(r"^UC[0-9A-Za-z_-]{22}$")
_CHANNEL_URL_RE = re.compile(r"/channel/(UC[0-9A-Za-z_-]{22})")


@dataclass(frozen=True)
class Blocklist:
    terms: frozenset[str]  # 归一化搜索词(env ∪ DB)
    channel_ids: frozenset[str]  # 精确频道 ID(原样)
    channel_names: frozenset[str]  # 归一化频道名


# ---- 进程内 TTL 缓存 ----
_cache: Blocklist | None = None
_cache_expires_at: datetime | None = None


def invalidate_cache() -> None:
    """admin 写操作后调用,清当前进程缓存(同进程即时;跨副本 ≤TTL 传播)。"""
    global _cache, _cache_expires_at
    _cache = None
    _cache_expires_at = None


def _env_terms() -> set[str]:
    return {normalize_query(w) for w in settings.YOUTUBE_SEARCH_DENYLIST if w and w.strip()}


async def get_blocklist(db: AsyncSession) -> Blocklist:
    """加载黑名单(env terms ∪ DB 活跃行),30s TTL 进程缓存。"""
    global _cache, _cache_expires_at
    now = datetime.now(UTC)
    if _cache is not None and _cache_expires_at is not None and now < _cache_expires_at:
        return _cache

    rows = (
        await db.execute(
            select(YouTubeBlocklistEntry.match_field, YouTubeBlocklistEntry.normalized_value).where(
                YouTubeBlocklistEntry.deleted_at.is_(None)
            )
        )
    ).all()

    terms = _env_terms()
    channel_ids: set[str] = set()
    channel_names: set[str] = set()
    for match_field, normalized_value in rows:
        if match_field == "query":
            terms.add(normalized_value)
        elif match_field == "channel_id":
            channel_ids.add(normalized_value)
        elif match_field == "channel_name":
            channel_names.add(normalized_value)

    bl = Blocklist(
        terms=frozenset(terms),
        channel_ids=frozenset(channel_ids),
        channel_names=frozenset(channel_names),
    )
    _cache = bl
    _cache_expires_at = now + timedelta(seconds=settings.BLOCKLIST_CACHE_TTL_SECONDS)
    return bl


def is_term_blocked(normalized_query: str, bl: Blocklist) -> bool:
    return normalized_query in bl.terms


def is_channel_blocked(hit: VideoHit, bl: Blocklist) -> bool:
    if hit.channel_id and hit.channel_id in bl.channel_ids:
        return True
    if hit.channel and normalize_query(hit.channel) in bl.channel_names:
        return True
    return False


def filter_hits(hits: list[VideoHit], bl: Blocklist) -> list[VideoHit]:
    return [h for h in hits if not is_channel_blocked(h, bl)]


def classify_channel_input(raw: str) -> tuple[str, str]:
    """把管理员输入判型为 (match_field, normalized_value)。

    UCxxxx 裸 ID 或 .../channel/UCxxxx 链接 → ('channel_id', <原样 ID>);
    其余(显示名、@handle、/c/、/user/)→ ('channel_name', 归一化名)。
    """
    s = raw.strip()
    url_match = _CHANNEL_URL_RE.search(s)
    if url_match:
        return ("channel_id", url_match.group(1))
    if _CHANNEL_ID_RE.match(s):
        return ("channel_id", s)
    return ("channel_name", normalize_query(s))
