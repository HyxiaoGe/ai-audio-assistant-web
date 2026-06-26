from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.redis import get_redis_client
from app.models.youtube_search import YouTubeSearchQuery
from app.services.youtube.search_service import VideoHit


def normalize_query(raw: str) -> str:
    """trim → 折叠内部连续空白为单空格 → casefold()。"""
    return " ".join(raw.split()).casefold()


def _denylist_normalized() -> set[str]:
    return {normalize_query(w) for w in settings.YOUTUBE_SEARCH_DENYLIST if w and w.strip()}


def is_denylisted(normalized: str) -> bool:
    return normalized in _denylist_normalized()


@dataclass
class TrendingItem:
    query: str
    count: int


async def get_cached_results(db: AsyncSession, normalized: str) -> list[VideoHit] | None:
    row = (
        await db.execute(select(YouTubeSearchQuery).where(YouTubeSearchQuery.normalized_query == normalized))
    ).scalar_one_or_none()
    if row is None or row.fetched_at is None:
        return None
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.YOUTUBE_SEARCH_CACHE_TTL_SECONDS)
    if row.fetched_at < cutoff:
        return None
    return [VideoHit.model_validate(item) for item in (row.results_json or [])]


async def upsert_results(db: AsyncSession, normalized: str, display: str, hits: list[VideoHit]) -> None:
    """写入/刷新结果与 fetched_at(成功抓取后调用;失败路径不调用 => 不写负缓存)。"""
    payload = [h.model_dump() for h in hits]
    now = datetime.now(UTC)
    # last_searched_at 故意不在此写:register_query_heat 是唯一写入者,
    # 避免崩在两次 commit 之间留下「count=0 但已进热门窗口」的孤儿行。
    stmt = (
        pg_insert(YouTubeSearchQuery)
        .values(
            normalized_query=normalized,
            display_query=display,
            results_json=payload,
            fetched_at=now,
        )
        .on_conflict_do_update(
            index_elements=[YouTubeSearchQuery.normalized_query],
            set_={"display_query": display, "results_json": payload, "fetched_at": now},
        )
    )
    await db.execute(stmt)
    await db.commit()


async def heat_is_new_searcher(normalized: str, searcher_key: str) -> bool:
    """按 (查询, 搜索者) 在热门窗口内去重:Redis SETNX 成功=该搜索者本窗口首次搜该词。

    Redis 故障 fail-open 返 True(宁可微量多计,不丢热度)。
    """
    window_seconds = settings.YOUTUBE_TRENDING_WINDOW_DAYS * 86400
    try:
        client = get_redis_client()
        return bool(await client.set(f"yts:heat:{normalized}:{searcher_key}", "1", ex=window_seconds, nx=True))
    except Exception:
        return True


async def register_query_heat(db: AsyncSession, normalized: str, searcher_key: str) -> None:
    """每次搜索抬升热度:去重计数(防刷)+ 刷新 last_searched_at。行须已存在。"""
    now = datetime.now(UTC)
    is_new = await heat_is_new_searcher(normalized, searcher_key)
    stmt = update(YouTubeSearchQuery).where(YouTubeSearchQuery.normalized_query == normalized)
    if is_new:
        stmt = stmt.values(last_searched_at=now, search_count=YouTubeSearchQuery.search_count + 1)
    else:
        stmt = stmt.values(last_searched_at=now)
    await db.execute(stmt)
    await db.commit()


async def get_trending(db: AsyncSession) -> list[TrendingItem]:
    # 局部 import 打破 search_cache ↔ blocklist_service 潜在环(blocklist_service 顶部 import 本模块)。
    from app.services.youtube import blocklist_service

    window_start = datetime.now(UTC) - timedelta(days=settings.YOUTUBE_TRENDING_WINDOW_DAYS)
    rows = (
        (
            await db.execute(
                select(YouTubeSearchQuery).where(
                    YouTubeSearchQuery.last_searched_at >= window_start,
                    YouTubeSearchQuery.is_blocked.is_(False),
                )
            )
        )
        .scalars()
        .all()
    )
    bl = await blocklist_service.get_blocklist(db)
    eligible = [r for r in rows if r.normalized_query not in bl.terms]
    # 冷启动隐藏:近窗口不同查询数 < 阈值则不展示热门
    if len(eligible) < settings.YOUTUBE_TRENDING_MIN_VOLUME:
        return []
    eligible.sort(key=lambda r: r.search_count, reverse=True)
    top = eligible[: settings.YOUTUBE_TRENDING_TOP_N]
    return [TrendingItem(query=r.display_query, count=r.search_count) for r in top]
