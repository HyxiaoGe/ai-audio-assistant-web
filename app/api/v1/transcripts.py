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
from app.models.task import Task
from app.models.transcript import Transcript
from app.models.user import User
from app.schemas.transcript import TranscriptItem, TranscriptListResponse

router = APIRouter(prefix="/transcripts")


@router.get("/{task_id}")
async def get_transcripts(
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

    # Get all transcripts for this task
    stmt = (
        select(Transcript)
        .where(Transcript.task_id == task_id)
        .order_by(Transcript.sequence)
    )
    result = await db.execute(stmt)
    transcripts = result.scalars().all()

    items = [
        TranscriptItem(
            id=str(t.id),
            speaker_id=t.speaker_id,
            speaker_label=t.speaker_label,
            content=t.content,
            start_time=float(t.start_time),
            end_time=float(t.end_time),
            confidence=float(t.confidence) if t.confidence else None,
            sequence=t.sequence,
            is_edited=t.is_edited,
            original_content=t.original_content,
            created_at=t.created_at,
            updated_at=t.updated_at,
        )
        for t in transcripts
    ]

    response = TranscriptListResponse(
        task_id=task_id, total=len(items), items=items
    )
    return success(data=jsonable_encoder(response))
