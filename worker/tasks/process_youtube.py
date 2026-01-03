from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

from yt_dlp import YoutubeDL
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.core.redis import publish_message
from app.db import async_session_factory
from app.models.task import Task
from app.services.storage.factory import get_storage_service
from worker.celery_app import celery_app

logger = logging.getLogger("worker.process_youtube")


def _get_download_dir() -> Path:
    raw_dir = settings.YOUTUBE_DOWNLOAD_DIR
    if not raw_dir:
        raise RuntimeError("YOUTUBE_DOWNLOAD_DIR is not set")
    path = Path(raw_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _get_output_template() -> str:
    template = settings.YOUTUBE_OUTPUT_TEMPLATE
    if not template:
        raise RuntimeError("YOUTUBE_OUTPUT_TEMPLATE is not set")
    return template


def _get_download_format() -> str:
    fmt = settings.YOUTUBE_DOWNLOAD_FORMAT
    if not fmt:
        raise RuntimeError("YOUTUBE_DOWNLOAD_FORMAT is not set")
    return fmt


def _build_file_key(filename: str) -> str:
    now = datetime.now(timezone.utc)
    safe_name = Path(filename).name.replace(" ", "_")
    return f"uploads/youtube/{now:%Y/%m}/{uuid4().hex}_{safe_name}"


def _extract_direct_url(info: dict) -> Optional[str]:
    url = info.get("url")
    if isinstance(url, str) and url:
        return url
    requested = info.get("requested_formats")
    if isinstance(requested, list):
        for fmt in requested:
            if isinstance(fmt, dict):
                candidate = fmt.get("url")
                if isinstance(candidate, str) and candidate:
                    return candidate
    formats = info.get("formats")
    if isinstance(formats, list):
        for fmt in formats:
            if isinstance(fmt, dict):
                candidate = fmt.get("url")
                if isinstance(candidate, str) and candidate:
                    return candidate
    return None


def _extract_youtube_info(url: str) -> tuple[Optional[str], Optional[str]]:
    output_dir = _get_download_dir()
    outtmpl = str(output_dir / _get_output_template())
    fmt = _get_download_format()
    ydl_opts = {
        "format": fmt,
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        title = info.get("title") if isinstance(info, dict) else None
        direct_url = info if isinstance(info, str) else _extract_direct_url(info)
        return direct_url, title


def _download_youtube(url: str, progress_callback=None) -> str:
    output_dir = _get_download_dir()
    outtmpl = str(output_dir / _get_output_template())
    fmt = _get_download_format()
    ydl_opts = {
        "format": fmt,
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
    }
    if progress_callback is not None:
        ydl_opts["progress_hooks"] = [progress_callback]
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)


def _transcode_to_wav_16k(input_path: str) -> str:
    output_path = str(Path(input_path).with_suffix(".wav"))
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            input_path,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-acodec",
            "pcm_s16le",
            output_path,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise BusinessError(ErrorCode.FILE_PROCESSING_ERROR, reason=detail)
    return output_path


async def _get_task(session: AsyncSession, task_id: str) -> Optional[Task]:
    result = await session.execute(
        select(Task).where(Task.id == task_id, Task.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def _update_metadata(
    session: AsyncSession,
    task: Task,
    direct_url: Optional[str],
    title: Optional[str],
) -> None:
    metadata = dict(task.source_metadata or {})
    if direct_url:
        metadata["direct_url"] = direct_url
    if title and not task.title:
        task.title = title
    task.source_metadata = metadata
    await session.commit()


async def _update_source_key(
    session: AsyncSession,
    task: Task,
    source_key: str,
) -> None:
    task.source_key = source_key
    await session.commit()


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
                "type": "progress",
                "status": status,
                "stage": stage,
                "progress": task.progress,
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


async def _process_youtube(task_id: str, request_id: Optional[str]) -> None:
    async with async_session_factory() as session:
        task = await _get_task(session, task_id)
        if task is None:
            logger.warning("task not found: %s", task_id)
            return
        if task.source_type != "youtube":
            logger.warning("task source_type is not youtube: %s", task_id)
            return
        if not task.source_url:
            await _mark_failed(
                session,
                task,
                BusinessError(ErrorCode.INVALID_PARAMETER, detail="source_url"),
                request_id,
            )
            return
        await _update_task(session, task, "resolving", 5, "resolving", request_id)

    filename = None
    original_filename = None
    direct_url = None
    title = None
    try:
        direct_url, title = _extract_youtube_info(task.source_url)
    except Exception as exc:
        logger.exception("youtube download failed: %s", exc)
        if isinstance(exc, BusinessError):
            error = exc
        else:
            error = BusinessError(ErrorCode.YOUTUBE_DOWNLOAD_FAILED, reason=str(exc))
        async with async_session_factory() as session:
            task = await _get_task(session, task_id)
            if task is None:
                return
            await _mark_failed(session, task, error, request_id)
        return

    async with async_session_factory() as session:
        task = await _get_task(session, task_id)
        if task is None:
            return
        await _update_metadata(session, task, direct_url, title)
        await _update_task(session, task, "downloading", 15, "downloading", request_id)

    try:
        loop = asyncio.get_running_loop()
        last_progress = 15
        last_emit = 0.0

        async def _emit_progress(progress: int) -> None:
            async with async_session_factory() as session:
                task = await _get_task(session, task_id)
                if task is None:
                    return
                await _update_task(
                    session, task, "downloading", progress, "downloading", request_id
                )

        def _progress_hook(payload: dict) -> None:
            nonlocal last_progress, last_emit
            if payload.get("status") != "downloading":
                return
            total = payload.get("total_bytes") or payload.get("total_bytes_estimate")
            downloaded = payload.get("downloaded_bytes")
            if not total or not downloaded:
                return
            ratio = min(max(downloaded / total, 0.0), 1.0)
            mapped = 15 + int(ratio * 10)
            if mapped <= last_progress:
                return
            now = time.monotonic()
            if now - last_emit < 1.0:
                return
            last_progress = mapped
            last_emit = now
            loop.call_soon_threadsafe(
                lambda: asyncio.create_task(_emit_progress(mapped))
            )

        original_filename = _download_youtube(
            task.source_url, progress_callback=_progress_hook
        )
        if not original_filename:
            raise BusinessError(
                ErrorCode.FILE_PROCESSING_ERROR, reason="download produced empty file"
            )
    except Exception as exc:
        logger.exception("youtube download failed: %s", exc)
        if not direct_url:
            if isinstance(exc, BusinessError):
                error = exc
            else:
                error = BusinessError(ErrorCode.YOUTUBE_DOWNLOAD_FAILED, reason=str(exc))
            async with async_session_factory() as session:
                task = await _get_task(session, task_id)
                if task is None:
                    return
                await _mark_failed(session, task, error, request_id)
            return

    async with async_session_factory() as session:
        task = await _get_task(session, task_id)
        if task is None:
            return
        await _update_task(session, task, "downloaded", 25, "downloaded", request_id)

    try:
        async with async_session_factory() as session:
            task = await _get_task(session, task_id)
            if task is None:
                return
            await _update_task(
                session, task, "transcoding", 27, "transcoding", request_id
            )
        filename = _transcode_to_wav_16k(original_filename)
    except Exception as exc:
        logger.exception("youtube transcode failed: %s", exc)
        async with async_session_factory() as session:
            task = await _get_task(session, task_id)
            if task is None:
                return
            if isinstance(exc, BusinessError):
                error = exc
            else:
                error = BusinessError(ErrorCode.FILE_PROCESSING_ERROR, reason=str(exc))
            await _mark_failed(session, task, error, request_id)
        return

    source_key = None
    if filename:
        try:
            storage_service = get_storage_service()
            source_key = _build_file_key(filename)
            async with async_session_factory() as session:
                task = await _get_task(session, task_id)
                if task is None:
                    return
                await _update_task(
                    session, task, "uploading", 30, "uploading", request_id
                )
            storage_service.upload_file(source_key, filename)
        except Exception as exc:
            logger.exception("storage upload failed: %s", exc)
            source_key = None
        finally:
            if original_filename:
                Path(original_filename).unlink(missing_ok=True)
            Path(filename).unlink(missing_ok=True)

    if source_key:
        async with async_session_factory() as session:
            task = await _get_task(session, task_id)
            if task is None:
                return
            await _update_source_key(session, task, source_key)
            await _update_task(session, task, "uploaded", 35, "uploaded", request_id)
    elif not direct_url:
        async with async_session_factory() as session:
            task = await _get_task(session, task_id)
            if task is None:
                return
            await _mark_failed(
                session,
                task,
                BusinessError(ErrorCode.FILE_UPLOAD_FAILED),
                request_id,
            )
        return
    else:
        async with async_session_factory() as session:
            task = await _get_task(session, task_id)
            if task is None:
                return
            await _update_task(session, task, "resolved", 30, "resolved", request_id)

    async with async_session_factory() as session:
        task = await _get_task(session, task_id)
        if task is None:
            return
        await _update_task(session, task, "queued", 40, "queued", request_id)

    celery_app.send_task(
        "worker.tasks.process_audio",
        args=[task_id],
        kwargs={"request_id": request_id},
    )


@celery_app.task(
    name="worker.tasks.process_youtube",
    bind=True,
    max_retries=3,
    soft_time_limit=1800,
    hard_time_limit=2000,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def process_youtube(self, task_id: str, request_id: Optional[str] = None) -> None:
    return asyncio.run(_process_youtube(task_id, request_id))
