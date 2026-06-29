"""管理后台「看用户转写明细」只读端点组(/api/v1/admin/*)。

设计:../../../docs(ui 仓)/superpowers/specs/2026-06-29-admin-view-user-transcripts-design.md。
- 全部 get_admin_user 闸门;全部 SELECT-only(service 层保证,绝不触发惰性切分写)。
- 复用公开/私有出参 schema(转写/摘要/详情),仅列表项用 AdminUserTaskItem。
- 只读是结构性的:本路由组无任何 mutation 端点。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_admin_user, get_db
from app.core.response import success
from app.schemas.admin_task import AdminUserTaskItem
from app.schemas.common import PageResponse
from app.services.task_service import TaskService

router = APIRouter(prefix="/admin", tags=["admin-tasks"])


@router.get("/users/{user_id}/tasks")
async def list_user_tasks(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    admin: CurrentUser = Depends(get_admin_user),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
    status: str = Query(default="all"),
    q: str | None = Query(default=None),
) -> JSONResponse:
    items, total = await TaskService.list_user_tasks_for_admin(db, user_id, page, page_size, status, q)
    data = PageResponse[AdminUserTaskItem](items=items, total=total, page=page, page_size=page_size)
    return success(data=jsonable_encoder(data))


@router.get("/tasks/{task_id}")
async def admin_task_detail(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    admin: CurrentUser = Depends(get_admin_user),
) -> JSONResponse:
    detail = await TaskService.get_admin_task_detail(db, task_id)
    return success(data=jsonable_encoder(detail))


@router.get("/tasks/{task_id}/transcript")
async def admin_task_transcript(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    admin: CurrentUser = Depends(get_admin_user),
) -> JSONResponse:
    data = await TaskService.get_admin_task_transcript(db, task_id)
    return success(data=jsonable_encoder(data))


@router.get("/tasks/{task_id}/summary")
async def admin_task_summary(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    admin: CurrentUser = Depends(get_admin_user),
) -> JSONResponse:
    data = await TaskService.get_admin_task_summary(db, task_id)
    return success(data=jsonable_encoder(data))
