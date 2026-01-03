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
    TaskBatchDeleteRequest,
    TaskCreateRequest,
    TaskCreateResponse,
    TaskDetailResponse,
    TaskListItem,
    TaskRetryRequest,
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


@router.post("/{task_id}/retry")
async def retry_task(
    task_id: str,
    retry_request: TaskRetryRequest = TaskRetryRequest(),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """重试失败的任务.

    支持多种重试模式：
    - full: 完整重试（清空所有阶段，从头开始）
    - auto: 智能重试（自动从失败的阶段继续，默认）
    - from_transcribe: 从转写开始（复用下载/上传）
    - transcribe_only: 仅重新转写
    - summarize_only: 仅重新生成摘要

    其他逻辑：
    - 检查任务状态（只有 failed 状态才能重试）
    - 检查是否有相同内容的成功任务
    - 如果有成功任务，返回 duplicate_found 并自动跳转（不允许强制重试）
    - 如果没有成功任务，根据重试模式重新提交任务
    """
    result = await TaskService.retry_task(db, user, task_id, retry_request.mode)
    return success(data=jsonable_encoder(result))


@router.post("/batch-delete")
async def batch_delete_tasks(
    data: TaskBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """批量删除任务.

    - 批量软删除指定的任务
    - 只能删除属于当前用户的任务
    - 返回成功删除的数量和失败的任务ID列表
    """
    result = await TaskService.batch_delete_tasks(db, user, data.task_ids)
    return success(data=jsonable_encoder(result))
