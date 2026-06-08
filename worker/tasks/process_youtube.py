from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import subprocess  # nosec B404
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session
from yt_dlp import YoutubeDL

from app.config import settings
from app.core.asr_scheduler import ASRScheduler, TaskFeatures
from app.core.config_manager import ConfigManager
from app.core.exceptions import BusinessError
from app.core.registry import ServiceRegistry
from app.core.smart_factory import SmartFactory
from app.core.task_stages import StageType
from app.i18n.codes import ErrorCode
from app.models.asr_usage import ASRUsage
from app.models.llm_usage import LLMUsage
from app.models.summary import Summary
from app.models.task import Task
from app.models.transcript import Transcript
from app.services.asr.base import TranscriptSegment, WordTimestamp
from app.services.asr_quota_service import record_usage_sync
from app.services.notifications.service import NotificationService
from app.services.notifications.types import NotificationType
from app.services.rag import ingest_task_chunks_sync
from app.services.summary.markdown_fence import strip_markdown_fence
from app.services.summary.style_catalog import normalize_content_style
from app.services.summary.style_resolution import (
    is_auto_style,
    persist_detected_style,
    resolve_content_style,
)
from app.services.task_service import TaskService
from app.services.transcript_polish import polish_transcripts
from worker.celery_app import celery_app
from worker.db import get_sync_db_session
from worker.redis_client import publish_task_update_sync
from worker.stage_manager import StageManager
from worker.tasks.asr_idempotency import AsrRetryAction, decide_asr_action
from worker.tasks.image_generator import (
    build_image_specs,
    is_auto_images_enabled,
)

logger = logging.getLogger("worker.process_youtube")


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
        # provider 来自 /llm/models 的展示分组标签（deepseek/openai/litellm…），不是注册服务名。
        # 文本 LLM 统一经 proxy 路由，真正的选择键是 model_id —— 把展示名归一到注册的默认服务并
        # 保留 model_id，否则 SmartFactory.get_service 会因 "Service llm:<展示名> not found" 崩在 worker。
        if provider not in ServiceRegistry.list_services("llm"):
            provider = _select_default_llm_provider()
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

    与 process_audio._effective_asr_variant 同口径：provider 的 transcribe 不消费 asr_variant 时
    只会跑标准版 file，计费必须按 file，避免给只支持标准版的 provider 按极速版(file_fast)多收。
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


def _finalize_asr_cost_sync(
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
    """原子补记一次 ASR 计费并把终态 ASRUsage 置为 ``success``（D5-retry 钱路，sync 版）。

    与 process_audio._finalize_asr_cost 同口径：``record_usage_sync(commit=False)`` 累加 AsrUserQuota、
    ``consume_quota`` 只 flush 分拆免费/付费，最后随 ASRUsage(success) 一次性 commit——三者同事务原子写入，
    关闭「转写后/ASRUsage 前」少计费窗口，并避免重试重复累加配额。``claim_row`` 非空则就地收尾（不新增行）。
    """
    duration_seconds = float(duration_seconds or 0.0)
    estimated_cost = 0.0
    free_quota_consumed = 0.0
    paid_duration_seconds = duration_seconds
    actual_paid_cost = 0.0

    if provider_name and duration_seconds > 0:
        if asr_service is not None and hasattr(asr_service, "estimate_cost"):
            estimated_cost = asr_service.estimate_cost(int(duration_seconds), variant=asr_variant)
        # AsrUserQuota 累加：参与本事务，不单独提交（commit=False）
        record_usage_sync(
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

            consumption = asyncio.run(
                AsrFreeQuotaService.consume_quota(
                    session,
                    provider_name,
                    asr_variant,
                    duration_seconds,
                    user_id=None,  # 全局配额
                )
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
        session.add(usage)
    session.commit()  # 原子提交：AsrUserQuota + 免费额度周期 + ASRUsage(success)
    logger.info(
        "Task %s: ASRUsage finalized (status=success) - provider=%s, duration=%ds, free=%.1fs, paid=%.1fs, cost=%.4f",
        task.id,
        usage.provider,
        int(duration_seconds),
        free_quota_consumed,
        paid_duration_seconds,
        actual_paid_cost,
    )


def _finalize_existing_transcript_cost_sync(session: Session, task: Task, task_id: str) -> None:
    """FINALIZE_COST：复用已落库转写，按上次实跑的 provider/variant 原子补记一次计费。

    provider/variant/audio_url 取自 processing claim（优先）或 task，不重新调度，避免按另一家 provider 计费。
    """
    transcripts = session.query(Transcript).filter(Transcript.task_id == task_id).order_by(Transcript.sequence).all()
    if not transcripts:
        return
    claim = (
        session.query(ASRUsage)
        .filter(ASRUsage.task_id == str(task_id), ASRUsage.status == "processing")
        .order_by(ASRUsage.created_at.desc())
        .first()
    )
    diarization = task.options.get("enable_speaker_diarization") if isinstance(task.options, dict) else None
    if claim is not None:
        provider = claim.provider
        variant = claim.variant
        audio_url = claim.audio_url
    else:
        provider = _resolve_asr_provider(task) or task.asr_provider
        variant = _resolve_asr_variant(task)
        audio_url = None
    segments = _segments_from_transcripts(transcripts)
    duration = _estimate_asr_duration(task, segments)
    service = None
    if provider:
        # provider 实例只用于可选的 estimate_cost；构造失败（注册表/凭证/实例化，force_new=True）
        # 不能阻断计费——否则异常冒泡会让 youtube 任务 autoretry 进「卡死且从未计费」状态。
        try:
            service = asyncio.run(SmartFactory.get_service("asr", user_id=str(task.user_id), provider=provider))
        except Exception as exc:
            logger.warning(
                "Task %s: ASR service lookup failed during FINALIZE_COST; recording cost without estimate_cost: %s",
                task_id,
                exc,
            )
            service = None
    logger.warning(
        "Task %s: %d transcripts present but cost not finalized; recording cost once (retry-safe)",
        task_id,
        len(transcripts),
    )
    _finalize_asr_cost_sync(
        session,
        task,
        provider_name=(getattr(service, "provider", None) or provider or "unknown"),
        asr_variant=variant or "file",
        duration_seconds=float(duration),
        asr_service=service,
        successful_audio_url=audio_url,
        diarization=diarization,
        processing_time_ms=0,
        claim_row=claim,
    )


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
    now = datetime.now(UTC)
    ext = Path(filename).suffix.lower()
    file_id = uuid4().hex
    return f"youtube/{user_id}/{now:%Y/%m/%d}/{file_id}{ext}"


def _extract_direct_url(info: dict) -> str | None:
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


# —— yt-dlp 抓取韧性：超时 + 瞬时/永久错误分类 + 仅瞬时重试 ——
# 命中即判为「永久失败」的关键词（私有/删除/不可用/地域/需登录/坏链接）：重试无意义，
# 立即失败并给出友好错误码，避免空耗 worker、配额与时间。
_PERMANENT_YOUTUBE_KEYWORDS: tuple[str, ...] = (
    "private video",
    "video unavailable",
    "video is unavailable",
    "this video isn't available",
    "isn't available",
    "has been removed",
    "removed by the uploader",
    "is not available",
    "members-only",
    "members only",
    "sign in",
    "login required",
    "not available in your country",
    "geo-restrict",
    "incomplete youtube id",
    "invalid youtube id",
    "unsupported url",
    "is not a valid url",
    "no video formats found",
)


def _is_transient_youtube_error(exc: Exception) -> bool:
    """瞬时(可重试) vs 永久(不可重试)。命中永久关键词或已判定的 BusinessError → 不重试；
    其余（网络超时 / 连接重置 / HTTP 5xx / 分片错误）默认视为瞬时，值得重试。"""
    if isinstance(exc, BusinessError):
        return False
    msg = str(exc).lower()
    return not any(keyword in msg for keyword in _PERMANENT_YOUTUBE_KEYWORDS)


def _classify_youtube_error(exc: Exception) -> BusinessError:
    """把 yt-dlp 原始异常归一成带友好错误码/文案的 BusinessError，用于失败阶段的终态标记。
    （承接旧 create 路径里 _validate_youtube_video_sync 的关键词分类逻辑。）"""
    if isinstance(exc, BusinessError):
        return exc
    msg = str(exc).lower()
    if any(k in msg for k in ("private", "members", "sign in", "login")):
        return BusinessError(ErrorCode.YOUTUBE_VIDEO_UNAVAILABLE)
    if any(k in msg for k in ("unavailable", "has been removed", "removed by", "is not available", "isn't available")):
        return BusinessError(ErrorCode.YOUTUBE_VIDEO_UNAVAILABLE)
    if any(k in msg for k in ("incomplete youtube id", "invalid youtube id", "unsupported url", "is not a valid url")):
        return BusinessError(ErrorCode.INVALID_URL_FORMAT)
    if "country" in msg or "geo-restrict" in msg:
        return BusinessError(ErrorCode.YOUTUBE_DOWNLOAD_FAILED, reason="该视频存在地域限制，当前地区无法访问")
    if "timeout" in msg or "timed out" in msg:
        return BusinessError(ErrorCode.YOUTUBE_DOWNLOAD_FAILED, reason="网络超时，请稍后重试")
    return BusinessError(ErrorCode.YOUTUBE_DOWNLOAD_FAILED, reason=str(exc)[:200])


def _youtube_ydl_opts() -> dict[str, Any]:
    """yt-dlp 基础选项 + 韧性参数。在原 format/outtmpl/noplaylist/quiet 之上注入：
    - socket_timeout：单连接读/连超时，避免慢连接无限期挂住占满 worker；
    - retries / fragment_retries / extractor_retries：yt-dlp 库内重试，吸收单次请求内的瞬时抖动。
    解析与下载共用同一组选项（下载路径再单独挂 progress_hooks）。"""
    return {
        "format": _get_download_format(),
        "outtmpl": str(_get_download_dir() / _get_output_template()),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": settings.YOUTUBE_SOCKET_TIMEOUT,
        "retries": settings.YOUTUBE_DOWNLOAD_RETRIES,
        "fragment_retries": settings.YOUTUBE_DOWNLOAD_RETRIES,
        "extractor_retries": settings.YOUTUBE_DOWNLOAD_RETRIES,
    }


def _run_with_youtube_retry(fn: Callable[[], Any], *, max_attempts: int, what: str) -> Any:
    """对 yt-dlp 调用做「仅瞬时错误」的应用层重试（指数退避，含首次共 max_attempts 次）。

    永久错误（私有/删除/地域/坏链接）立即抛出不重试；瞬时错误（超时/连接/5xx）退避后重试。
    这是 yt-dlp 库内 retries 之上的第二层：库内重试覆盖单次请求内的分片/连接抖动，
    本层覆盖「整次解析/下载」级别的失败（如握手阶段超时直接抛错、库内重试够不着的情况）。
    """
    attempts = max(1, max_attempts)
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - 需按错误内容判定瞬时/永久
            last_exc = exc
            if attempt >= attempts or not _is_transient_youtube_error(exc):
                raise
            delay = min(2.0 * (2 ** (attempt - 1)), 20.0)
            logger.warning(
                "youtube %s transient failure (attempt %d/%d), retrying in %.1fs: %s",
                what,
                attempt,
                attempts,
                delay,
                exc,
            )
            time.sleep(delay)
    if last_exc is not None:  # 理论不可达（循环内已 return 或 raise）
        raise last_exc
    raise RuntimeError("unreachable")  # pragma: no cover


def _extract_youtube_info(url: str) -> tuple[str | None, str | None]:
    def _do() -> tuple[str | None, str | None]:
        with YoutubeDL(_youtube_ydl_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get("title") if isinstance(info, dict) else None
            direct_url = info if isinstance(info, str) else _extract_direct_url(info)
            return direct_url, title

    return _run_with_youtube_retry(_do, max_attempts=settings.YOUTUBE_RESOLVE_MAX_ATTEMPTS, what="resolve")


def _download_youtube(url: str, progress_callback=None) -> str:
    ydl_opts = _youtube_ydl_opts()
    if progress_callback is not None:
        ydl_opts["progress_hooks"] = [progress_callback]

    def _do() -> str:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)

    return _run_with_youtube_retry(_do, max_attempts=settings.YOUTUBE_DOWNLOAD_MAX_ATTEMPTS, what="download")


def _get_audio_duration(file_path: str) -> int | None:
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


def _get_task(session: Session, task_id: str) -> Task | None:
    result = session.execute(select(Task).where(Task.id == task_id, Task.deleted_at.is_(None)))
    return result.scalar_one_or_none()


def _update_metadata(
    session: Session,
    task: Task,
    direct_url: str | None,
    title: str | None,
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
    duration_seconds: int | None = None,
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
    stage: str | None,
    request_id: str | None,
) -> None:
    task.status = status
    task.progress = max(task.progress or 0, progress)
    task.stage = stage
    if request_id:
        task.request_id = request_id
    session.commit()

    # 任务完成：经唯一收口 NotificationService 派发
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
        "progress": task.progress,
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

    # Publish to both task-specific and user-global channels
    publish_task_update_sync(task.id, str(task.user_id), message)


def _mark_failed(session: Session, task: Task, error: BusinessError, request_id: str | None) -> None:
    # 失败处理必须「尽力而为且绝不向外抛异常」：它是各 except 分支的收尾步骤，若自身抛错
    # （publish / 通知写库 / commit 失败）会冒泡触发 Celery autoretry_for=(Exception,) 把整个
    # 任务（含已付费 ASR）从头重跑 → 重复转写/重复扣费 (D5-retry gap b)。整体兜底，子步骤异常只记日志。
    try:
        task.status = "failed"
        task.progress = 0
        task.error_code = error.code.value
        task.error_message = error.kwargs.get("reason") or str(error)
        if request_id:
            task.request_id = request_id
        session.commit()

        # 失败通知：params 只携带 error_code，由 i18n 目录渲染友好文案，绝不外泄原始错误。
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

        # Publish to both task-specific and user-global channels
        publish_task_update_sync(task.id, str(task.user_id), message)
    except Exception as exc:
        logger.error(
            "Task %s: _mark_failed best-effort path failed, suppressed to avoid task retry: %s",
            getattr(task, "id", "?"),
            exc,
            exc_info=True,
        )


def _init_overview_images(summary: Summary, content_style: str | None) -> bool:
    """为 overview summary 初始化 images=[{status:"pending"}…] 并保留 content 占位锚点。

    复用 build_image_specs：若摘要无 {{IMAGE:…}} 占位符，会规划默认占位符并写回 summary.content。
    返回是否产生了图集（True 表示需后续异步生图）。非 overview / 未启用自动配图 / 无可规划占位符 -> False。
    """
    if summary.summary_type != "overview":
        return False
    if not is_auto_images_enabled("overview", content_style):
        return False
    new_content, specs = build_image_specs(summary.content, content_style)
    if not specs:
        return False
    summary.content = new_content  # 保留 {{IMAGE:…}} 锚点（默认占位符已插入）
    summary.images = specs
    return True


def _enqueue_summary_images(
    *,
    task_id: str,
    user_id: str,
    summaries: list[Summary],
    content_style: str | None,
) -> None:
    """completed 之后异步触发 overview 配图任务（每个有 pending images 的 overview 一个任务）。

    best-effort：入队失败只记日志，绝不影响已 completed 的任务。
    """
    for summary in summaries:
        if summary.summary_type != "overview" or not summary.images:
            continue
        if not any(item.get("status") == "pending" for item in summary.images):
            continue
        try:
            celery_app.send_task(
                "worker.tasks.generate_summary_images_async",
                kwargs={
                    "task_id": task_id,
                    "user_id": user_id,
                    "summary_id": str(summary.id),
                    "content": summary.content,
                    "content_style": content_style,
                },
            )
        except Exception:
            logger.warning("Task %s: enqueue async summary images failed, suppressed", task_id, exc_info=True)


def _process_youtube(
    task_id: str,
    request_id: str | None,
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
        # 严格校验拉取 URL（白名单主机 + 拒绝 IP 字面量），防 SSRF。
        # auto-transcribe 路径不经 create_task，此处是该路径的主要防线。
        try:
            TaskService.validate_ingest_url(task.source_url)
        except BusinessError as exc:
            _mark_failed(session, task, exc, request_id)
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
            error = _classify_youtube_error(exc)
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
                raise BusinessError(ErrorCode.FILE_PROCESSING_ERROR, reason="download produced empty file")
        except Exception as exc:
            logger.exception("youtube download failed: %s", exc)
            if not direct_url:
                error = _classify_youtube_error(exc)
                with get_sync_db_session() as session:
                    task = _get_task(session, task_id)
                    if task is None:
                        return
                    stage_manager.fail_stage(session, StageType.DOWNLOAD, error.code, str(error))
                    _mark_failed(session, task, error, request_id)
                return
            # 下载失败但有直链：跳过下载/转码/上传，直接用直链转写（任务仍会成功）。
            # 标记为 skipped 而非 failed，避免详情页出现误导性的「失败阶段」。
            with get_sync_db_session() as session:
                stage_manager.skip_stage(session, StageType.DOWNLOAD, "下载失败，回退到直链流")
                for fallback_stage in (StageType.TRANSCODE, StageType.UPLOAD_STORAGE):
                    stage_manager.start_stage(session, fallback_stage)
                    stage_manager.skip_stage(session, fallback_stage, "回退到直链流")

        # 只有成功下载文件时才继续转码和上传
        if original_filename:
            with get_sync_db_session() as session:
                task = _get_task(session, task_id)
                if task is None:
                    return
                _update_task(session, task, "downloaded", 25, "downloaded", request_id)
                stage_manager.complete_stage(session, StageType.DOWNLOAD)

        # ========== 阶段 3: 转码音频 ==========
        try:
            if original_filename:
                with get_sync_db_session() as session:
                    task = _get_task(session, task_id)
                    if task is None:
                        return
                    _update_task(session, task, "transcoding", 27, "transcoding", request_id)
                    stage_manager.start_stage(session, StageType.TRANSCODE)
                filename = _transcode_to_wav_16k(original_filename)
                with get_sync_db_session() as session:
                    stage_manager.complete_stage(session, StageType.TRANSCODE)
            else:
                # 下载失败，但有 direct_url，跳过转码和上传（阶段已在 download 回退分支标记 skipped）
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
                stage_manager.fail_stage(session, StageType.TRANSCODE, error.code, str(error))
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
                    stage_manager.start_stage(session, StageType.UPLOAD_STORAGE)

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
                with get_sync_db_session() as session:
                    stage_manager.complete_stage(session, StageType.UPLOAD_STORAGE)
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
            upload_error = BusinessError(ErrorCode.FILE_UPLOAD_FAILED)
            with get_sync_db_session() as session:
                task = _get_task(session, task_id)
                if task is None:
                    return
                stage_manager.fail_stage(session, StageType.UPLOAD_STORAGE, upload_error.code, str(upload_error))
                _mark_failed(session, task, upload_error, request_id)
            return
        else:
            with get_sync_db_session() as session:
                task = _get_task(session, task_id)
                if task is None:
                    return
                if filename:
                    # 转码成功但上传失败，且有直链：跳过上传，改用直链转写（任务仍会成功）
                    stage_manager.skip_stage(session, StageType.UPLOAD_STORAGE, "上传失败，回退到直链流")
                _update_task(session, task, "resolved", 35, "resolved", request_id)

    # ========== 检查是否可以跳过转写阶段 ==========
    skip_transcribe = False
    asr_action = AsrRetryAction.FULL_RUN
    segments = []
    with get_sync_db_session() as session:
        task = _get_task(session, task_id)
        # 幂等保护（D5-retry 钱路）：以「终态计费记录 ASRUsage(status=success)」为准，而非仅看转写是否存在。
        # decide_asr_action 把四种重试状态收敛成动作（与 process_audio 同口径）。
        existing_transcripts = session.query(Transcript).filter(Transcript.task_id == task_id).count()
        usage_rows = (
            session.query(ASRUsage).filter(ASRUsage.task_id == str(task_id)).order_by(ASRUsage.created_at.desc()).all()
        )
        asr_action = decide_asr_action(
            has_success_usage=any(u.status == "success" for u in usage_rows),
            has_transcripts=existing_transcripts > 0,
            has_processing_claim=any(u.status == "processing" for u in usage_rows),
        )

        if asr_action in (AsrRetryAction.SKIP_ALL, AsrRetryAction.FINALIZE_COST):
            skip_transcribe = True
            logger.info(
                "[%s] Skipping transcription (found %s existing transcripts, action=%s)",
                request_id,
                existing_transcripts,
                asr_action.value,
            )
            stage_manager.skip_stage(session, StageType.TRANSCRIBE, "Transcripts already exist")

            transcripts_list = (
                session.query(Transcript).filter(Transcript.task_id == task_id).order_by(Transcript.sequence).all()
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

    # 复用转写但尚未计费：在独立 session 中原子补记一次成本（关闭「转写后/ASRUsage 前」少计费窗口）。
    # 该块在主 transcribe try/except 之外，计费补记失败不得静默 autoretry 进卡死状态：
    # 标记失败（转写仍在、processing claim 留作对账线索），与 process_audio 主 try 的失败处理对齐。
    if asr_action is AsrRetryAction.FINALIZE_COST:
        try:
            with get_sync_db_session() as session:
                task = _get_task(session, task_id)
                if task is not None:
                    _finalize_existing_transcript_cost_sync(session, task, task_id)
        except Exception as exc:
            logger.exception("Task %s: FINALIZE_COST cost recording failed: %s", task_id, exc)
            with get_sync_db_session() as session:
                task = _get_task(session, task_id)
                if task is not None:
                    error = BusinessError(ErrorCode.ASR_SERVICE_FAILED, reason=str(exc))
                    _mark_failed(session, task, error, request_id)
            return

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
                        SmartFactory.get_service("storage", provider="cos", user_id=str(task.user_id))
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
                        asr_provider = asyncio.run(
                            ASRScheduler.select_best_provider(
                                session=session,
                                user_id=str(task.user_id),
                                variant=variant,
                                task_features=task_features,
                            )
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
                asr_service = asyncio.run(
                    SmartFactory.get_service(
                        "asr",
                        user_id=str(task.user_id),
                        provider=asr_provider,
                    )
                )
                # 归一为「实际会被执行的变体」，与 process_audio 同口径，避免给只支持标准版的
                # 钉死 provider 按 file_fast 多收（D5-variant）。
                asr_variant = _effective_asr_variant(asr_service.transcribe, asr_variant)
                if asr_provider:
                    task.asr_provider = asr_provider
                    if isinstance(task.options, dict):
                        task.options["asr_variant"] = asr_variant
                    session.commit()
                provider_name = asr_service.provider or "unknown"

                # 付费 ASR 调用前写 claim：即便崩在转写中途/落库前，重试也能据此检测「上一次可能已扣费」，
                # 据此显式记录并对账双扣费风险，而不是悄悄重复计费。RESUME（已有 processing claim）复用既有行并告警。
                claim_row: ASRUsage | None = (
                    session.query(ASRUsage)
                    .filter(ASRUsage.task_id == str(task_id), ASRUsage.status == "processing")
                    .order_by(ASRUsage.created_at.desc())
                    .first()
                )
                if claim_row is not None:
                    logger.warning(
                        "Task %s: prior ASR attempt was claimed but left no transcripts; re-running paid ASR. "
                        "The earlier attempt may have charged the provider — reconcile ASRUsage claim id=%s.",
                        task_id,
                        str(claim_row.id),
                    )
                else:
                    claim_row = ASRUsage(
                        user_id=str(task.user_id),
                        task_id=str(task.id),
                        provider=provider_name,
                        variant=asr_variant,
                        duration_seconds=0.0,
                        status="processing",
                    )
                    session.add(claim_row)
                    session.commit()  # claim 在付费调用前落库，崩溃后可检测/对账

                last_error: BusinessError | None = None
                segments = []
                asr_start_time = time.time()
                successful_audio_url: str | None = None

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
                # 原子补记计费并把 claim 收尾为 success（终态幂等标记）。三处计费写入同一事务提交，
                # 关闭「转写后/ASRUsage 前」少计费窗口，并避免重试重复累加配额（详见 _finalize_asr_cost_sync）。
                asr_processing_time_ms = int((time.time() - asr_start_time) * 1000)
                _finalize_asr_cost_sync(
                    session,
                    task,
                    provider_name=provider_name,
                    asr_variant=asr_variant,
                    duration_seconds=float(duration_seconds),
                    asr_service=asr_service,
                    successful_audio_url=successful_audio_url,
                    diarization=diarization,
                    processing_time_ms=asr_processing_time_ms,
                    claim_row=claim_row,
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
                stage_manager.complete_stage(session, StageType.TRANSCRIBE, {"segment_count": len(transcripts)})
                logger.info(
                    "Task %s: Saved %d transcript segments",
                    task_id,
                    len(transcripts),
                    extra={"task_id": task_id, "segment_count": len(transcripts)},
                )

                # ========== 转写润色（固定步骤）==========
                _update_task(session, task, "polishing", 72, "polishing", request_id)
                stage_manager.start_stage(session, StageType.POLISH)

                try:
                    transcript_rows = (
                        session.query(Transcript)
                        .filter(Transcript.task_id == task_id)
                        .order_by(Transcript.sequence)
                        .all()
                    )

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
                    polish_llm = asyncio.run(
                        SmartFactory.get_service(
                            "llm",
                            provider=polish_provider,
                            model_id=polish_model_id,
                            user_id=str(task.user_id),
                        )
                    )

                    polish_results = asyncio.run(polish_transcripts(polish_llm, seg_dicts))

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
                        session.commit()

                    stage_manager.complete_stage(
                        session,
                        StageType.POLISH,
                        {
                            "total_segments": len(seg_dicts),
                            "changed_segments": changed_count,
                        },
                    )
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
                    stage_manager.fail_stage(
                        session,
                        StageType.POLISH,
                        ErrorCode.LLM_SERVICE_FAILED,
                        str(exc),
                    )
                # ========== 润色结束 ==========

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
                        stage_manager.fail_stage(session, StageType.TRANSCRIBE, error.code, str(error))
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
                _update_task(session, task, "summarizing", 82, "summarizing", request_id)
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
                # 从 DB 读取最新的转写内容（可能已被润色修改）
                latest_transcripts = (
                    session.query(Transcript).filter(Transcript.task_id == task_id).order_by(Transcript.sequence).all()
                )
                full_text = "\n".join([t.content for t in latest_transcripts])

                # 提取请求风格并解析为 7 规范风格之一（auto/空→识别，显式→归一）
                options = task.options or {}
                requested_style = options.get("summary_style")
                if not isinstance(requested_style, str):
                    requested_style = ""
                content_style = asyncio.run(
                    resolve_content_style(
                        requested_style=requested_style,
                        transcript=full_text,
                        title=task.title,
                        locale="zh-CN",
                        user_id=str(task.user_id),
                    )
                )
                # 写回 task.options.summary_style + auto_detected 来源标记，供配图/regenerate
                # 复用并供前端仅对 auto 识别结果展示「AI 识别为：X」
                if is_auto_style(requested_style) and content_style != requested_style:
                    task.options = persist_detected_style(
                        task.options, content_style, auto_detected=True
                    )
                    session.commit()
                logger.info(
                    "Task %s: resolved content_style=%s (requested=%r)",
                    task_id,
                    content_style,
                    requested_style or "auto",
                )

                logger.info(
                    "Task %s: Starting LLM summarization with %d characters (style: %s)",
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
                llm_usages: list[LLMUsage] = []
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
                    try:
                        content = asyncio.run(llm_service.summarize(full_text, summary_type, content_style))
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
                    # LLM 偶发把整段散文包进 ```markdown 围栏，落库前在源头剥掉（与前端渲染防御同语义）
                    content = strip_markdown_fence(content)
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

                # ========== 初始化 overview 摘要的配图状态（不生图、不阻塞 completed）==========
                # 渐进式展示：摘要文字此刻已落库；overview 配图改为 images=[{status:"pending"}…]，
                # content 永久保留 {{IMAGE:…}} 占位锚点（不再被 replace_placeholders 覆盖）。
                # 真正生图推迟到 completed 之后的异步段（见文末 _enqueue_summary_images）。
                images_initialized = False
                for summary in summaries:
                    try:
                        if _init_overview_images(summary, content_style):
                            images_initialized = True
                    except Exception:
                        logger.warning(
                            "Task %s: init overview images failed for one summary, suppressed",
                            task_id,
                            exc_info=True,
                        )
                if images_initialized:
                    session.commit()

                stage_manager.complete_stage(session, StageType.SUMMARIZE, {"summary_count": len(summaries)})
                logger.info(
                    "Task %s: All summaries saved to database",
                    task_id,
                    extra={"task_id": task_id, "summary_count": len(summaries)},
                )
        except Exception as exc:
            # 渐进式展示 §C：摘要文字失败不再连带整任务 failed（否则会藏掉已好的转写）。
            # 标记 SUMMARIZE 阶段失败用于诊断/前端摘要区局部报错，但不 _mark_failed、不 return：
            # 任务仍按 completed 收尾，转写正常展示。转写失败才是 task failed（见上方 TRANSCRIBE except）。
            logger.error(
                "Task %s: Summarization failed but keeping task completed (transcript preserved): %s",
                task_id,
                exc,
                exc_info=True,
                extra={"task_id": task_id},
            )
            with get_sync_db_session() as session:
                task = _get_task(session, task_id)
                if task is not None:
                    if isinstance(exc, BusinessError):
                        stage_manager.fail_stage(session, StageType.SUMMARIZE, exc.code, str(exc))
                    else:
                        error = BusinessError(ErrorCode.LLM_SERVICE_FAILED, reason=str(exc))
                        stage_manager.fail_stage(session, StageType.SUMMARIZE, error.code, str(error))

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

        # 渐进式展示 §B：completed 之后异步补 overview 配图（占位符已在 images 标 pending）。
        overview_summaries = (
            session.query(Summary)
            .filter(
                Summary.task_id == task_id,
                Summary.summary_type == "overview",
                Summary.is_active == True,  # noqa: E712
            )
            .all()
        )
        content_style_for_images = normalize_content_style((task.options or {}).get("summary_style"))
        _enqueue_summary_images(
            task_id=task_id,
            user_id=str(task.user_id),
            summaries=overview_summaries,
            content_style=content_style_for_images,
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
    request_id: str | None = None,
) -> None:
    """Celery 任务入口：处理 YouTube 视频

    Args:
        task_id: 任务ID
        request_id: 请求追踪ID
    """
    _process_youtube(task_id, request_id)
