"""审核卫生巡检(beat 每小时 :05)。

两件独立的事(各自 best-effort,互不拖累、绝不向 beat 抛):
  1. 缓存 GC:删过期的 youtube_search_queries 行(普通 7d / 敏感 6h 双保留期)。
  2. flagged backfill:把已处置行的 last_title 置 NULL(脱敏 + 长期安全网)。
最小化 CMS 账本之外残留的政治敏感明文。
"""

from __future__ import annotations

import asyncio
import logging

from app.services.youtube import channel_flag_service, search_cache
from worker.celery_app import celery_app
from worker.db import worker_async_session_factory

logger = logging.getLogger("worker.moderation_hygiene")


async def _run_hygiene() -> dict[str, int]:
    result = {"purged": 0, "scrubbed": 0}
    try:
        async with worker_async_session_factory() as session:
            result["purged"] = await search_cache.purge_stale_queries(session)
    except Exception:
        logger.warning("purge_stale_queries failed", exc_info=True)
    try:
        async with worker_async_session_factory() as session:
            result["scrubbed"] = await channel_flag_service.scrub_resolved_titles(session)
    except Exception:
        logger.warning("scrub_resolved_titles failed", exc_info=True)
    return result


@celery_app.task(name="worker.tasks.run_moderation_hygiene")
def run_moderation_hygiene() -> dict[str, int]:
    return asyncio.run(_run_hygiene())
