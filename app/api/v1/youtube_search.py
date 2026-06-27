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
from app.schemas.youtube_search import SearchData, TrendingData, TrendingItemOut
from app.services.moderation import gate as moderation_gate
from app.services.youtube import blocklist_service, search_cache
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
        # 展示态审核:剔除 block 项后再 upsert → 缓存只存干净子集;
        # enforce+degraded 在此抛 51400 → 不到 upsert、不缓存(fail-closed)。off 态即时短路。
        hits = await moderation_gate.filter_display(hits, request_id=getattr(request.state, "trace_id", None))
        await search_cache.upsert_results(db, normalized, display, hits)
        was_cached = False

    # 响应前剔除被拉黑频道:缓存存原始结果,过滤只在读时 → 拉黑/解禁即时,旧缓存不泄露。
    hits = blocklist_service.filter_hits(hits, bl)

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
    """公开:返回近 7d top-N 热门词;不同查询数不足阈值时 get_trending 已返空。"""
    items = await search_cache.get_trending(db)
    data = TrendingData(items=[TrendingItemOut(query=i.query, count=i.count) for i in items[:limit]])
    return success(data=jsonable_encoder(data))
