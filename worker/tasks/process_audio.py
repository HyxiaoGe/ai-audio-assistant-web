from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import BusinessError
from app.core.redis import publish_message
from app.db import async_session_factory
from app.i18n.codes import ErrorCode
from app.models.summary import Summary
from app.models.task import Task
from app.models.transcript import Transcript
from app.services.asr.factory import get_asr_service
from app.services.llm.factory import get_llm_service
from app.services.storage.factory import get_storage_service
from worker.celery_app import celery_app

logger = logging.getLogger("worker.process_audio")


async def _get_task(session: AsyncSession, task_id: str) -> Optional[Task]:
    result = await session.execute(
        select(Task).where(Task.id == task_id, Task.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def _update_task(
    session: AsyncSession,
    task: Task,
    status: str,
    progress: int,
    stage: Optional[str],
    request_id: Optional[str],
) -> None:
    task.status = status
    task.progress = max(task.progress or 0, progress)
    task.stage = stage
    if request_id:
        task.request_id = request_id
    await session.commit()
    trace_id = request_id or uuid4().hex
    message = json.dumps(
        {
            "code": 0,
            "message": "成功",
            "data": {
                "type": "completed" if status == "completed" else "progress",
                "status": status,
                "stage": stage,
                "progress": progress,
                "task_id": task.id,
                "request_id": request_id,
            },
            "traceId": trace_id,
        }
    )
    await publish_message(f"tasks:{task.id}", message)


async def _mark_failed(
    session: AsyncSession, task: Task, error: BusinessError, request_id: Optional[str]
) -> None:
    task.status = "failed"
    task.progress = 0
    task.error_code = error.code.value
    task.error_message = error.kwargs.get("reason") or str(error)
    if request_id:
        task.request_id = request_id
    await session.commit()
    trace_id = request_id or uuid4().hex
    message = json.dumps(
        {
            "code": error.code.value,
            "message": str(error),
            "data": {"type": "error", "status": "failed", "task_id": task.id},
            "traceId": trace_id,
        }
    )
    await publish_message(f"tasks:{task.id}", message)


async def _process_task(task_id: str, request_id: Optional[str]) -> None:
    async with async_session_factory() as session:
        task = await _get_task(session, task_id)
        if task is None:
            logger.warning("task not found: %s", task_id)
            return

        try:
            await _update_task(session, task, "extracting", 10, "extracting", request_id)

            audio_candidates: list[str] = []
            if task.source_type == "upload":
                if not task.source_key:
                    raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="source_key")
                expires_in = settings.UPLOAD_PRESIGN_EXPIRES
                if not expires_in:
                    raise BusinessError(
                        ErrorCode.INVALID_PARAMETER, detail="upload_presign_expires"
                    )
                storage_service = get_storage_service()
                audio_candidates.append(
                    storage_service.generate_presigned_url(task.source_key, expires_in)
                )
            else:
                if task.source_key:
                    expires_in = settings.UPLOAD_PRESIGN_EXPIRES
                    if not expires_in:
                        raise BusinessError(
                            ErrorCode.INVALID_PARAMETER, detail="upload_presign_expires"
                        )
                    storage_service = get_storage_service()
                    audio_candidates.append(
                        storage_service.generate_presigned_url(
                            task.source_key, expires_in
                        )
                    )
                direct_url = None
                if isinstance(task.source_metadata, dict):
                    direct_url = task.source_metadata.get("direct_url")
                if isinstance(direct_url, str) and direct_url:
                    audio_candidates.append(direct_url)
                if not audio_candidates:
                    if not task.source_url:
                        raise BusinessError(
                            ErrorCode.INVALID_PARAMETER, detail="source_url"
                        )
                    audio_candidates.append(task.source_url)

            await _update_task(session, task, "transcribing", 40, "transcribing", request_id)
            asr_service = get_asr_service()
            last_error: Optional[BusinessError] = None
            segments: list[TranscriptSegment] = []

            async def _asr_status(stage: str) -> None:
                if stage == "asr_submitting":
                    await _update_task(
                        session, task, "asr_submitting", 45, "asr_submitting", request_id
                    )
                elif stage == "asr_polling":
                    await _update_task(
                        session, task, "asr_polling", 55, "asr_polling", request_id
                    )

            for audio_url in audio_candidates:
                try:
                    segments = await asr_service.transcribe(
                        audio_url, status_callback=_asr_status
                    )
                    last_error = None
                    break
                except BusinessError as exc:
                    last_error = exc
                    if exc.code not in {
                        ErrorCode.ASR_SERVICE_FAILED,
                        ErrorCode.ASR_SERVICE_TIMEOUT,
                        ErrorCode.ASR_SERVICE_UNAVAILABLE,
                    }:
                        raise
                    logger.warning(
                        "asr failed for url, trying fallback: %s", exc.code.value
                    )
            if last_error is not None and not segments:
                raise last_error

            transcripts = []
            for idx, segment in enumerate(segments, start=1):
                transcripts.append(
                    Transcript(
                        task_id=task.id,
                        speaker_id=segment.speaker_id,
                        speaker_label=None,
                        content=segment.content,
                        start_time=segment.start_time,
                        end_time=segment.end_time,
                        confidence=segment.confidence,
                        sequence=idx,
                        is_edited=False,
                        original_content=None,
                    )
                )
            session.add_all(transcripts)
            await session.commit()

            await _update_task(session, task, "summarizing", 80, "summarizing", request_id)
            llm_service = get_llm_service()
            full_text = "\n".join([seg.content for seg in segments])

            summaries = []
            for summary_type in ("overview", "key_points", "action_items"):
                content = await llm_service.summarize(full_text, summary_type)
                summaries.append(
                    Summary(
                        task_id=task.id,
                        summary_type=summary_type,
                        version=1,
                        is_active=True,
                        content=content,
                        model_used=llm_service.model_name,
                        prompt_version=None,
                        token_count=None,
                    )
                )
            session.add_all(summaries)
            await session.commit()

            task.error_code = None
            task.error_message = None
            await _update_task(session, task, "completed", 100, "completed", request_id)
        except BusinessError as exc:
            await _mark_failed(session, task, exc, request_id)
        except Exception as exc:
            logger.exception("process_audio failed: %s", exc)
            error = BusinessError(ErrorCode.INTERNAL_SERVER_ERROR)
            await _mark_failed(session, task, error, request_id)


@celery_app.task(
    name="worker.tasks.process_audio",
    bind=True,
    max_retries=3,
    soft_time_limit=1800,
    hard_time_limit=2000,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def process_audio(self, task_id: str, request_id: Optional[str] = None) -> None:
    asyncio.run(_process_task(task_id, request_id))
