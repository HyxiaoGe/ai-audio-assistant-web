from __future__ import annotations

import asyncio
import inspect
import json
import logging
import random
import re
import subprocess  # nosec B404
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session
from yt_dlp import YoutubeDL

from app.config import settings
from app.core.config_manager import ConfigManager
from app.core.exceptions import BusinessError
from app.core.registry import ServiceRegistry
from app.core.smart_factory import SmartFactory
from app.core.task_stages import StageType
from app.i18n.codes import ErrorCode
from app.models.llm_usage import LLMUsage
from app.models.summary import Summary
from app.models.task import Task
from app.models.transcript import Transcript
from app.services.asr.base import TranscriptSegment, WordTimestamp
from app.services.asr_quota_service import (
    get_quota_providers_sync,
    record_usage_sync,
    select_available_provider_sync,
)
from app.services.rag import ingest_task_chunks_sync
from worker.celery_app import celery_app
from worker.db import get_sync_db_session
from worker.redis_client import publish_task_update_sync
from worker.stage_manager import StageManager

logger = logging.getLogger("worker.process_youtube")


def _load_llm_model_id(provider: str, user_id: Optional[str]) -> Optional[str]:
    try:
        config = ConfigManager.get_config("llm", provider, user_id=user_id)
    except Exception:
        return None
    return getattr(config, "model", None)


def _select_default_llm_provider() -> str:
    providers = ServiceRegistry.list_services("llm")
    if not providers:
        raise ValueError("No available llm service found")
    providers.sort(
        key=lambda name: ServiceRegistry.get_metadata("llm", name).priority,
    )
    return providers[0]


def _resolve_llm_selection(task: Task, user_id: Optional[str]) -> tuple[str, str]:
    options = task.options or {}
    raw_provider = options.get("llm_provider") or options.get("provider")
    raw_model_id = options.get("llm_model_id") or options.get("model_id")
    provider = raw_provider if isinstance(raw_provider, str) else None
    model_id = raw_model_id if isinstance(raw_model_id, str) else None
    if provider:
        model_id = model_id or _load_llm_model_id(provider, user_id) or provider
        return provider, model_id
    provider = _select_default_llm_provider()
    model_id = _load_llm_model_id(provider, user_id) or provider
    return provider, model_id


def _resolve_asr_provider(task: Task) -> Optional[str]:
    options = task.options or {}
    raw_provider = options.get("asr_provider")
    return raw_provider if isinstance(raw_provider, str) else None


def _resolve_asr_variant(task: Task) -> str:
    options = task.options or {}
    raw_variant = options.get("asr_variant")
    return raw_variant if isinstance(raw_variant, str) and raw_variant else "file"


def _select_asr_provider_by_quota(
    session: Session,
    owner_user_id: Optional[str],
    variants: list[str],
    providers: Optional[list[str]] = None,
) -> tuple[Optional[str], str]:
    providers = providers or ServiceRegistry.list_services("asr")
    for variant in variants:
        quota_providers = get_quota_providers_sync(
            session, providers, owner_user_id, variant=variant
        )
        available = select_available_provider_sync(
            session, providers, owner_user_id, variant=variant
        )
        if available:
            return random.choice(available), variant  # nosec B311
        if quota_providers:
            fallback = [provider for provider in providers if provider not in quota_providers]
            if fallback:
                return random.choice(fallback), variant  # nosec B311
    return None, variants[-1] if variants else "file"


def _preferred_asr_providers_for_diarization() -> list[str]:
    return ["tencent"]


def _resolve_tencent_app_id(user_id: Optional[str]) -> Optional[str]:
    try:
        config = ConfigManager.get_config("asr", "tencent", user_id=user_id)
    except Exception:
        config = None
    app_id = getattr(config, "app_id", None) if config else None
    if app_id:
        return str(app_id).strip()
    if settings.TENCENT_ASR_APP_ID:
        return str(settings.TENCENT_ASR_APP_ID).strip()
    if settings.COS_BUCKET:
        match = re.search(r"-(\d+)$", settings.COS_BUCKET.strip())
        if match:
            return match.group(1)
    return None


def _supports_file_fast(user_id: Optional[str]) -> bool:
    if "tencent" not in ServiceRegistry.list_services("asr"):
        return False
    return bool(_resolve_tencent_app_id(user_id))


def _estimate_asr_duration(task: Task, segments: list[TranscriptSegment]) -> int:
    if task.duration_seconds and task.duration_seconds > 0:
        return int(task.duration_seconds)
    if not segments:
        return 0
    end_times = [seg.end_time for seg in segments if seg.end_time]
    if not end_times:
        return 0
    return int(max(end_times))


def _normalize_speaker_segments(
    segments: list[TranscriptSegment],
    enable_speaker_diarization: Optional[bool],
) -> list[TranscriptSegment]:
    if enable_speaker_diarization is False:
        return [replace(segment, speaker_id=None) for segment in segments]
    if enable_speaker_diarization is True:
        has_speaker = any(segment.speaker_id for segment in segments)
        if not has_speaker:
            return [replace(segment, speaker_id="spk_0") for segment in segments]
    return segments


def _serialize_words(
    words: Optional[list[WordTimestamp]],
) -> Optional[list[dict[str, float | str | None]]]:
    if not words:
        return None
    return [
        {
            "word": word.word,
            "start_time": float(word.start_time),
            "end_time": float(word.end_time),
            "confidence": float(word.confidence) if word.confidence is not None else None,
        }
        for word in words
    ]


def _build_asr_kwargs(
    transcribe: Any,
    status_callback: Optional[Callable[[str], Awaitable[None]]],
    enable_speaker_diarization: Optional[bool],
    asr_variant: Optional[str],
) -> dict[str, Any]:
    params = inspect.signature(transcribe).parameters
    kwargs: dict[str, Any] = {}
    if "status_callback" in params:
        kwargs["status_callback"] = status_callback
    if "enable_speaker_diarization" in params:
        kwargs["enable_speaker_diarization"] = enable_speaker_diarization
    if "asr_variant" in params:
        kwargs["asr_variant"] = asr_variant
    return kwargs


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


def _build_file_key(filename: str, user_id: str) -> str:
    now = datetime.now(timezone.utc)
    ext = Path(filename).suffix.lower()
    file_id = uuid4().hex
    return f"youtube/{user_id}/{now:%Y/%m/%d}/{file_id}{ext}"


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


def _get_audio_duration(file_path: str) -> Optional[int]:
    """Get audio duration in seconds using ffprobe.

    Returns:
        Duration in seconds (rounded), or None if failed to get duration
    """
    try:
        result = subprocess.run(  # nosec
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                file_path,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            duration = float(result.stdout.strip())
            return int(duration)
    except Exception as e:
        logger.warning(f"Failed to get audio duration for {file_path}: {e}")
    return None


def _transcode_to_wav_16k(input_path: str) -> str:
    output_path = str(Path(input_path).with_suffix(".wav"))
    result = subprocess.run(  # nosec
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


def _get_task(session: Session, task_id: str) -> Optional[Task]:
    result = session.execute(select(Task).where(Task.id == task_id, Task.deleted_at.is_(None)))
    return result.scalar_one_or_none()


def _update_metadata(
    session: Session,
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
    session.commit()


def _update_source_key(
    session: Session,
    task: Task,
    source_key: str,
    duration_seconds: Optional[int] = None,
) -> None:
    task.source_key = source_key
    if duration_seconds is not None:
        task.duration_seconds = duration_seconds
    session.commit()


def _update_task(
    session: Session,
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
    session.commit()

    # Create notification when task is completed
    if status == "completed":
        from app.models.notification import Notification

        task_title = task.title or "未命名任务"
        notification = Notification(
            user_id=str(task.user_id),
            task_id=str(task.id),
            category="task",
            action="completed",
            title=f"任务《{task_title}》已完成",
            message="转写和摘要已生成，点击查看详情",
            action_url=f"/tasks/{task.id}",
            priority="normal",
            extra_data={
                "task_title": task_title,
                "duration_seconds": task.duration_seconds,
                "source_type": task.source_type,
            },
        )
        session.add(notification)
        session.commit()

    trace_id = request_id or uuid4().hex

    # Prepare WebSocket message data
    message_data = {
        "type": "completed" if status == "completed" else "progress",
        "status": status,
        "stage": stage,
        "progress": task.progress,
        "task_id": task.id,
        "task_title": task.title,  # Add task_title for frontend
        "request_id": request_id,
    }

    message = json.dumps(
        {
            "code": 0,
            "message": "成功",
            "data": message_data,
            "traceId": trace_id,
        }
    )

    # Publish to both task-specific and user-global channels
    publish_task_update_sync(task.id, str(task.user_id), message)


def _mark_failed(
    session: Session, task: Task, error: BusinessError, request_id: Optional[str]
) -> None:
    task.status = "failed"
    task.progress = 0
    task.error_code = error.code.value
    task.error_message = error.kwargs.get("reason") or str(error)
    if request_id:
        task.request_id = request_id
    session.commit()

    # Create notification when task fails
    from app.models.notification import Notification

    task_title = task.title or "未命名任务"
    error_message = error.kwargs.get("reason") or str(error)

    notification = Notification(
        user_id=str(task.user_id),
        task_id=str(task.id),
        category="task",
        action="failed",
        title=f"任务《{task_title}》处理失败",
        message=error_message,
        action_url=f"/tasks/{task.id}",
        priority="high",  # Failed tasks have higher priority
        extra_data={
            "task_title": task_title,
            "error_code": error.code.value,
            "error_message": error_message,
            "source_type": task.source_type,
        },
    )
    session.add(notification)
    session.commit()

    trace_id = request_id or uuid4().hex
    message = json.dumps(
        {
            "code": error.code.value,
            "message": str(error),
            "data": {
                "type": "error",
                "status": "failed",
                "task_id": task.id,
                "task_title": task.title,  # Add task_title for frontend
            },
            "traceId": trace_id,
        }
    )

    # Publish to both task-specific and user-global channels
    publish_task_update_sync(task.id, str(task.user_id), message)


def _process_youtube(
    task_id: str,
    request_id: Optional[str],
) -> None:
    """处理 YouTube 任务（支持阶段管理和智能重试）

    Args:
        task_id: 任务ID
        request_id: 请求追踪ID
    """
    stage_manager = StageManager(task_id, request_id)

    with get_sync_db_session() as session:
        task = _get_task(session, task_id)
        if task is None:
            logger.warning("task not found: %s", task_id)
            return
        if task.source_type != "youtube":
            logger.warning("task source_type is not youtube: %s", task_id)
            return
        if not task.source_url:
            _mark_failed(
                session,
                task,
                BusinessError(ErrorCode.INVALID_PARAMETER, detail="source_url"),
                request_id,
            )
            return

    # ========== 检查是否可以跳过下载/上传阶段 ==========
    skip_download = False
    with get_sync_db_session() as session:
        task = _get_task(session, task_id)
        # 如果已有 source_key（上传成功），跳过下载/转码/上传
        if task.source_key:
            skip_download = True
            logger.info(
                "[%s] Skipping download/transcode/upload (source_key exists: %s)",
                request_id,
                task.source_key,
            )
            stage_manager.skip_stage(session, StageType.RESOLVE_YOUTUBE, "Already resolved")
            stage_manager.skip_stage(session, StageType.DOWNLOAD, "Source already uploaded")
            stage_manager.skip_stage(session, StageType.TRANSCODE, "Source already uploaded")
            stage_manager.skip_stage(session, StageType.UPLOAD_STORAGE, "Source already uploaded")

    filename = None
    original_filename = None
    direct_url = None
    title = None

    if not skip_download:
        # ========== 阶段 1: 解析 YouTube 信息 ==========
        with get_sync_db_session() as session:
            task = _get_task(session, task_id)
            _update_task(session, task, "resolving", 5, "resolving", request_id)
            stage_manager.start_stage(session, StageType.RESOLVE_YOUTUBE)

        try:
            direct_url, title = _extract_youtube_info(task.source_url)
            with get_sync_db_session() as session:
                stage_manager.complete_stage(session, StageType.RESOLVE_YOUTUBE, {"title": title})
        except Exception as exc:
            logger.exception("youtube info extraction failed: %s", exc)
            if isinstance(exc, BusinessError):
                error = exc
            else:
                error = BusinessError(ErrorCode.YOUTUBE_DOWNLOAD_FAILED, reason=str(exc))
            with get_sync_db_session() as session:
                task = _get_task(session, task_id)
                if task is None:
                    return
                stage_manager.fail_stage(session, StageType.RESOLVE_YOUTUBE, error.code, str(error))
                _mark_failed(session, task, error, request_id)
            return

        with get_sync_db_session() as session:
            task = _get_task(session, task_id)
            if task is None:
                return
            _update_metadata(session, task, direct_url, title)
            _update_task(session, task, "downloading", 15, "downloading", request_id)
            stage_manager.start_stage(session, StageType.DOWNLOAD)

        # ========== 阶段 2: 下载 YouTube 视频 ==========
        try:
            last_progress = 15
            last_emit = 0.0

            def _emit_progress(progress: int) -> None:
                with get_sync_db_session() as session:
                    task = _get_task(session, task_id)
                    if task is None:
                        return
                    _update_task(session, task, "downloading", progress, "downloading", request_id)

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
                # Call progress update directly (sync version)
                _emit_progress(mapped)

            original_filename = _download_youtube(task.source_url, progress_callback=_progress_hook)
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
                with get_sync_db_session() as session:
                    task = _get_task(session, task_id)
                    if task is None:
                        return
                    _mark_failed(session, task, error, request_id)
                return

        # 只有成功下载文件时才继续转码和上传
        if original_filename:
            with get_sync_db_session() as session:
                task = _get_task(session, task_id)
                if task is None:
                    return
                _update_task(session, task, "downloaded", 25, "downloaded", request_id)

        # ========== 阶段 3: 转码音频 ==========
        try:
            if original_filename:
                with get_sync_db_session() as session:
                    task = _get_task(session, task_id)
                    if task is None:
                        return
                    _update_task(session, task, "transcoding", 27, "transcoding", request_id)
                filename = _transcode_to_wav_16k(original_filename)
            else:
                # 下载失败，但有 direct_url，跳过转码和上传
                filename = None
        except Exception as exc:
            logger.exception("youtube transcode failed: %s", exc)
            with get_sync_db_session() as session:
                task = _get_task(session, task_id)
                if task is None:
                    return
                if isinstance(exc, BusinessError):
                    error = exc
                else:
                    error = BusinessError(ErrorCode.FILE_PROCESSING_ERROR, reason=str(exc))
                _mark_failed(session, task, error, request_id)
            return

        # ========== 阶段 4: 上传到存储服务 ==========
        source_key = None
        duration_seconds = None
        if filename:
            try:
                with get_sync_db_session() as session:
                    task = _get_task(session, task_id)
                    if task is None:
                        return
                    user_id = str(task.user_id)

                source_key = _build_file_key(filename, user_id)

                # 获取音频时长（在删除文件前）
                duration_seconds = _get_audio_duration(filename)
                if duration_seconds:
                    logger.info(
                        "Task %s: Audio duration detected: %d seconds",
                        task_id,
                        duration_seconds,
                        extra={"task_id": task_id, "duration": duration_seconds},
                    )

                with get_sync_db_session() as session:
                    task = _get_task(session, task_id)
                    if task is None:
                        return
                    _update_task(session, task, "uploading", 30, "uploading", request_id)

                # 双存储上传：同时上传到 COS 和 MinIO
                # 使用 SmartFactory 获取 storage 服务
                cos_storage = asyncio.run(
                    SmartFactory.get_service("storage", provider="cos", user_id=str(task.user_id))
                )
                minio_storage = asyncio.run(
                    SmartFactory.get_service("storage", provider="minio", user_id=str(task.user_id))
                )

                logger.info(
                    "Task %s: Uploading to COS (for ASR access)",
                    task_id,
                    extra={"task_id": task_id, "source_key": source_key},
                )
                cos_storage.upload_file(source_key, filename)

                logger.info(
                    "Task %s: Uploading to MinIO (for frontend playback)",
                    task_id,
                    extra={"task_id": task_id, "source_key": source_key},
                )
                minio_storage.upload_file(source_key, filename)

                logger.info(
                    "Task %s: Dual storage upload completed",
                    task_id,
                    extra={"task_id": task_id},
                )
            except Exception as exc:
                logger.exception("storage upload failed: %s", exc)
                source_key = None
            finally:
                if original_filename:
                    Path(original_filename).unlink(missing_ok=True)
                Path(filename).unlink(missing_ok=True)

        if source_key:
            with get_sync_db_session() as session:
                task = _get_task(session, task_id)
                if task is None:
                    return
                _update_source_key(session, task, source_key, duration_seconds)
                _update_task(session, task, "uploaded", 35, "uploaded", request_id)
        elif not direct_url:
            with get_sync_db_session() as session:
                task = _get_task(session, task_id)
                if task is None:
                    return
                _mark_failed(
                    session,
                    task,
                    BusinessError(ErrorCode.FILE_UPLOAD_FAILED),
                    request_id,
                )
            return
        else:
            with get_sync_db_session() as session:
                task = _get_task(session, task_id)
                if task is None:
                    return
                _update_task(session, task, "resolved", 35, "resolved", request_id)

    # ========== 检查是否可以跳过转写阶段 ==========
    skip_transcribe = False
    segments = []
    with get_sync_db_session() as session:
        task = _get_task(session, task_id)
        # 检查是否已有转写结果
        existing_transcripts = (
            session.query(Transcript).filter(Transcript.task_id == task_id).count()
        )

        if existing_transcripts > 0:
            skip_transcribe = True
            logger.info(
                "[%s] Skipping transcription (found %s existing transcripts)",
                request_id,
                existing_transcripts,
            )
            stage_manager.skip_stage(session, StageType.TRANSCRIBE, "Transcripts already exist")

            transcripts_list = (
                session.query(Transcript)
                .filter(Transcript.task_id == task_id)
                .order_by(Transcript.sequence)
                .all()
            )

            from app.services.asr.base import TranscriptSegment

            segments = [
                TranscriptSegment(
                    speaker_id=t.speaker_id or "",
                    content=t.content,
                    start_time=t.start_time,
                    end_time=t.end_time,
                    confidence=t.confidence or 0.0,
                )
                for t in transcripts_list
            ]

    # 开始 ASR 转写（进度 35-70%）
    if not skip_transcribe:
        try:
            with get_sync_db_session() as session:
                task = _get_task(session, task_id)
                if task is None:
                    return

                audio_candidates = []
                if task.source_key:
                    # 使用 SmartFactory 获取 COS storage
                    cos_storage = asyncio.run(
                        SmartFactory.get_service(
                            "storage", provider="cos", user_id=str(task.user_id)
                        )
                    )
                    audio_url = cos_storage.generate_presigned_url(task.source_key, expires_in=7200)
                    audio_candidates.append(audio_url)
                if direct_url:
                    audio_candidates.append(direct_url)
                if not audio_candidates:
                    if not task.source_url:
                        raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="source_url")
                    audio_candidates.append(task.source_url)

                _update_task(session, task, "transcribing", 40, "transcribing", request_id)
                stage_manager.start_stage(session, StageType.TRANSCRIBE)
                # 使用 SmartFactory 获取 ASR 服务（支持指定 provider）
                asr_provider = _resolve_asr_provider(task)
                asr_variant = _resolve_asr_variant(task)
                diarization = None
                if isinstance(task.options, dict):
                    diarization = task.options.get("enable_speaker_diarization")
                if not asr_provider:
                    if asr_variant != "file":
                        variants = [asr_variant]
                    else:
                        variants = ["file", "file_fast"]
                    if "file_fast" in variants and not _supports_file_fast(str(task.user_id)):
                        variants = [variant for variant in variants if variant != "file_fast"]
                        if not variants:
                            variants = ["file"]
                    if diarization is True:
                        preferred = [
                            provider
                            for provider in _preferred_asr_providers_for_diarization()
                            if provider in ServiceRegistry.list_services("asr")
                        ]
                        if preferred:
                            asr_provider, asr_variant = _select_asr_provider_by_quota(
                                session,
                                str(task.user_id),
                                variants,
                                providers=preferred,
                            )
                            if not asr_provider:
                                asr_provider = preferred[0]
                        else:
                            asr_provider, asr_variant = _select_asr_provider_by_quota(
                                session,
                                str(task.user_id),
                                variants,
                            )
                    else:
                        asr_provider, asr_variant = _select_asr_provider_by_quota(
                            session,
                            str(task.user_id),
                            variants,
                        )
                if asr_provider:
                    task.asr_provider = asr_provider
                    if isinstance(task.options, dict):
                        task.options["asr_variant"] = asr_variant
                    session.commit()
                asr_service = asyncio.run(
                    SmartFactory.get_service(
                        "asr",
                        user_id=str(task.user_id),
                        provider=asr_provider,
                    )
                )
                last_error: Optional[BusinessError] = None
                segments = []

                async def _asr_status(stage: str) -> None:
                    # ASR status callback (currently not used for progress updates)
                    pass

                # 尝试使用不同的音频 URL 进行转写
                for idx, audio_url in enumerate(audio_candidates, start=1):
                    try:
                        logger.info(
                            "Task %s: Attempting ASR with URL %d/%d",
                            task_id,
                            idx,
                            len(audio_candidates),
                            extra={"task_id": task_id, "audio_url_index": idx},
                        )
                        kwargs = _build_asr_kwargs(
                            asr_service.transcribe,
                            status_callback=_asr_status,
                            enable_speaker_diarization=diarization,
                            asr_variant=asr_variant,
                        )
                        segments = asyncio.run(asr_service.transcribe(audio_url, **kwargs))
                        logger.info(
                            "Task %s: ASR succeeded with URL %d, got %d segments",
                            task_id,
                            idx,
                            len(segments),
                            extra={"task_id": task_id, "segment_count": len(segments)},
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
                            "Task %s: ASR failed for URL %d/%d with error %s: %s",
                            task_id,
                            idx,
                            len(audio_candidates),
                            exc.code.value,
                            exc.kwargs.get("reason", str(exc)),
                            extra={
                                "task_id": task_id,
                                "error_code": exc.code.value,
                                "audio_url_index": idx,
                            },
                        )

                if last_error is not None and not segments:
                    raise last_error

                segments = _normalize_speaker_segments(segments, diarization)
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
                            words=_serialize_words(segment.words),
                            sequence=idx,
                            is_edited=False,
                            original_content=None,
                        )
                    )
                session.add_all(transcripts)
                session.commit()
                duration_seconds = _estimate_asr_duration(task, segments)
                if duration_seconds and not task.duration_seconds:
                    task.duration_seconds = duration_seconds
                    session.commit()
                record_usage_sync(
                    session,
                    asr_service.provider,
                    duration_seconds,
                    str(task.user_id),
                    variant=asr_variant,
                )
                try:
                    ingest_task_chunks_sync(session, task, transcripts, str(task.user_id))
                except Exception as exc:
                    logger.warning(
                        "Task %s: RAG chunk ingest failed: %s",
                        task_id,
                        exc,
                        exc_info=True,
                        extra={"task_id": task_id},
                    )
                stage_manager.complete_stage(
                    session, StageType.TRANSCRIBE, {"segment_count": len(transcripts)}
                )
                logger.info(
                    "Task %s: Saved %d transcript segments",
                    task_id,
                    len(transcripts),
                    extra={"task_id": task_id, "segment_count": len(transcripts)},
                )
        except Exception as exc:
            logger.exception("Transcription failed: %s", exc)
            with get_sync_db_session() as session:
                task = _get_task(session, task_id)
                if task is not None:
                    if isinstance(exc, BusinessError):
                        stage_manager.fail_stage(session, StageType.TRANSCRIBE, exc.code, str(exc))
                        _mark_failed(session, task, exc, request_id)
                    else:
                        error = BusinessError(ErrorCode.ASR_SERVICE_FAILED, reason=str(exc))
                        stage_manager.fail_stage(
                            session, StageType.TRANSCRIBE, error.code, str(error)
                        )
                        _mark_failed(session, task, error, request_id)
            return

    # ========== 检查是否可以跳过摘要阶段 ==========
    skip_summarize = False
    with get_sync_db_session() as session:
        task = _get_task(session, task_id)
        # 检查是否已有摘要结果
        existing_summaries = (
            session.query(Summary)
            .filter(Summary.task_id == task_id, Summary.is_active == True)  # noqa: E712
            .count()
        )

        if existing_summaries > 0:
            skip_summarize = True
            logger.info(
                "[%s] Skipping summarization (found %s existing summaries)",
                request_id,
                existing_summaries,
            )
            stage_manager.skip_stage(session, StageType.SUMMARIZE, "Summaries already exist")

    # 开始 LLM 总结（进度 70-95%）
    if not skip_summarize:
        try:
            with get_sync_db_session() as session:
                task = _get_task(session, task_id)
                if task is None:
                    return
                _update_task(session, task, "summarizing", 75, "summarizing", request_id)
                stage_manager.start_stage(session, StageType.SUMMARIZE)
                # 使用 SmartFactory 获取 LLM 服务（自动选择最优服务）
                provider, model_id = _resolve_llm_selection(task, str(task.user_id))
                llm_service = asyncio.run(
                    SmartFactory.get_service(
                        "llm",
                        provider=provider,
                        model_id=model_id,
                        user_id=str(task.user_id),
                    )
                )
                llm_provider = getattr(llm_service, "provider", None) or provider
                if llm_provider:
                    task.llm_provider = llm_provider
                    session.commit()
                full_text = "\n".join([seg.content for seg in segments])
                logger.info(
                    "Task %s: Starting LLM summarization with %d characters of text",
                    task_id,
                    len(full_text),
                    extra={"task_id": task_id, "text_length": len(full_text)},
                )

                summaries = []
                llm_usages: list[LLMUsage] = []
                for summary_type in ("overview", "key_points", "action_items"):
                    logger.info(
                        "Task %s: Generating %s summary",
                        task_id,
                        summary_type,
                        extra={"task_id": task_id, "summary_type": summary_type},
                    )
                    try:
                        content = asyncio.run(llm_service.summarize(full_text, summary_type))
                    except Exception:
                        if llm_provider:
                            llm_usages.append(
                                LLMUsage(
                                    user_id=str(task.user_id),
                                    task_id=str(task.id),
                                    provider=llm_provider,
                                    model_id=llm_service.model_name,
                                    call_type="summarize",
                                    summary_type=summary_type,
                                    status="failed",
                                )
                            )
                            session.add_all(llm_usages)
                            session.commit()
                        raise
                    logger.info(
                        "Task %s: Generated %s summary (%d characters)",
                        task_id,
                        summary_type,
                        len(content),
                        extra={
                            "task_id": task_id,
                            "summary_type": summary_type,
                            "content_length": len(content),
                        },
                    )
                    if llm_provider:
                        llm_usages.append(
                            LLMUsage(
                                user_id=str(task.user_id),
                                task_id=str(task.id),
                                provider=llm_provider,
                                model_id=llm_service.model_name,
                                call_type="summarize",
                                summary_type=summary_type,
                                status="success",
                            )
                        )
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
                if llm_usages:
                    session.add_all(llm_usages)
                session.commit()
                stage_manager.complete_stage(
                    session, StageType.SUMMARIZE, {"summary_count": len(summaries)}
                )
                logger.info(
                    "Task %s: All summaries saved to database",
                    task_id,
                    extra={"task_id": task_id, "summary_count": len(summaries)},
                )
        except Exception as exc:
            logger.exception("Summarization failed: %s", exc)
            with get_sync_db_session() as session:
                task = _get_task(session, task_id)
                if task is not None:
                    if isinstance(exc, BusinessError):
                        stage_manager.fail_stage(session, StageType.SUMMARIZE, exc.code, str(exc))
                        _mark_failed(session, task, exc, request_id)
                    else:
                        error = BusinessError(ErrorCode.LLM_SERVICE_FAILED, reason=str(exc))
                        stage_manager.fail_stage(
                            session, StageType.SUMMARIZE, error.code, str(error)
                        )
                        _mark_failed(session, task, error, request_id)
            return

    # ========== 任务完成 ==========
    with get_sync_db_session() as session:
        task = _get_task(session, task_id)
        if task is None:
            return

        # 设置语言（根据 ASR 模型推断）
        if not task.detected_language:
            task.detected_language = "zh"  # 中文

        task.error_code = None
        task.error_message = None
        _update_task(session, task, "completed", 100, "completed", request_id)
        logger.info(
            "Task %s: YouTube video processing completed successfully",
            task_id,
            extra={"task_id": task_id},
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
def process_youtube(
    self,
    task_id: str,
    request_id: Optional[str] = None,
) -> None:
    """Celery 任务入口：处理 YouTube 视频

    Args:
        task_id: 任务ID
        request_id: 请求追踪ID
    """
    _process_youtube(task_id, request_id)
