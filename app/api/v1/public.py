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
from app.services.media_url import build_media_download_url
from app.services.task_service import TaskService

router = APIRouter(prefix="/public", tags=["public"])

_rate_limit = rate_limit_by_ip(limit=settings.RATE_LIMIT_PUBLIC_PER_MIN, scope="public_read")


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
    return success(data=jsonable_encoder(response))


@router.get("/tasks/{task_id}")
async def get_public_task_detail(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(_rate_limit),
) -> JSONResponse:
    """公开任务详情(白名单字段;非公开/不存在/未完成一律 TASK_NOT_FOUND)。"""
    detail = await TaskService.get_public_task_detail(db, task_id)
    return success(data=jsonable_encoder(detail))


@router.get("/tasks/{task_id}/transcripts")
async def get_public_transcripts(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(_rate_limit),
) -> JSONResponse:
    """公开任务转写(裁剪字段,纯只读——不走私有端点的懒拆分写路径)。"""
    task = await TaskService.get_public_task(db, task_id)
    rows = (
        (
            await db.execute(
                select(Transcript).where(Transcript.task_id == task.id).order_by(Transcript.sequence)
            )
        )
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
    return success(
        data=jsonable_encoder(
            PublicTranscriptListResponse(task_id=str(task.id), total=len(items), items=items)
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
            image_url = await build_media_download_url(summary.image_key, task.user_id)
        images: list[PublicSummaryImageItem] | None = None
        if summary.images:
            images = [
                PublicSummaryImageItem(
                    placeholder=str(item.get("placeholder", "")),
                    status=str(item.get("status", "pending")),
                    url=item.get("url") if isinstance(item.get("url"), str) else None,
                    alt=str(item.get("alt", "")),
                )
                for item in summary.images
            ]
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
    return success(
        data=jsonable_encoder(PublicSummaryListResponse(task_id=str(task.id), total=len(items), items=items))
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
