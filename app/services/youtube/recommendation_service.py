from __future__ import annotations

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.youtube_recommended_video import YouTubeRecommendedVideo
from app.services.youtube.search_service import VideoHit

RECOMMENDATIONS_TOP_N = 12  # 单一真源:harvest 取 top-N + 端点默认 limit


async def replace_recommendations(db: AsyncSession, hits: list[VideoHit]) -> None:
    """全量替换热门推荐:清空表后按顺序插入(rank=索引)。调用方保证 hits 已排序、已过审、非空。"""
    await db.execute(delete(YouTubeRecommendedVideo))
    for rank, h in enumerate(hits):
        db.add(
            YouTubeRecommendedVideo(
                rank=rank,
                video_id=h.video_id,
                title=h.title,
                channel=h.channel,
                channel_id=h.channel_id,
                handle=h.handle,
                thumbnail=h.thumbnail,
                url=h.url,
                view_count=h.view_count,
                duration=h.duration,
            )
        )
    await db.commit()


async def get_recommendations(db: AsyncSession, limit: int) -> list[VideoHit]:
    """按 rank 升序读 top-limit,构造 VideoHit。读错兜 [](fail-safe:推荐非关键路径,失败回落空)。"""
    try:
        stmt = select(YouTubeRecommendedVideo).order_by(YouTubeRecommendedVideo.rank).limit(limit)
        rows = (await db.execute(stmt)).scalars().all()
        return [
            VideoHit(
                video_id=r.video_id,
                title=r.title,
                channel=r.channel,
                channel_id=r.channel_id,
                handle=r.handle,
                thumbnail=r.thumbnail,
                url=r.url,
                view_count=r.view_count,
                duration=r.duration,
            )
            for r in rows
        ]
    except Exception as exc:
        logger.warning("get_recommendations 读推荐异常,回落空: {}", exc)
        return []
