from __future__ import annotations

import asyncio
import inspect
import json
import logging
import random
import re
import subprocess  # nosec B404
import tempfile
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.engine import Result
from sqlalchemy.orm import Session

from app.config import settings
from app.core.config_manager import ConfigManager
from app.core.exceptions import BusinessError
from app.core.registry import ServiceRegistry
from app.core.smart_factory import SmartFactory
from app.i18n.codes import ErrorCode
from app.models.summary import Summary
from app.models.task import Task
from app.models.transcript import Transcript
from app.services.asr.base import ASRService, TranscriptSegment, WordTimestamp
from app.services.asr_quota_service import (
    get_quota_providers_sync,
    record_usage_sync,
    select_available_provider_sync,
)
from app.services.llm.base import LLMService
from app.services.storage.base import StorageService
from worker.celery_app import celery_app
from worker.db import get_sync_db_session
from worker.redis_client import publish_message_sync, publish_task_update_sync

logger = logging.getLogger("worker.process_audio")


async def _maybe_await(result: Awaitable[Any] | Any) -> Any:
    if inspect.isawaitable(result):
        return await result
    return result


@asynccontextmanager
async def async_session_factory():
    with get_sync_db_session() as session:
        yield session


async def get_asr_service(
    user_id: Optional[str] = None, provider: Optional[str] = None
) -> ASRService:
    return await SmartFactory.get_service("asr", user_id=user_id, provider=provider)


async def get_llm_service(
    provider: str, model_id: str, user_id: Optional[str] = None
) -> LLMService:
    return await SmartFactory.get_service(
        "llm", provider=provider, model_id=model_id, user_id=user_id
    )


async def get_storage_service(
    provider: str = "cos", user_id: Optional[str] = None
) -> StorageService:
    return await SmartFactory.get_service("storage", provider=provider, user_id=user_id)


async def publish_message(channel: str, message: str) -> None:
    if ":" in channel:
        task_id, user_id = channel.split(":", 1)
        publish_task_update_sync(task_id, user_id, message)
    else:
        publish_message_sync(channel, message)


def _add_record(session: Session, record: object) -> None:
    if hasattr(session, "add"):
        session.add(record)
    else:
        session.add_all([record])


async def _get_task(session: Session, task_id: str) -> Optional[Task]:
    result = await _maybe_await(
        session.execute(select(Task).where(Task.id == task_id, Task.deleted_at.is_(None)))
    )
    result = cast(Result, result)
    return result.scalar_one_or_none()


async def _commit(session: Session) -> None:
    result: Any = session.commit()  # type: ignore[func-returns-value]
    if inspect.isawaitable(result):
        await result


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


async def _update_task(
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
    await _commit(session)

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
        _add_record(session, notification)
        await _commit(session)

    trace_id = request_id or uuid4().hex

    # Prepare WebSocket message data
    message_data = {
        "type": "completed" if status == "completed" else "progress",
        "status": status,
        "stage": stage,
        "progress": progress,
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

    await publish_message(f"{task.id}:{task.user_id}", message)


async def _mark_failed(
    session: Session, task: Task, error: BusinessError, request_id: Optional[str]
) -> None:
    task.status = "failed"
    task.progress = 0
    task.error_code = error.code.value
    task.error_message = error.kwargs.get("reason") or str(error)
    if request_id:
        task.request_id = request_id
    await _commit(session)

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
    _add_record(session, notification)
    await _commit(session)

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

    await publish_message(f"{task.id}:{task.user_id}", message)


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

                # 提取音频时长并同步文件到 MinIO（用于前端播放）
                if not task.duration_seconds:
                    logger.info(
                        "Task %s: Extracting audio duration and syncing to MinIO",
                        task_id,
                        extra={"task_id": task_id, "source_key": task.source_key},
                    )

                    # 下载文件到临时目录（用于提取时长和上传到 MinIO）
                    # 使用 SmartFactory 获取 COS storage（异步调用）
                    cos_storage: StorageService = await _maybe_await(
                        get_storage_service(user_id=str(task.user_id))
                    )
                    cos_storage_client = cast(Any, cos_storage)
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
                        tmp_path = tmp_file.name

                    try:
                        if not hasattr(cos_storage_client, "_client"):
                            logger.info(
                                "Task %s: Skipping duration extraction; COS client not available",
                                task_id,
                                extra={"task_id": task_id},
                            )
                            raise RuntimeError("cos client unavailable")

                        # 从 COS 下载文件
                        logger.info(f"Downloading from COS: {task.source_key}")
                        cos_storage_client._client.download_file(
                            Bucket=cos_storage_client._bucket,
                            Key=task.source_key,
                            DestFilePath=tmp_path,
                        )

                        # 提取音频时长
                        result = subprocess.run(  # nosec
                            [
                                "ffprobe",
                                "-v",
                                "error",
                                "-show_entries",
                                "format=duration",
                                "-of",
                                "default=noprint_wrappers=1:nokey=1",
                                tmp_path,
                            ],
                            capture_output=True,
                            text=True,
                            check=False,
                        )

                        if result.returncode == 0 and result.stdout.strip():
                            duration = float(result.stdout.strip())
                            task.duration_seconds = int(duration)
                            logger.info(
                                "Task %s: Audio duration set to %d seconds",
                                task_id,
                                task.duration_seconds,
                                extra={"task_id": task_id, "duration": task.duration_seconds},
                            )

                        # 上传到 MinIO（用于前端播放）
                        # 使用 SmartFactory 获取 MinIO storage
                        minio_storage: StorageService = await _maybe_await(
                            get_storage_service("minio", user_id=str(task.user_id))
                        )
                        logger.info(f"Uploading to MinIO: {task.source_key}")
                        minio_storage.upload_file(task.source_key, tmp_path, "audio/wav")
                        logger.info("Task %s: File synced to MinIO successfully", task_id)

                        await _commit(session)

                    except RuntimeError:
                        pass
                    finally:
                        # 删除临时文件
                        try:
                            Path(tmp_path).unlink(missing_ok=True)
                        except Exception as e:
                            logger.warning(f"Failed to delete temp file {tmp_path}: {e}")

                expires_in = settings.UPLOAD_PRESIGN_EXPIRES
                if not expires_in:
                    raise BusinessError(
                        ErrorCode.INVALID_PARAMETER, detail="upload_presign_expires"
                    )
                # 使用 COS 存储生成带签名的 URL 供 ASR 访问
                # 使用 SmartFactory 获取 COS storage
                cos_storage = await _maybe_await(get_storage_service(user_id=str(task.user_id)))
                audio_candidates.append(
                    cos_storage.generate_presigned_url(task.source_key, expires_in)
                )
            else:
                if task.source_key:
                    # YouTube 下载的音频也使用 COS 存储（双存储方案）
                    expires_in = settings.UPLOAD_PRESIGN_EXPIRES
                    if not expires_in:
                        raise BusinessError(
                            ErrorCode.INVALID_PARAMETER, detail="upload_presign_expires"
                        )
                    # 使用 SmartFactory 获取 COS storage
                    cos_storage = await _maybe_await(get_storage_service(user_id=str(task.user_id)))
                    audio_candidates.append(
                        cos_storage.generate_presigned_url(task.source_key, expires_in)
                    )
                direct_url = None
                if isinstance(task.source_metadata, dict):
                    direct_url = task.source_metadata.get("direct_url")
                if isinstance(direct_url, str) and direct_url:
                    audio_candidates.append(direct_url)
                if not audio_candidates:
                    if not task.source_url:
                        raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="source_url")
                    audio_candidates.append(task.source_url)

            await _update_task(session, task, "transcribing", 40, "transcribing", request_id)
            # 使用 SmartFactory 获取 ASR 服务（自动选择最优服务）
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
                await _commit(session)
            asr_service: ASRService = await _maybe_await(
                get_asr_service(str(task.user_id), provider=asr_provider)
            )
            last_error: Optional[BusinessError] = None
            segments: list[TranscriptSegment] = []

            async def _asr_status(stage: str) -> None:
                # This callback is called from within async context
                # We can't use the sync session here, so we skip status updates during ASR
                pass

            for idx, audio_url in enumerate(audio_candidates, start=1):
                try:
                    logger.info(
                        "Task %s: Attempting ASR with URL %d/%d",
                        task_id,
                        idx,
                        len(audio_candidates),
                        extra={"task_id": task_id, "audio_url_index": idx},
                    )
                    # ASR service is async, so we run it in asyncio.run()
                    transcribe = asr_service.transcribe
                    kwargs = _build_asr_kwargs(
                        transcribe,
                        status_callback=_asr_status,
                        enable_speaker_diarization=diarization,
                        asr_variant=asr_variant,
                    )
                    transcribe_result = transcribe(audio_url, **kwargs)
                    segments = cast(
                        list[TranscriptSegment],
                        await _maybe_await(transcribe_result),
                    )
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
                        "Task %s: ASR failed for URL %d/%d with error %s: %s, "
                        "trying next URL if available",
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
            await _commit(session)
            duration_seconds = _estimate_asr_duration(task, segments)
            if duration_seconds and not task.duration_seconds:
                task.duration_seconds = duration_seconds
                await _commit(session)
            record_usage_sync(
                session,
                asr_service.provider,
                duration_seconds,
                str(task.user_id),
                variant=asr_variant,
            )

            await _update_task(session, task, "summarizing", 80, "summarizing", request_id)
            # 使用 SmartFactory 获取 LLM 服务（自动选择最优服务）
            provider, model_id = _resolve_llm_selection(task, str(task.user_id))
            llm_service: LLMService = await _maybe_await(
                get_llm_service(provider, model_id, str(task.user_id))
            )
            full_text = "\n".join([seg.content for seg in segments])

            options = task.options or {}
            content_style = options.get("summary_style", "meeting")
            if not isinstance(content_style, str):
                content_style = "meeting"

            logger.info(
                "Task %s: Starting LLM summarization with %d characters of text (style: %s)",
                task_id,
                len(full_text),
                content_style,
                extra={
                    "task_id": task_id,
                    "text_length": len(full_text),
                    "content_style": content_style,
                },
            )

            summaries = []
            for summary_type in ("overview", "key_points", "action_items"):
                logger.info(
                    "Task %s: Generating %s summary (style: %s)",
                    task_id,
                    summary_type,
                    content_style,
                    extra={
                        "task_id": task_id,
                        "summary_type": summary_type,
                        "content_style": content_style,
                    },
                )
                # LLM service is async, so we run it in asyncio.run()
                summarize = llm_service.summarize
                if len(inspect.signature(summarize).parameters) >= 3:
                    summarize_result = summarize(full_text, summary_type, content_style)
                else:
                    summarize_result = summarize(full_text, summary_type)
                content = cast(str, await _maybe_await(summarize_result))
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
            await _commit(session)
            logger.info(
                "Task %s: All summaries saved to database",
                task_id,
                extra={"task_id": task_id, "summary_count": len(summaries)},
            )

            # 设置语言（根据 ASR 模型推断，目前使用中文模型）
            if not task.detected_language:
                task.detected_language = "zh"  # 中文

            task.error_code = None
            task.error_message = None
            await _update_task(session, task, "completed", 100, "completed", request_id)
        except BusinessError as exc:
            logger.error(
                "Task %s failed with business error: %s (code=%s)",
                task_id,
                exc,
                exc.code.value,
                exc_info=True,
                extra={"task_id": task_id, "error_code": exc.code.value},
            )
            await _mark_failed(session, task, exc, request_id)
        except Exception as exc:
            logger.exception(
                "Task %s failed with unexpected error: %s",
                task_id,
                exc,
                extra={"task_id": task_id, "error_type": type(exc).__name__},
            )
            error = BusinessError(
                ErrorCode.INTERNAL_SERVER_ERROR,
                reason=f"{type(exc).__name__}: {str(exc)}",
            )
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
