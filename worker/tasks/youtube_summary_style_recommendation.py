"""YouTube summary style recommendation prewarm tasks."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from celery import shared_task

from app.services.youtube.summary_style_recommendation import prewarm_summary_styles_for_videos
from worker.db import worker_async_session_factory

logger = logging.getLogger(__name__)

DEFAULT_PREWARM_LIMIT = 20
MAX_PREWARM_LIMIT = 50


async def _prewarm(
    *,
    user_id: str,
    video_ids: list[str],
    locale: str,
    limit: int,
) -> dict[str, int]:
    async with worker_async_session_factory() as session:
        return await prewarm_summary_styles_for_videos(
            session,
            user_id,
            video_ids,
            locale=locale,
            limit=limit,
        )


@shared_task(
    name="worker.tasks.youtube_summary_style_recommendation.prewarm_youtube_summary_style_recommendations",
    bind=True,
    soft_time_limit=300,
)
def prewarm_youtube_summary_style_recommendations(
    self,
    user_id: str,
    video_ids: list[str],
    locale: str = "zh",
    limit: int = DEFAULT_PREWARM_LIMIT,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Prewarm cached summary style recommendations for YouTube videos."""
    bounded_limit = max(0, min(limit, MAX_PREWARM_LIMIT))
    logger.info(
        "Starting YouTube summary style recommendation prewarm: user_id=%s count=%s limit=%s request_id=%s",
        user_id,
        len(video_ids),
        bounded_limit,
        request_id,
    )

    result = asyncio.run(
        _prewarm(
            user_id=user_id,
            video_ids=video_ids,
            locale=locale,
            limit=bounded_limit,
        )
    )
    logger.info(
        "Completed YouTube summary style recommendation prewarm: user_id=%s result=%s request_id=%s",
        user_id,
        result,
        request_id,
    )
    return {
        "status": "success",
        "request_id": request_id,
        **result,
    }
