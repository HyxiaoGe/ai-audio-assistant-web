"""匿名公开只读端点组(/api/v1/public/*)。

设计:docs/superpowers/specs/2026-06-10-public-shared-tasks-design.md。
- 零鉴权,与存量带鉴权端点物理隔离;资格统一收口在 TaskService.get_public_task。
- 出参只用 app/schemas/public.py 的白名单裁剪 schema。
- 全部端点挂按 IP 固定窗口限流(匿名无 user 可依)。
- 纯只读:不触碰 transcripts.py 那条带「懒拆分写副作用」的查询路径。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.config import settings
from app.core.rate_limit import rate_limit_by_ip
from app.core.response import success
from app.core.security import SCOPE_MEDIA, issue_scoped_token
from app.models.summary import Summary
from app.models.transcript import Transcript
from app.schemas.common import PageResponse
from app.schemas.public import (
    PublicSummaryImageItem,
    PublicSummaryItem,
    PublicSummaryListResponse,
    PublicTaskListItem,
    PublicTranscriptItem,
    PublicTranscriptListResponse,
)
from app.services.media_url import build_media_download_url, build_presigned_media_url
from app.services.summary.public_image import (
    PUBLIC_IMAGE_PRESIGN_EXPIRES as _PUBLIC_IMAGE_PRESIGN_EXPIRES,
)
from app.services.summary.public_image import (
    public_summary_image_url as _public_summary_image_url,
)
from app.services.task_service import TaskService

router = APIRouter(prefix="/public", tags=["public"])

_rate_limit = rate_limit_by_ip(limit=settings.RATE_LIMIT_PUBLIC_PER_MIN, scope="public_read")

# 公开 GET 成功路径的边缘缓存头(CF Cache Rule 在 dashboard 侧配,这里做 origin 准备)。
# 取舍(用户已拍板):取消公开后,文本(标题/转写/摘要)在命中过的边缘 PoP 残留
# ≤~2min(max-age+stale-while-revalidate);媒体票/音频/图签发每次过 is_public DB
# 复核即时失效,残余仅纯文本。
_PUBLIC_CACHE_CONTROL = "public, max-age=60, s-maxage=60, stale-while-revalidate=60"


def _cacheable(resp: JSONResponse) -> JSONResponse:
    """只给「业务成功(code==0)」响应贴边缘缓存头。

    本项目错误信封是 HTTP 200(TASK_NOT_FOUND/限流/DB 错全走异常处理器返回
    200+错误 code),绝不能让错误信封被边缘缓存——否则刚公开的任务会在 PoP 上缓存
    「不存在」长达 max-age。隔离方式:头只贴在路由函数体内 success() 构造出的响应
    对象上;任何异常在 return 前 raise,走 app/main.py 的异常处理器重新构造响应
    (error() 不带本头)。注意 FastAPI 对「路由直接返回 Response」不会合并注入的
    response 参数头(fastapi/routing.py raw_response 分支),所以必须贴在这里。
    """
    resp.headers["Cache-Control"] = _PUBLIC_CACHE_CONTROL
    return resp


@router.get("/tasks")
async def list_public_tasks(
    db: AsyncSession = Depends(get_db),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
    _rl: None = Depends(_rate_limit),
) -> JSONResponse:
    """公开任务列表(仅 is_public+completed+未删),按发布时间倒序分页。"""
    items, total = await TaskService.list_public_tasks(db, page, page_size)
    response = PageResponse[PublicTaskListItem](items=items, total=total, page=page, page_size=page_size)
    return _cacheable(success(data=jsonable_encoder(response)))


@router.get("/tasks/{task_id}")
async def get_public_task_detail(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(_rate_limit),
) -> JSONResponse:
    """公开任务详情(白名单字段;非公开/不存在/未完成一律 TASK_NOT_FOUND)。"""
    detail = await TaskService.get_public_task_detail(db, task_id)
    return _cacheable(success(data=jsonable_encoder(detail)))


@router.get("/tasks/{task_id}/transcripts")
async def get_public_transcripts(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(_rate_limit),
) -> JSONResponse:
    """公开任务转写(裁剪字段,纯只读——不走私有端点的懒拆分写路径)。"""
    task = await TaskService.get_public_task(db, task_id)
    rows = (
        (await db.execute(select(Transcript).where(Transcript.task_id == task.id).order_by(Transcript.sequence)))
        .scalars()
        .all()
    )
    items = [
        PublicTranscriptItem(
            sequence=row.sequence,
            speaker_id=row.speaker_id,
            speaker_label=row.speaker_label,
            content=row.content,
            start_time=float(row.start_time),
            end_time=float(row.end_time),
        )
        for row in rows
    ]
    # 转写是公开端点里唯一的大响应(实测最大 ~260KB),序列化热点在 jsonable_encoder
    # (逐字段反射递归,实测 10-44ms);model_dump(mode="json") 走 pydantic-core 原生
    # 序列化,产物等价(schema 无 alias/自定义 encoder,字段全是 str/int/float)。
    # 其余小响应端点不值得动,保持 jsonable_encoder。
    return _cacheable(
        success(
            data=PublicTranscriptListResponse(task_id=str(task.id), total=len(items), items=items).model_dump(
                mode="json"
            )
        )
    )


@router.get("/tasks/{task_id}/summaries")
async def get_public_summaries(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(_rate_limit),
) -> JSONResponse:
    """公开任务摘要(active 版本;配图集裁掉 model_id/error)。"""
    task = await TaskService.get_public_task(db, task_id)
    rows = (
        (
            await db.execute(
                select(Summary)
                .where(Summary.task_id == task.id, Summary.is_active.is_(True))
                .order_by(Summary.summary_type)
            )
        )
        .scalars()
        .all()
    )
    items: list[PublicSummaryItem] = []
    for summary in rows:
        image_url = None
        if summary.image_key:
            # 旧式单图:优先 OSS 直链,签发失败回落同源代理 URL(别让整个摘要 500)
            image_url = await build_presigned_media_url(summary.image_key, _PUBLIC_IMAGE_PRESIGN_EXPIRES)
            if image_url is None:
                image_url = await build_media_download_url(summary.image_key, task.user_id)
        images: list[PublicSummaryImageItem] | None = None
        if summary.images:
            images = []
            for item in summary.images:
                status = str(item.get("status", "pending"))
                raw_url = item.get("url") if isinstance(item.get("url"), str) else None
                final_url = await _public_summary_image_url(raw_url, status)
                images.append(
                    PublicSummaryImageItem(
                        placeholder=str(item.get("placeholder", "")),
                        status=status,
                        url=final_url,
                        # 换发直链成功(url 已非原代理路径)才给回落字段;url 仍是代理时为 None
                        proxy_url=raw_url if (final_url and raw_url and final_url != raw_url) else None,
                        alt=str(item.get("alt", "")),
                    )
                )
        items.append(
            PublicSummaryItem(
                summary_type=summary.summary_type,
                version=summary.version,
                content=summary.content,
                image_url=image_url,
                images=images,
                created_at=summary.created_at,
            )
        )
    return _cacheable(
        success(data=jsonable_encoder(PublicSummaryListResponse(task_id=str(task.id), total=len(items), items=items)))
    )


@router.post("/tasks/{task_id}/media-ticket")
async def mint_public_media_ticket(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(_rate_limit),
) -> JSONResponse:
    """为公开任务签发短期媒体票(匿名可签)。

    sub=任务 owner(媒体 key 第二段是 owner id,过 assert_owns_media_key 双保险),
    resource 钉死 public_task——媒体端点见 pin 即走「仍公开 + key∈允许集」DB 复核,
    绝不等价于裸 owner 票(那会解锁该用户全部媒体命名空间)。
    """
    task = await TaskService.get_public_task(db, task_id)
    token = issue_scoped_token(
        sub=task.user_id,
        scope=SCOPE_MEDIA,
        ttl=settings.MEDIA_TOKEN_TTL,
        resource={"public_task": str(task.id)},
    )
    return success(data={"token": token, "expires_in": settings.MEDIA_TOKEN_TTL})
