"""热门推荐 harvest(beat 每 6h :20)。

搜精选「大家在搜」种子词 → 过审(照搬搜索路径)→ 跨词去重按 view_count 排 top-N → 全量替换写表。
容错:单词抓取失败跳过、整轮异常不向 beat 抛;抓空/全被过审掉绝不清表(保留上一轮)。
"""

from __future__ import annotations

import asyncio
import logging

from app.services.feature.flags import get_curated_trending_queries
from app.services.youtube import blocklist_service, recommendation_service
from app.services.youtube.moderation_pipeline import moderate_hits
from app.services.youtube.search_service import VideoHit, YouTubeSearchService
from worker.celery_app import celery_app
from worker.db import worker_async_session_factory

logger = logging.getLogger("worker.harvest_recommendations")

PER_TERM = 5  # 每个种子词取前 N 条
_DEFAULT_SEEDS = ["大模型", "AI", "Claude", "OpenAI", "播客"]  # 精选未配时的兜底种子词


def _merge_dedup(per_term: list[list[VideoHit]]) -> list[VideoHit]:
    """跨词按 video_id 去重,同 id 保留 view_count 最大者。"""
    best: dict[str, VideoHit] = {}
    for hits in per_term:
        for h in hits:
            cur = best.get(h.video_id)
            if cur is None or (h.view_count or 0) > (cur.view_count or 0):
                best[h.video_id] = h
    return list(best.values())


def _top_n_by_views(hits: list[VideoHit], n: int) -> list[VideoHit]:
    """按 view_count 降序(缺失当 0)取前 n。"""
    return sorted(hits, key=lambda h: h.view_count or 0, reverse=True)[:n]


async def _seed_terms(db: object) -> list[str]:
    curated = await get_curated_trending_queries(db)  # type: ignore[arg-type]
    return curated or _DEFAULT_SEEDS


async def _harvest(session: object) -> dict[str, int]:
    terms = await _seed_terms(session)
    svc = YouTubeSearchService()
    per_term: list[list[VideoHit]] = []
    for term in terms:
        try:
            per_term.append(await svc.search(term, PER_TERM))
        except Exception:
            logger.warning("harvest 单词抓取失败,跳过 term=%r", term, exc_info=True)
    merged = _merge_dedup(per_term)
    if not merged:
        return {"stored": 0}  # 抓空:保留上一轮,绝不清表
    bl = await blocklist_service.get_blocklist(session)  # type: ignore[arg-type]
    kept, _sensitive = await moderate_hits(session, merged, bl)  # type: ignore[arg-type]
    ranked = _top_n_by_views(kept, recommendation_service.RECOMMENDATIONS_TOP_N)
    if not ranked:
        return {"stored": 0}  # 全被过审掉:同样保留上一轮
    await recommendation_service.replace_recommendations(session, ranked)  # type: ignore[arg-type]
    return {"stored": len(ranked)}


async def _run_harvest() -> dict[str, int]:
    async with worker_async_session_factory() as session:
        return await _harvest(session)


@celery_app.task(name="worker.tasks.harvest_recommendations.run_harvest")
def run_harvest() -> dict[str, int]:
    try:
        return asyncio.run(_run_harvest())
    except Exception:
        logger.warning("harvest_recommendations 整轮失败", exc_info=True)
        return {"stored": 0}
