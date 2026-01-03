from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.response import success
from app.models.user import User
from app.schemas.common import PageResponse
from app.schemas.task import (
    TaskCreateRequest,
    TaskCreateResponse,
    TaskDetailResponse,
    TaskListItem,
)
from app.services.task_service import TaskService

router = APIRouter(prefix="/tasks")


@router.post("")
async def create_task(
    request: Request,
    data: TaskCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    trace_id = getattr(request.state, "trace_id", None)
    task = await TaskService.create_task(db, user, data, trace_id)
    response = TaskCreateResponse(
        id=task.id,
        status=task.status,
        progress=task.progress,
        created_at=task.created_at,
    )
    return success(data=jsonable_encoder(response))


@router.get("")
async def list_tasks(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str = Query(default="all"),
) -> JSONResponse:
    allowed_status = {
        "all",
        "pending",
        "queued",
        "resolving",
        "downloading",
        "downloaded",
        "transcoding",
        "uploading",
        "uploaded",
        "resolved",
        "processing",
        "asr_submitting",
        "asr_polling",
        "completed",
        "failed",
    }
    if status not in allowed_status:
        status = "all"
    items, total = await TaskService.list_tasks(db, user, page, page_size, status)
    response = PageResponse[TaskListItem](
        items=items, total=total, page=page, page_size=page_size
    )
    return success(data=jsonable_encoder(response))


@router.get("/{task_id}")
async def get_task_detail(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    detail = await TaskService.get_task_detail(db, user, task_id)
    response = TaskDetailResponse(**detail.model_dump())
    return success(data=jsonable_encoder(response))


@router.delete("/{task_id}")
async def delete_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    await TaskService.delete_task(db, user, task_id)
    return success(data=None)
