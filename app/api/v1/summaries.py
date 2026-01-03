from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.exceptions import BusinessError
from app.core.response import success
from app.i18n.codes import ErrorCode
from app.models.summary import Summary
from app.models.task import Task
from app.models.user import User
from app.schemas.summary import SummaryItem, SummaryListResponse

router = APIRouter(prefix="/summaries")


@router.get("/{task_id}")
async def get_summaries(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    # Verify task exists and belongs to user
    stmt = select(Task).where(
        Task.id == task_id, Task.user_id == user.id, Task.deleted_at.is_(None)
    )
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()

    if not task:
        raise BusinessError(ErrorCode.TASK_NOT_FOUND)

    # Get all active summaries for this task
    stmt = (
        select(Summary)
        .where(Summary.task_id == task_id, Summary.is_active == True)
        .order_by(Summary.summary_type, Summary.version.desc())
    )
    result = await db.execute(stmt)
    summaries = result.scalars().all()

    items = [
        SummaryItem(
            id=str(s.id),
            summary_type=s.summary_type,
            version=s.version,
            is_active=s.is_active,
            content=s.content,
            model_used=s.model_used,
            prompt_version=s.prompt_version,
            token_count=s.token_count,
            created_at=s.created_at,
        )
        for s in summaries
    ]

    response = SummaryListResponse(task_id=task_id, total=len(items), items=items)
    return success(data=jsonable_encoder(response))
