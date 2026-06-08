from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db
from app.config import settings
from app.core.rate_limit import rate_limit
from app.core.response import success
from app.schemas.common import PageResponse
from app.schemas.task import (
    TaskBatchDeleteRequest,
    TaskCreateRequest,
    TaskCreateResponse,
    TaskDetailResponse,
    TaskListItem,
    TaskStatusCountsResponse,
)
from app.services.task_service import PROCESSING_STATUSES, TaskService

router = APIRouter(prefix="/tasks")

# 列表筛选可接受的 status 取值：聚合关键字 "processing" + 终态 + 全部"处理中"子状态。
# 派生自单一事实源 PROCESSING_STATUSES，避免与 list_tasks 的伞形筛选漂移（曾漏 polishing
# 导致润色中的任务用 ?status=polishing 被静默回退为 "all"）。非白名单值一律回退 "all"。
_LIST_STATUS_FILTERS: frozenset[str] = frozenset(
    {"all", "processing", "completed", "failed", *PROCESSING_STATUSES}
)


@router.post("")
async def create_task(
    request: Request,
    data: TaskCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _rl: None = Depends(rate_limit(limit=settings.RATE_LIMIT_TASK_CREATE_PER_MIN, scope="task_create")),
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
    user: CurrentUser = Depends(get_current_user),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str = Query(default="all"),
) -> JSONResponse:
    if status not in _LIST_STATUS_FILTERS:
        status = "all"
    items, total = await TaskService.list_tasks(db, user, page, page_size, status)
    response = PageResponse[TaskListItem](items=items, total=total, page=page, page_size=page_size)
    return success(data=jsonable_encoder(response))


@router.get("/status-counts")
async def get_task_status_counts(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """列表页筛选 tab 的状态计数（all / processing / completed / failed）。

    一次 GROUP BY 返回全部计数，替代前端为四个 tab 各发一次 page_size=1 查询。
    注意：路由须声明在 ``/{task_id}`` 之前，否则会被动态段捕获。
    """
    counts = await TaskService.get_status_counts(db, user)
    return success(data=jsonable_encoder(TaskStatusCountsResponse(**counts)))


@router.get("/{task_id}")
async def get_task_detail(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    detail = await TaskService.get_task_detail(db, user, task_id)
    response = TaskDetailResponse(**detail.model_dump())
    return success(data=jsonable_encoder(response))


@router.delete("/{task_id}")
async def delete_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    await TaskService.delete_task(db, user, task_id)
    return success(data=None)


@router.post("/{task_id}/retry")
async def retry_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """重试失败的任务.

    其他逻辑：
    - 检查任务状态（只有 failed 状态才能重试）
    - 检查是否有相同内容的成功任务
    - 如果有成功任务，返回 duplicate_found 并自动跳转（不允许强制重试）
    - 如果没有成功任务，自动智能重试
    """
    result = await TaskService.retry_task(db, user, task_id)
    return success(data=jsonable_encoder(result))


@router.post("/batch-delete")
async def batch_delete_tasks(
    data: TaskBatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """批量删除任务.

    - 批量软删除指定的任务
    - 只能删除属于当前用户的任务
    - 返回成功删除的数量和失败的任务ID列表
    """
    result = await TaskService.batch_delete_tasks(db, user, data.task_ids)
    return success(data=jsonable_encoder(result))
