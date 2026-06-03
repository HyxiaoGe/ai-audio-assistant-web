from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import subprocess  # nosec B404
import tempfile
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.engine import Result
from sqlalchemy.orm import Session

from app.config import settings
from app.core.asr_scheduler import ASRScheduler, TaskFeatures
from app.core.config_manager import ConfigManager
from app.core.cost_optimizer import cost_tracker
from app.core.exceptions import BusinessError
from app.core.registry import ServiceRegistry
from app.core.smart_factory import SmartFactory
from app.i18n.codes import ErrorCode
from app.models.asr_usage import ASRUsage
from app.models.task import Task
from app.models.transcript import Transcript
from app.services.asr.base import ASRService, TranscriptSegment, WordTimestamp
from app.services.asr_quota_service import record_usage
from app.services.llm.base import LLMService
from app.services.notifications.service import NotificationService
from app.services.notifications.types import NotificationType
from app.services.rag import ingest_task_chunks_async
from app.services.storage.base import StorageService
from app.services.transcript_polish import polish_transcripts
from worker.celery_app import celery_app
from worker.db import get_sync_db_session
from worker.redis_client import publish_message_sync, publish_task_update_sync
from worker.tasks.asr_idempotency import AsrRetryAction, decide_asr_action
from worker.tasks.summary_generator import generate_summaries_with_quality_awareness

logger = logging.getLogger("worker.process_audio")


async def _maybe_await(result: Awaitable[Any] | Any) -> Any:
    if inspect.isawaitable(result):
        return await result
    return result


def _call_factory(factory: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        return factory(*args, **kwargs)

    params = list(signature.parameters.values())
    if any(param.kind == param.VAR_POSITIONAL for param in params):
        return factory(*args, **kwargs)
    if any(param.kind == param.VAR_KEYWORD for param in params):
        return factory(*args, **kwargs)

    accepted_kwargs = {key: value for key, value in kwargs.items() if key in signature.parameters}
    positional_params = [
        param for param in params if param.kind in (param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD)
    ]
    max_positional = len(positional_params) - len(
        [param for param in positional_params if param.name in accepted_kwargs]
    )
    filtered_args = list(args[:max_positional])
    return factory(*filtered_args, **accepted_kwargs)


@asynccontextmanager
async def async_session_factory():
    with get_sync_db_session() as session:
        yield session


async def get_asr_service(user_id: str | None = None, provider: str | None = None) -> ASRService:
    return await SmartFactory.get_service("asr", user_id=user_id, provider=provider)


async def get_llm_service(provider: str, model_id: str, user_id: str | None = None) -> LLMService:
    return await SmartFactory.get_service("llm", provider=provider, model_id=model_id, user_id=user_id)


async def get_storage_service(provider: str = "cos", user_id: str | None = None) -> StorageService:
    return await SmartFactory.get_service("storage", provider=provider, user_id=user_id)


async def _enforce_object_size_limit(storage: StorageService, object_key: str) -> None:
    """服务端强制校验已落库对象的真实大小，超限即删除并失败。

    presigned PUT 只能签固定 Content-Length（客户端可绕过），无法签大小范围；
    worker 这里 HEAD 一次对象，是唯一能看到真实大小、关掉这条 DoS 的地方。
    HEAD 瞬时失败时 fail-open，仅对确认超限的对象动手。
    """
    max_size = settings.UPLOAD_MAX_SIZE_BYTES
    if not max_size or max_size <= 0:
        return
    try:
        info = await _maybe_await(storage.get_file_info(object_key))
    except Exception:
        return  # 瞬时 HEAD 失败 fail-open
    size = int(info.get("size") or 0)
    if size > max_size:
        with suppress(Exception):
            storage.delete_file(object_key)
        limit_mb = max(1, max_size // (1024 * 1024))
        raise BusinessError(
            ErrorCode.FILE_TOO_LARGE,
            reason=f"uploaded object exceeds the {limit_mb}MB limit",
            max_size=f"{limit_mb}MB",
        )


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


async def _get_task(session: Session, task_id: str) -> Task | None:
    result = await _maybe_await(session.execute(select(Task).where(Task.id == task_id, Task.deleted_at.is_(None))))
    result = cast(Result, result)
    return result.scalar_one_or_none()


async def _commit(session: Session) -> None:
    result: Any = session.commit()  # type: ignore[func-returns-value]
    if inspect.isawaitable(result):
        await result


def _load_llm_model_id(provider: str, user_id: str | None) -> str | None:
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


def _default_model_id_for_provider(provider: str, user_id: str | None) -> str:
    configured_model = _load_llm_model_id(provider, user_id)
    if configured_model:
        return configured_model
    if provider == "proxy":
        return settings.LITELLM_MODEL
    return provider


def _resolve_llm_selection(task: Task, user_id: str | None) -> tuple[str, str]:
    options = task.options or {}
    raw_provider = options.get("llm_provider") or options.get("provider")
    raw_model_id = options.get("llm_model_id") or options.get("model_id")
    provider = raw_provider if isinstance(raw_provider, str) else None
    model_id = raw_model_id if isinstance(raw_model_id, str) else None
    if provider:
        model_id = model_id or _default_model_id_for_provider(provider, user_id)
        return provider, model_id
    provider = _select_default_llm_provider()
    model_id = _default_model_id_for_provider(provider, user_id)
    return provider, model_id


def _resolve_asr_provider(task: Task) -> str | None:
    options = task.options or {}
    raw_provider = options.get("asr_provider")
    return raw_provider if isinstance(raw_provider, str) else None


def _resolve_asr_variant(task: Task) -> str:
    options = task.options or {}
    raw_variant = options.get("asr_variant")
    return raw_variant if isinstance(raw_variant, str) and raw_variant else "file"


def _resolve_tencent_app_id(user_id: str | None) -> str | None:
    config = None
    if settings.CONFIG_CENTER_DB_ENABLED:
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


def _supports_file_fast(user_id: str | None) -> bool:
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
    enable_speaker_diarization: bool | None,
) -> list[TranscriptSegment]:
    if enable_speaker_diarization is False:
        return [replace(segment, speaker_id=None) for segment in segments]
    if enable_speaker_diarization is True:
        has_speaker = any(segment.speaker_id for segment in segments)
        if not has_speaker:
            return [replace(segment, speaker_id="spk_0") for segment in segments]
    return segments


def _serialize_words(
    words: list[WordTimestamp] | None,
) -> list[dict[str, float | str | None]] | None:
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
    status_callback: Callable[[str], Awaitable[None]] | None,
    enable_speaker_diarization: bool | None,
    asr_variant: str | None,
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


def _effective_asr_variant(transcribe: Any, asr_variant: str) -> str:
    """返回该 provider「实际会被执行」的 ASR 变体，供计费/记录使用（D5-variant）。

    若 provider 的 transcribe 不接受 asr_variant 形参（如 aliyun/volcengine 只实现标准版），
    则无论是否请求 file_fast 都只会按标准版 file 运行，计费也必须按 file，否则会出现
    「实跑 ¥2.5 标准版、却按 ¥3.3 极速版计费」的多收。provider 真正消费该形参时（如 tencent）
    按请求变体计费，与实跑一致。判定口径与 _build_asr_kwargs 的签名内省保持一致。
    """
    if "asr_variant" in inspect.signature(transcribe).parameters:
        return asr_variant
    return "file"


def _segments_from_transcripts(transcripts: list[Transcript]) -> list[TranscriptSegment]:
    """从已落库 Transcript 重建最小 TranscriptSegment（仅用于重试时估算时长/计费）。"""
    return [
        TranscriptSegment(
            speaker_id=t.speaker_id,
            content=t.content,
            start_time=float(t.start_time) if t.start_time is not None else 0.0,
            end_time=float(t.end_time) if t.end_time is not None else 0.0,
            confidence=t.confidence,
        )
        for t in transcripts
    ]


async def _finalize_asr_cost(
    session: Session,
    task: Task,
    *,
    provider_name: str,
    asr_variant: str,
    duration_seconds: float,
    asr_service: Any,
    successful_audio_url: str | None,
    diarization: Any,
    processing_time_ms: int,
    claim_row: ASRUsage | None,
) -> None:
    """原子补记一次 ASR 计费，并把终态 ASRUsage 置为 ``success``（D5-retry 钱路）。

    幂等终态标记 = ``ASRUsage.status == "success"``。本函数把三处计费写入放进同一事务：
    ``record_usage(commit=False)`` 累加 AsrUserQuota，``consume_quota`` 只 flush 分拆免费/付费，
    最后随 ASRUsage(success) 一次性提交——要么全写要么全不写，关闭「转写后/ASRUsage 前」少计费窗口，
    同时避免重试时重复累加配额。``claim_row`` 非空则就地收尾该 claim（不新增行），保证每个 task
    只有一条计费记录。
    """
    duration_seconds = float(duration_seconds or 0.0)
    estimated_cost = 0.0
    free_quota_consumed = 0.0
    paid_duration_seconds = duration_seconds
    actual_paid_cost = 0.0

    if provider_name and duration_seconds > 0:
        if asr_service is not None and hasattr(asr_service, "estimate_cost"):
            estimated_cost = asr_service.estimate_cost(int(duration_seconds), variant=asr_variant)
        cost_tracker.record_usage(
            "asr",
            provider_name,
            {"duration_seconds": duration_seconds},
            estimated_cost,
        )
        # AsrUserQuota 累加：参与本事务，不单独提交（commit=False）
        await record_usage(
            session,
            provider_name,
            duration_seconds,
            str(task.user_id),
            variant=asr_variant,
            commit=False,
        )
        # 免费/付费分拆：consume_quota 只 flush 不 commit，随下方 ASRUsage 一并提交
        try:
            from app.services.asr_free_quota_service import AsrFreeQuotaService

            consumption = await AsrFreeQuotaService.consume_quota(
                session,
                provider_name,
                asr_variant,
                duration_seconds,
                user_id=None,  # 全局配额
            )
            free_quota_consumed = consumption.free_consumed
            paid_duration_seconds = consumption.paid_consumed
            actual_paid_cost = consumption.cost
        except Exception as exc:
            logger.warning(
                "Task %s: Failed to consume free quota, using full cost: %s",
                task.id,
                exc,
            )
            actual_paid_cost = estimated_cost

    usage = claim_row if claim_row is not None else ASRUsage(user_id=str(task.user_id), task_id=str(task.id))
    usage.provider = provider_name or "unknown"
    usage.variant = asr_variant
    usage.duration_seconds = duration_seconds
    usage.estimated_cost = estimated_cost
    if successful_audio_url:
        usage.audio_url = successful_audio_url[:1000]
    if duration_seconds > 0:
        usage.status = "success"
    else:
        # 时长为 0（提取静默失败 + provider 未回时间戳）：不要把这条零成本记为终态 success，
        # 否则 D5-retry 的 SKIP_ALL 幂等标记会把漏计费记录永久锁死。显式置非终态 "processing"
        # （覆盖 ASRUsage.status 的 server_default 'success'），留待对账/重试按真实时长补记。
        usage.status = "processing"
        logger.warning(
            "Task %s: ASR duration is 0 (provider=%s); leaving ASRUsage non-terminal (status=processing) "
            "instead of locking a zero-cost success",
            task.id,
            provider_name or "unknown",
        )
    usage.processing_time_ms = processing_time_ms
    usage.request_params = {"enable_speaker_diarization": diarization, "asr_variant": asr_variant}
    usage.free_quota_consumed = free_quota_consumed
    usage.paid_duration_seconds = paid_duration_seconds
    usage.actual_paid_cost = actual_paid_cost
    if claim_row is None:
        _add_record(session, usage)
    await _commit(session)  # 原子提交：AsrUserQuota + 免费额度周期 + ASRUsage(success)
    logger.info(
        "Task %s: ASRUsage finalized (status=success) - provider=%s, duration=%ds, free=%.1fs, paid=%.1fs, cost=%.4f",
        task.id,
        usage.provider,
        int(duration_seconds),
        free_quota_consumed,
        paid_duration_seconds,
        actual_paid_cost,
    )


async def _update_task(
    session: Session,
    task: Task,
    status: str,
    progress: int,
    stage: str | None,
    request_id: str | None,
) -> None:
    task.status = status
    task.progress = max(task.progress or 0, progress)
    task.stage = stage
    if request_id:
        task.request_id = request_id
    await _commit(session)

    # 任务完成：经唯一收口 NotificationService 派发（落库+推送由 InAppChannel 负责）
    if status == "completed":
        NotificationService.notify(
            session,
            type=NotificationType.TASK_COMPLETED,
            user_id=str(task.user_id),
            params={
                "task_title": task.title or "未命名任务",
                "duration": task.duration_seconds,
                "source_type": task.source_type,
            },
            task_id=str(task.id),
        )

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
            "kind": "task_progress",
            "code": 0,
            "message": "成功",
            "data": message_data,
            "traceId": trace_id,
        }
    )

    await publish_message(f"{task.id}:{task.user_id}", message)


async def _mark_failed(session: Session, task: Task, error: BusinessError, request_id: str | None) -> None:
    # 失败处理必须「尽力而为且绝不向外抛异常」：本函数是 _process_task 两个 except 分支的最后一步，
    # 若它自身抛错（Redis publish 失败 / 通知写库失败 / commit 失败）会冒泡出 asyncio.run，触发
    # Celery autoretry_for=(Exception,) 把整个任务（含已付费 ASR）从头重跑 → 重复转写/重复扣费
    # (D5-retry gap b)。这里整体兜底，记录失败状态尽力落库，任何子步骤异常都吞掉只记日志。
    try:
        task.status = "failed"
        task.progress = 0
        task.error_code = error.code.value
        task.error_message = error.kwargs.get("reason") or str(error)
        if request_id:
            task.request_id = request_id
        await _commit(session)

        # 失败通知：经 NotificationService 派发；params 只携带 error_code，
        # 由前端/后端 i18n 目录按 error_code 渲染友好文案，绝不外泄原始内部错误。
        NotificationService.notify(
            session,
            type=NotificationType.TASK_FAILED,
            user_id=str(task.user_id),
            params={
                "task_title": task.title or "未命名任务",
                "error_code": error.code.value,
                "source_type": task.source_type,
            },
            task_id=str(task.id),
        )

        trace_id = request_id or uuid4().hex
        message = json.dumps(
            {
                "kind": "task_progress",
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
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error(
            "Task %s: _mark_failed best-effort path failed, suppressed to avoid task retry: %s",
            getattr(task, "id", "?"),
            exc,
            exc_info=True,
        )


async def _process_task(task_id: str, request_id: str | None) -> None:
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
                # 纵深防御：绝不处理落在属主前缀之外的 key（即使脏数据进了库）
                if not str(task.source_key).startswith(f"upload/{task.user_id}/"):
                    raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="source_key")

                # 服务端强制校验对象真实大小（presigned PUT 无法签 size，客户端 size_bytes 可绕过）
                size_storage: StorageService = await _maybe_await(get_storage_service(user_id=str(task.user_id)))
                await _enforce_object_size_limit(size_storage, task.source_key)

                # 提取音频时长并同步文件到 MinIO（用于前端播放）
                if not task.duration_seconds:
                    logger.info(
                        "Task %s: Extracting audio duration and syncing to MinIO",
                        task_id,
                        extra={"task_id": task_id, "source_key": task.source_key},
                    )

                    # 下载文件到临时目录（用于提取时长和上传到 MinIO）
                    # 使用 SmartFactory 获取 COS storage（异步调用）
                    cos_storage: StorageService = await _maybe_await(get_storage_service(user_id=str(task.user_id)))
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
                            _call_factory(get_storage_service, "minio", user_id=str(task.user_id))
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
                    raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="upload_presign_expires")
                # 使用 COS 存储生成带签名的 URL 供 ASR 访问
                # 使用 SmartFactory 获取 COS storage
                cos_storage = await _maybe_await(_call_factory(get_storage_service, user_id=str(task.user_id)))
                audio_candidates.append(cos_storage.generate_presigned_url(task.source_key, expires_in))
            else:
                if task.source_key:
                    # YouTube 下载的音频也使用 COS 存储（双存储方案）
                    expires_in = settings.UPLOAD_PRESIGN_EXPIRES
                    if not expires_in:
                        raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="upload_presign_expires")
                    # 使用 SmartFactory 获取 COS storage
                    cos_storage = await _maybe_await(_call_factory(get_storage_service, user_id=str(task.user_id)))
                    audio_candidates.append(cos_storage.generate_presigned_url(task.source_key, expires_in))
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

            # 幂等保护（D5-retry 钱路）：以「终态计费记录 ASRUsage(status=success)」为准，而非仅看转写是否存在。
            # Celery autoretry_for 会从头重跑整个任务，decide_asr_action 把四种重试状态收敛成一个动作：
            #   SKIP_ALL           已有 success 计费 -> 复用转写，不再付费/计费
            #   FINALIZE_COST      有转写但无 success 计费 -> 复用转写、只原子补记一次计费（关闭「转写后/ASRUsage 前」少计费窗口）
            #   RESUME_AFTER_CLAIM 有 processing claim 但无转写 -> 上次付费可能已扣费，重跑但复用 claim + 告警对账（双扣费窗口）
            #   FULL_RUN           干净首跑 -> 付费前写 claim、付费转写、补记计费
            existing_transcripts = (
                (
                    await session.execute(
                        select(Transcript).where(Transcript.task_id == task_id).order_by(Transcript.sequence)
                    )
                )
                .scalars()
                .all()
            )
            usage_rows = (
                (
                    await session.execute(
                        select(ASRUsage).where(ASRUsage.task_id == str(task_id)).order_by(ASRUsage.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            success_usage = next((u for u in usage_rows if u.status == "success"), None)
            processing_claim = next((u for u in usage_rows if u.status == "processing"), None)
            asr_action = decide_asr_action(
                has_success_usage=success_usage is not None,
                has_transcripts=bool(existing_transcripts),
                has_processing_claim=processing_claim is not None,
            )

            if asr_action is AsrRetryAction.SKIP_ALL:
                logger.info(
                    "Task %s: ASR + cost already finalized (success usage present), skipping (retry-safe)",
                    task_id,
                )
                transcripts = list(existing_transcripts)
                segments = _segments_from_transcripts(transcripts)
            elif asr_action is AsrRetryAction.FINALIZE_COST:
                # 上次已落库转写但崩在计费前：复用转写、跳过付费 ASR，只原子补记一次计费。
                # provider/variant 取自上次实跑的持久记录（claim 优先，否则 task），不能重新调度，
                # 否则可能按另一家 provider 计费。
                transcripts = list(existing_transcripts)
                logger.warning(
                    "Task %s: %d transcripts present but cost not finalized; recording cost once (retry-safe)",
                    task_id,
                    len(transcripts),
                )
                diarization = task.options.get("enable_speaker_diarization") if isinstance(task.options, dict) else None
                if processing_claim is not None:
                    finalize_provider = processing_claim.provider
                    finalize_variant = processing_claim.variant
                    finalize_audio_url = processing_claim.audio_url
                else:
                    finalize_provider = _resolve_asr_provider(task) or task.asr_provider
                    finalize_variant = _resolve_asr_variant(task)
                    finalize_audio_url = None
                segments = _segments_from_transcripts(transcripts)
                finalize_duration = _estimate_asr_duration(task, segments)
                finalize_service: ASRService | None = None
                if finalize_provider:
                    # provider 实例只用于可选的 estimate_cost；构造失败不能阻断计费，
                    # 否则会把「已有转写」的任务误判为失败（_mark_failed）。
                    try:
                        finalize_service = await _maybe_await(
                            _call_factory(get_asr_service, str(task.user_id), provider=finalize_provider)
                        )
                    except Exception as exc:
                        logger.warning(
                            "Task %s: ASR service lookup failed during FINALIZE_COST; "
                            "recording cost without estimate_cost: %s",
                            task.id,
                            exc,
                        )
                        finalize_service = None
                await _finalize_asr_cost(
                    session,
                    task,
                    provider_name=(getattr(finalize_service, "provider", None) or finalize_provider or "unknown"),
                    asr_variant=finalize_variant or "file",
                    duration_seconds=float(finalize_duration),
                    asr_service=finalize_service,
                    successful_audio_url=finalize_audio_url,
                    diarization=diarization,
                    processing_time_ms=0,
                    claim_row=processing_claim,
                )
            else:
                # 使用 SmartFactory 获取 ASR 服务（自动选择最优服务）
                asr_provider = _resolve_asr_provider(task)
                asr_variant = _resolve_asr_variant(task)
                diarization = None
                if isinstance(task.options, dict):
                    diarization = task.options.get("enable_speaker_diarization")
                if not asr_provider:
                    # 确定可用的 variant 列表
                    variants = [asr_variant] if asr_variant != "file" else ["file", "file_fast"]
                    if "file_fast" in variants and not _supports_file_fast(str(task.user_id)):
                        variants = [variant for variant in variants if variant != "file_fast"]
                        if not variants:
                            variants = ["file"]

                    # 使用 ASRScheduler 智能调度（综合考虑免费额度、成本、质量、特性等）
                    task_features = TaskFeatures(
                        diarization=diarization is True,
                        word_level=False,  # 暂不支持词级时间戳需求
                    )

                    # 优先尝试第一个 variant
                    for variant in variants:
                        asr_provider = await ASRScheduler.select_best_provider(
                            session=session,
                            user_id=str(task.user_id),
                            variant=variant,
                            task_features=task_features,
                        )
                        if asr_provider:
                            asr_variant = variant
                            break

                    # 如果没有可用的提供商，使用第一个注册的提供商作为降级
                    if not asr_provider:
                        all_providers = ServiceRegistry.list_services("asr")
                        if all_providers:
                            asr_provider = all_providers[0]
                            logger.warning(
                                "No ASR provider selected by scheduler, falling back to: %s",
                                asr_provider,
                            )
                asr_service: ASRService = await _maybe_await(
                    _call_factory(get_asr_service, str(task.user_id), provider=asr_provider)
                )
                # 归一为「实际会被执行的变体」：钉死的 provider 不经上面 auto-select 分支的
                # _supports_file_fast 剥离，若其 transcribe 不消费 asr_variant 就只会跑标准版 file，
                # 必须据此计费/记录，避免给只支持标准版的 provider 按 file_fast 多收（D5-variant）。
                asr_variant = _effective_asr_variant(asr_service.transcribe, asr_variant)
                if asr_provider:
                    task.asr_provider = asr_provider
                    if isinstance(task.options, dict):
                        task.options["asr_variant"] = asr_variant
                    await _commit(session)
                provider_name = getattr(asr_service, "provider", None) or asr_provider

                # 付费 ASR 调用前写 claim（仅 FULL_RUN）：即便崩在转写中途/落库前，重试也能据此检测到
                # 「上一次可能已扣费」，把双扣费风险显式记录、对账，而不是悄悄重复计费。RESUME 复用既有 claim 并告警。
                claim_row: ASRUsage | None
                if asr_action is AsrRetryAction.RESUME_AFTER_CLAIM and processing_claim is not None:
                    claim_row = processing_claim
                    logger.warning(
                        "Task %s: prior ASR attempt was claimed but left no transcripts; re-running paid ASR. "
                        "The earlier attempt may have charged the provider — reconcile ASRUsage claim id=%s.",
                        task_id,
                        str(processing_claim.id),
                    )
                else:
                    claim_row = ASRUsage(
                        user_id=str(task.user_id),
                        task_id=str(task.id),
                        provider=provider_name or "unknown",
                        variant=asr_variant,
                        duration_seconds=0.0,
                        status="processing",
                    )
                    _add_record(session, claim_row)
                    await _commit(session)  # claim 在付费调用前落库，崩溃后可检测/对账

                last_error: BusinessError | None = None
                segments: list[TranscriptSegment] = []
                asr_start_time = time.time()
                successful_audio_url: str | None = None

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
                        successful_audio_url = audio_url
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
                            "Task %s: ASR failed for URL %d/%d with error %s: %s, trying next URL if available",
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
                # 原子补记计费并把 claim 收尾为 success（终态幂等标记）。三处计费写入同一事务提交，
                # 关闭「转写后/ASRUsage 前」少计费窗口，并避免重试重复累加配额（详见 _finalize_asr_cost）。
                asr_processing_time_ms = int((time.time() - asr_start_time) * 1000)
                await _finalize_asr_cost(
                    session,
                    task,
                    provider_name=provider_name or "unknown",
                    asr_variant=asr_variant,
                    duration_seconds=float(duration_seconds),
                    asr_service=asr_service,
                    successful_audio_url=successful_audio_url,
                    diarization=diarization,
                    processing_time_ms=asr_processing_time_ms,
                    claim_row=claim_row,
                )

            try:
                await ingest_task_chunks_async(session, task, transcripts, str(task.user_id))
            except Exception as exc:
                logger.warning(
                    "Task %s: RAG chunk ingest failed: %s",
                    task_id,
                    exc,
                    exc_info=True,
                    extra={"task_id": task_id},
                )

            # ========== 转写润色（固定步骤）==========
            await _update_task(session, task, "polishing", 72, "polishing", request_id)

            try:
                polish_query = select(Transcript).where(Transcript.task_id == task_id).order_by(Transcript.sequence)
                polish_result = await session.execute(polish_query)
                transcript_rows = polish_result.scalars().all()

                seg_dicts = [
                    {
                        "sequence": t.sequence,
                        "content": t.content,
                        "start_time": float(t.start_time),
                        "end_time": float(t.end_time),
                    }
                    for t in transcript_rows
                ]

                polish_provider, polish_model_id = _resolve_llm_selection(task, str(task.user_id))
                polish_llm: LLMService = await _maybe_await(
                    get_llm_service(polish_provider, polish_model_id, str(task.user_id))
                )

                polish_results = await polish_transcripts(polish_llm, seg_dicts)

                changed_count = 0
                for pr in polish_results:
                    if pr.changed:
                        for t in transcript_rows:
                            if t.sequence == pr.sequence:
                                t.original_content = pr.original_content
                                t.content = pr.polished_content
                                t.is_edited = True
                                changed_count += 1
                                break

                if changed_count > 0:
                    await _commit(session)

                logger.info(
                    "Task %s: Polish completed, %d/%d segments changed",
                    task_id,
                    changed_count,
                    len(seg_dicts),
                    extra={
                        "task_id": task_id,
                        "changed_segments": changed_count,
                        "total_segments": len(seg_dicts),
                    },
                )

            except Exception as exc:
                logger.warning(
                    "Task %s: Polish failed, continuing with original transcripts: %s",
                    task_id,
                    exc,
                    extra={"task_id": task_id, "error": str(exc)},
                )
            # ========== 润色结束 ==========

            await _update_task(session, task, "summarizing", 82, "summarizing", request_id)

            # 提取content_style和LLM配置
            options = task.options or {}
            content_style = options.get("summary_style", "meeting")
            if not isinstance(content_style, str):
                content_style = "meeting"

            # 解析具体的 LLM provider 与 model_id（与润色步骤、YouTube 路径保持一致）
            # 用户未显式指定时回退到默认 provider + 默认 model_id，避免把 None 透传给
            # SmartFactory.get_service("llm", ...) 触发 "model_id is required" 而导致整任务失败
            llm_provider_option, llm_model_id_option = _resolve_llm_selection(task, str(task.user_id))

            logger.info(
                "Task %s: Starting quality-aware summary generation (style: %s)",
                task_id,
                content_style,
                extra={
                    "task_id": task_id,
                    "content_style": content_style,
                    "segments_count": len(segments),
                },
            )

            # 从 DB 读取最新转写内容（可能已被润色修改）
            summarize_query = select(Transcript).where(Transcript.task_id == task_id).order_by(Transcript.sequence)
            summarize_result = await session.execute(summarize_query)
            latest_transcripts = summarize_result.scalars().all()
            latest_segments = [
                TranscriptSegment(
                    content=t.content,
                    start_time=float(t.start_time),
                    end_time=float(t.end_time),
                    speaker_id=t.speaker_id,
                    confidence=float(t.confidence) if t.confidence else None,
                    words=[],
                )
                for t in latest_transcripts
            ]

            # 使用新的质量感知摘要生成函数
            try:
                summaries, summary_metadata = await generate_summaries_with_quality_awareness(
                    task_id=str(task.id),
                    segments=latest_segments,
                    content_style=content_style,
                    session=session,
                    user_id=str(task.user_id),
                    provider=llm_provider_option,
                    model_id=llm_model_id_option,
                )

                # 记录元数据
                logger.info(
                    "Task %s: Summary generation completed - quality: %s, confidence: %.2f, "
                    "provider: %s, model: %s, summaries: %d",
                    task_id,
                    summary_metadata["quality_score"],
                    summary_metadata["avg_confidence"],
                    summary_metadata["llm_provider"],
                    summary_metadata["llm_model"],
                    summary_metadata["summaries_generated"],
                    extra={"task_id": task_id, "summary_metadata": summary_metadata},
                )

                # 更新任务的llm_provider字段
                if summary_metadata.get("llm_provider"):
                    task.llm_provider = summary_metadata["llm_provider"]

                # 保存所有摘要到数据库
                session.add_all(summaries)
                await _commit(session)

                logger.info(
                    "Task %s: All summaries saved to database",
                    task_id,
                    extra={"task_id": task_id, "summary_count": len(summaries)},
                )

            except Exception as exc:
                logger.error(
                    "Task %s: Summary generation failed: %s",
                    task_id,
                    exc,
                    exc_info=True,
                    extra={"task_id": task_id},
                )
                # 摘要生成失败，任务标记为failed
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_GENERATION_FAILED,
                    reason=f"Failed to generate summaries: {exc}",
                ) from exc

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
def process_audio(self, task_id: str, request_id: str | None = None) -> None:
    asyncio.run(_process_task(task_id, request_id))
