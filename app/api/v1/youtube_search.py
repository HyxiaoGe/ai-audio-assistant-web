from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_db, get_public_viewer
from app.config import settings
from app.core.exceptions import BusinessError
from app.core.rate_limit import _client_ip, rate_limit_by_ip, rate_limit_user_or_ip
from app.core.response import success
from app.i18n.codes import ErrorCode
from app.schemas.youtube_search import RecommendationData, SearchData, TrendingData, TrendingItemOut
from app.services.feature.flags import get_curated_trending_queries, is_discover_enabled
from app.services.moderation import gate as moderation_gate
from app.services.youtube import blocklist_service, recommendation_service, search_cache
from app.services.youtube.existing_task_lookup import annotate_existing_tasks
from app.services.youtube.moderation_pipeline import moderate_hits
from app.services.youtube.search_service import YouTubeSearchService

router = APIRouter(prefix="/youtube", tags=["youtube-search"])

_search_rate_limit = rate_limit_user_or_ip(
    user_limit=settings.YOUTUBE_SEARCH_RATE_PER_USER_MIN,
    ip_limit=settings.YOUTUBE_SEARCH_RATE_PER_IP_MIN,
    scope="youtube_search",
)

_trending_rate_limit = rate_limit_by_ip(limit=settings.YOUTUBE_SEARCH_RATE_PER_IP_MIN, scope="youtube_trending")


@router.get("/search")
async def search_youtube(
    request: Request,
    q: str = Query(..., description="搜索关键词"),
    limit: int = Query(default=settings.YOUTUBE_SEARCH_RESULT_LIMIT, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    viewer: CurrentUser | None = Depends(get_public_viewer),
    _rl: None = Depends(_search_rate_limit),
) -> JSONResponse:
    """公开:按关键词搜索 YouTube。缓存优先(≤6h),miss 走 ytsearch flat 抓取并 upsert。"""
    if not await is_discover_enabled(db):
        raise BusinessError(ErrorCode.DISCOVER_DISABLED)
    normalized = search_cache.normalize_query(q)
    if not normalized or len(normalized) > 128:
        raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="q")

    bl = await blocklist_service.get_blocklist(db)
    if blocklist_service.is_term_blocked(normalized, bl):
        raise BusinessError(ErrorCode.YOUTUBE_SEARCH_QUERY_BLOCKED)

    # 人工屏蔽词未命中 → CMS 自动审核(公共边界:搜索输入)。off 态此调用即时短路、零开销。
    await moderation_gate.search_query(normalized, request_id=getattr(request.state, "trace_id", None))

    display = q.strip()
    cached = await search_cache.get_cached_results(db, normalized)
    if cached is not None:
        hits = cached
        was_cached = True
    else:
        # 失败抛 YOUTUBE_SEARCH_UNAVAILABLE,经全局 handler 转 200,不写负缓存
        hits = await YouTubeSearchService().search(display, limit)
        # 展示态过审管道:剔黑名单 → 放行表分流 → CMS filter_display → 复核队列 → 保序重建。
        # 缓存只存干净子集;enforce+degraded 抛 51400 不到 upsert(fail-closed)。off 态即时短路。
        request_id = getattr(request.state, "trace_id", None)
        hits, sensitive = await moderate_hits(db, hits, bl, request_id=request_id)
        await search_cache.upsert_results(db, normalized, display, hits, sensitive=sensitive)
        was_cached = False

    # 响应前剔除被拉黑频道:覆盖 cache-hit 路径(缓存存原始结果,拉黑读时即时);miss 路径上方已剔,此处幂等。
    hits = blocklist_service.filter_hits(hits, bl)

    # 「已有转写」感知:serve 时按 viewer 现算叠加(绝不进缓存);查询失败 fail-safe 原样返回。
    viewer_id = viewer.id if viewer is not None else None
    hits = await annotate_existing_tasks(db, hits, viewer_id)

    searcher_key = viewer.id if viewer is not None else _client_ip(request)
    await search_cache.register_query_heat(db, normalized, searcher_key)

    data = SearchData(query=display, items=hits, cached=was_cached)
    return success(data=jsonable_encoder(data))


@router.get("/search/trending")
async def youtube_trending(
    limit: int = Query(default=settings.YOUTUBE_TRENDING_TOP_N, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(_trending_rate_limit),
) -> JSONResponse:
    """公开:返回热门词。精选覆盖(admin 配置)已设时只返精选;否则回落近 7d top-N 组织化热度。"""
    if not await is_discover_enabled(db):
        raise BusinessError(ErrorCode.DISCOVER_DISABLED)
    # 精选覆盖:admin 配了干净列表就只返它(count 合成降序仅保序,前端 chips 只显示 query 文本)。
    curated = await get_curated_trending_queries(db)
    if curated is not None:
        picked = curated[:limit]
        data = TrendingData(items=[TrendingItemOut(query=q, count=len(picked) - idx) for idx, q in enumerate(picked)])
        return success(data=jsonable_encoder(data))
    items = await search_cache.get_trending(db)
    data = TrendingData(items=[TrendingItemOut(query=i.query, count=i.count) for i in items[:limit]])
    return success(data=jsonable_encoder(data))


@router.get("/recommendations")
async def youtube_recommendations(
    limit: int = Query(default=recommendation_service.RECOMMENDATIONS_TOP_N, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(_trending_rate_limit),
) -> JSONResponse:
    """公开:/discover 搜索前的「热门推荐」(harvest 定时按 view_count 排的快照)。

    受 discover kill-switch 门;读表 top-N 后 serve 时再套一次缓存黑名单(黑名单变更即时生效)。
    读空/读错 → items:[](前端回落轻量提示)。
    """
    if not await is_discover_enabled(db):
        raise BusinessError(ErrorCode.DISCOVER_DISABLED)
    hits = await recommendation_service.get_recommendations(db, limit)
    bl = await blocklist_service.get_blocklist(db)
    hits = blocklist_service.filter_hits(hits, bl)
    data = RecommendationData(items=hits)
    return success(data=jsonable_encoder(data))
