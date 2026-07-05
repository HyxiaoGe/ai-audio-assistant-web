from __future__ import annotations

import re
from typing import TypedDict

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db
from app.core.exceptions import BusinessError
from app.core.response import success
from app.i18n.codes import ErrorCode
from app.models.task import Task
from app.models.transcript import Transcript
from app.schemas.transcript import (
    TranscriptItem,
    TranscriptListResponse,
    TranscriptSegmentUpdateRequest,
)

router = APIRouter(prefix="/transcripts")

_TIMESTAMP_PATTERN = re.compile(r"\[(\d+):(\d+(?:\.\d+)?),(\d+):(\d+(?:\.\d+)?),(\d+)\]\s*(.*)")


class _TranscriptSegment(TypedDict):
    speaker_id: str
    start_time: float
    end_time: float
    content: str


def _split_timestamped_transcript(content: str) -> list[_TranscriptSegment]:
    segments: list[_TranscriptSegment] = []
    for match in _TIMESTAMP_PATTERN.finditer(content):
        start_min, start_sec, end_min, end_sec, speaker_id, text = match.groups()
        start_time = float(start_min) * 60 + float(start_sec)
        end_time = float(end_min) * 60 + float(end_sec)
        segments.append(
            {
                "speaker_id": speaker_id,
                "start_time": start_time,
                "end_time": end_time,
                "content": text.strip(),
            }
        )
    return segments


@router.get("/{task_id}")
async def get_transcripts(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    # Verify task exists and belongs to user
    task_stmt = select(Task).where(Task.id == task_id, Task.user_id == user.id, Task.deleted_at.is_(None))
    task_result = await db.execute(task_stmt)
    task = task_result.scalar_one_or_none()

    if not task:
        raise BusinessError(ErrorCode.TASK_NOT_FOUND)

    # Get all transcripts for this task
    transcript_stmt = select(Transcript).where(Transcript.task_id == task_id).order_by(Transcript.sequence)
    transcript_result = await db.execute(transcript_stmt)
    transcripts = transcript_result.scalars().all()

    if len(transcripts) == 1:
        content = transcripts[0].content
        segments = _split_timestamped_transcript(content)
        if segments:
            await db.execute(delete(Transcript).where(Transcript.task_id == task_id))
            for idx, seg in enumerate(segments, start=1):
                db.add(
                    Transcript(
                        task_id=task_id,
                        speaker_id=str(seg["speaker_id"]),
                        speaker_label=None,
                        content=str(seg["content"]),
                        start_time=float(seg["start_time"]),
                        end_time=float(seg["end_time"]),
                        confidence=None,
                        sequence=idx,
                        is_edited=False,
                        original_content=None,
                    )
                )
            await db.commit()
            transcript_result = await db.execute(transcript_stmt)
            transcripts = transcript_result.scalars().all()

    items = [_to_item(t) for t in transcripts]

    response = TranscriptListResponse(task_id=task_id, total=len(items), items=items)
    return success(data=jsonable_encoder(response))


def _to_item(t: Transcript) -> TranscriptItem:
    return TranscriptItem(
        id=str(t.id),
        speaker_id=t.speaker_id,
        speaker_label=t.speaker_label,
        content=t.content,
        start_time=float(t.start_time),
        end_time=float(t.end_time),
        confidence=float(t.confidence) if t.confidence else None,
        words=t.words,
        sequence=t.sequence,
        is_edited=t.is_edited,
        original_content=t.original_content,
        manually_edited=t.manually_edited,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


@router.patch("/{task_id}/segments/{segment_id}")
async def update_transcript_segment(
    task_id: str,
    segment_id: str,
    data: TranscriptSegmentUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """手动编辑单个转写段落内容。

    - 归属校验:任务须属于当前用户且未软删;段落须属于该任务(防跨任务改)。
    - 首次编辑把旧值存进 original_content;二次编辑保留最早原文。
    - 置 is_edited=True(内容已不同于 ASR 原文)且 manually_edited=True(来源=人工),
      前端据此显示「已编辑」而非「AI 已校对」,不与 AI 校对混淆。
    """
    task_stmt = select(Task).where(Task.id == task_id, Task.user_id == user.id, Task.deleted_at.is_(None))
    task = (await db.execute(task_stmt)).scalar_one_or_none()
    if not task:
        raise BusinessError(ErrorCode.TASK_NOT_FOUND)

    segment_stmt = select(Transcript).where(Transcript.id == segment_id, Transcript.task_id == task_id)
    segment = (await db.execute(segment_stmt)).scalar_one_or_none()
    if segment is None:
        raise BusinessError(ErrorCode.TRANSCRIPT_NOT_FOUND)

    if not segment.is_edited:
        segment.original_content = segment.content
    segment.content = data.content
    segment.is_edited = True
    segment.manually_edited = True

    # 在 commit 前用内存态构建响应,规避 async expire_on_commit 的惰性加载陷阱
    item = _to_item(segment)
    await db.commit()
    return success(data=jsonable_encoder(item))
