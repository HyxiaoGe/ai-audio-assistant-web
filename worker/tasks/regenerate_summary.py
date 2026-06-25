"""重新生成单个摘要任务"""

import asyncio
import json
import logging
import time
from uuid import uuid4

from redis import Redis
from sqlalchemy import select

from app.config import settings
from app.core.config_manager import ConfigManager
from app.core.exceptions import BusinessError
from app.core.registry import ServiceRegistry
from app.core.smart_factory import SmartFactory
from app.i18n.codes import ErrorCode
from app.models.llm_usage import LLMUsage
from app.models.summary import Summary
from app.models.task import Task
from app.models.transcript import Transcript
from app.services.summary.markdown_fence import strip_markdown_fence
from app.services.summary.preamble import strip_summary_preamble
from worker.celery_app import celery_app
from worker.db import get_sync_db_session
from worker.redis_client import get_sync_redis_client
from worker.tasks.image_generator import (
    apply_image_result_to_summary,
    build_image_specs,
    extract_image_placeholders,
    generate_images_parallel,
    get_auto_images_config,
    is_auto_images_enabled,
    publish_image_ready_global,
)

logger = logging.getLogger("worker.regenerate_summary")

REGEN_LOCK_TTL = 600  # 秒；仅作 worker 被 SIGKILL 时的兜底自动解锁(一次重生最重 dev 实测 ~133s)


def build_regen_lock_key(task_id: str, summary_type: str) -> str:
    """单条重生(非对比)的并发去重锁 key。端点预检与 worker 持锁共用此唯一真源。"""
    return f"summary:regen:lock:{task_id}:{summary_type}"


def _release_regen_lock(redis_client: Redis, lock_key: str, lock_token: str | None) -> None:
    """释放重生锁:仅当持有者 token 匹配才删,避免误删 TTL 过期后他人重新获取的锁;失败靠 TTL 兜底。"""
    try:
        if lock_token is not None and redis_client.get(lock_key) == lock_token:
            redis_client.delete(lock_key)
    except Exception:
        logger.warning("Failed to release regen lock %s, will expire via TTL", lock_key, exc_info=True)


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


def _resolve_llm_selection(
    provider: str | None,
    model_id: str | None,
    user_id: str | None,
) -> tuple[str, str]:
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


@celery_app.task(
    bind=True,
    name="worker.tasks.regenerate_summary",
    # per-task 超时兜底:重生最坏 = 摘要流式 + 内联最多 6 张配图(≈1200s)。
    # soft 1500 先抛 SoftTimeLimitExceeded → 经 finally 释放重生锁;time 1700 硬杀作 backstop。
    # 原无超时:卡死的重生会干等全局 4200s 白占 worker 槽。celery 硬超时参数名是 time_limit。
    soft_time_limit=1500,
    time_limit=1700,
)
def regenerate_summary(
    self,
    task_id: str,
    summary_type: str,
    model: str | None = None,
    model_id: str | None = None,
    request_id: str | None = None,
    comparison_id: str | None = None,
) -> None:
    """重新生成指定类型的摘要

    Args:
        task_id: 任务 ID
        summary_type: 摘要类型
        model: 指定的 LLM provider（如 "proxy"），None 表示走默认 LiteLLM 别名
        model_id: 指定的模型 ID / LiteLLM 业务别名（如 "chat-default"、"chat-premium"）
        request_id: 请求 ID（用于日志追踪）
        comparison_id: 对比 ID（用于多模型对比功能）
    """
    logger.info(
        "[%s] Starting summary regeneration for task %s, type: %s, provider: %s, model_id: %s, comparison_id: %s",
        request_id,
        task_id,
        summary_type,
        model or "auto",
        model_id or "default",
        comparison_id,
    )

    # 并发去重锁:仅对单条重生(非对比)加锁;compare(comparison_id 非空)按设计并发,豁免。
    # worker 侧原子锁是唯一正确性来源(端点预检仅快反馈);锁存活==任务存活,丢投递不留僵尸锁。
    redis_client: Redis | None = None
    lock_key: str | None = None
    lock_token: str | None = None
    if comparison_id is None:
        redis_client = get_sync_redis_client()
        lock_key = build_regen_lock_key(task_id, summary_type)
        lock_token = (self.request.id if self.request else None) or uuid4().hex
        if not redis_client.set(lock_key, lock_token, nx=True, ex=REGEN_LOCK_TTL):
            # SETNX 失败:若锁值==自己的 token,说明是「本任务上一条因 OOM 单子进程崩溃留下的
            # 陈旧自锁」(重投复用同 task id → 同 lock_token),应接管续跑而非把自己挡在外面;
            # 续租 TTL 重新武装兜底。否则确是别人在跑,维持原行为跳过。
            if redis_client.get(lock_key) == lock_token:
                redis_client.set(lock_key, lock_token, ex=REGEN_LOCK_TTL)
                logger.warning(
                    "[%s] Regen lock taken over (own stale lock): %s/%s",
                    request_id,
                    task_id,
                    summary_type,
                )
            else:
                logger.warning(
                    "[%s] Summary regeneration skipped: %s/%s already in progress",
                    request_id,
                    task_id,
                    summary_type,
                )
                return

    try:
        _regenerate_summary(task_id, summary_type, model, model_id, request_id, comparison_id)
        logger.info(
            "[%s] Summary regeneration completed for task %s, type: %s",
            request_id,
            task_id,
            summary_type,
        )
    except BusinessError as exc:
        logger.error(f"[{request_id}] Summary regeneration failed: {str(exc)} (code: {exc.code})")
        raise
    except Exception:
        logger.exception(f"[{request_id}] Unexpected error during summary regeneration")
        raise
    finally:
        if redis_client is not None and lock_key is not None:
            _release_regen_lock(redis_client, lock_key, lock_token)


async def _process_regenerated_images(
    *,
    task_id: str,
    user_id: str,
    summary_id: str,
    content: str,
    content_style: str | None,
    request_id: str | None = None,
) -> None:
    """regenerate overview 配图：写 summary.images（pending→ready/failed）+ 发全局 WS image_ready。

    与首跑(process_youtube)对齐：content 永久保留 {{IMAGE:…}} 占位锚点，绝不覆盖；
    除既有 SSE summary_stream(本函数不改)外，每张完成额外上全局 WS user:{uid}:updates。
    """
    new_content, specs = build_image_specs(content, content_style)
    if not specs:
        return
    # 初始化 images=pending（若插入了默认占位符，同时把 content 写回 DB 以保留锚点）
    with get_sync_db_session() as session:
        summary = session.query(Summary).filter(Summary.id == summary_id).first()
        if summary is None:
            return
        if new_content != content:
            summary.content = new_content
        summary.images = specs
        session.commit()

    placeholders = extract_image_placeholders(new_content)
    config = get_auto_images_config()
    max_images = config.get("max_images", 3)
    timeout = config.get("timeout_seconds", 60)

    def on_image_ready(result: dict, current: int, total: int) -> None:
        updated = None
        try:
            with get_sync_db_session() as session:
                updated = apply_image_result_to_summary(session, summary_id, result)
        except Exception:
            logger.warning(
                "[%s] [regen] Task %s: persist image result failed, suppressed",
                request_id,
                task_id,
                exc_info=True,
            )
        status = (updated or {}).get("status") or ("ready" if result.get("status") == "success" else "failed")
        publish_image_ready_global(
            user_id=user_id,
            task_id=task_id,
            summary_id=summary_id,
            placeholder=result.get("placeholder", ""),
            status=str(status),
            url=result.get("url") if status == "ready" else None,
            model_id=result.get("model_id"),
        )

    await generate_images_parallel(
        placeholders,
        user_id,
        task_id,
        content_style=content_style or "general",
        locale="zh-CN",
        max_images=max_images,
        timeout=timeout,
        on_image_ready=on_image_ready,
    )


def _regenerate_summary(
    task_id: str,
    summary_type: str,
    model: str | None,
    model_id: str | None,
    request_id: str | None,
    comparison_id: str | None,
) -> None:

    redis_client = get_sync_redis_client()
    stream_key = f"summary_stream:{task_id}:{summary_type}"

    user_id: str | None = None
    content_style: str = "meeting"  # 默认风格
    with get_sync_db_session() as session:
        transcript_stmt = select(Transcript).where(Transcript.task_id == task_id).order_by(Transcript.sequence)
        transcript_result = session.execute(transcript_stmt)
        transcripts = transcript_result.scalars().all()

        if not transcripts:
            raise BusinessError(ErrorCode.PARAMETER_ERROR, reason="任务没有转写结果，无法生成摘要")

        # 获取 user_id 和 content_style
        task_result = session.execute(select(Task.user_id, Task.options).where(Task.id == task_id)).first()
        if task_result:
            user_id = task_result[0]
            options = task_result[1] or {}
            content_style = options.get("summary_style") or "meeting"

        # 计算新版本号（覆盖全部历史版本，避免与对比版本号冲突）
        # 注意：此处不停用旧摘要——停用推迟到新摘要成功写入时的同一事务中完成，
        # 确保生成失败时用户仍有可用的活跃摘要（修复 regenerate 失败/对比导致无活跃摘要的问题）
        version_stmt = select(Summary.version).where(
            Summary.task_id == task_id,
            Summary.summary_type == summary_type,
        )
        existing_versions = session.execute(version_stmt).scalars().all()
        new_version = max(existing_versions, default=0) + 1

    max_wait = 50
    wait_count = 0
    subscriber_count = 0
    while wait_count < max_wait:
        subscriber_count = redis_client.publish(
            stream_key,
            json.dumps({"type": "ping"}, ensure_ascii=False),
        )
        if subscriber_count > 0:
            break
        time.sleep(0.1)
        wait_count += 1
    # 使用 SmartFactory 获取 LLM 服务
    provider, resolved_model_id = _resolve_llm_selection(model, model_id, user_id)
    llm_service = asyncio.run(
        SmartFactory.get_service("llm", provider=provider, model_id=resolved_model_id, user_id=user_id)
    )
    used_provider = provider
    used_model_id = resolved_model_id or (llm_service.model_name if hasattr(llm_service, "model_name") else provider)
    with get_sync_db_session() as session:
        task = session.query(Task).filter(Task.id == task_id).first()
        if task is not None and used_provider:
            task.llm_provider = used_provider
            session.commit()
    if model:
        logger.info(
            "[%s] Using user-specified LLM - provider: %s, model_id: %s",
            request_id,
            provider,
            resolved_model_id,
        )
    else:
        logger.info("[%s] Auto-selected LLM model: %s", request_id, used_model_id)

    logger.info("[%s] Using content_style: %s", request_id, content_style)

    redis_client.publish(
        stream_key,
        json.dumps(
            {
                "event": "summary.started",
                "data": {
                    "type": "summary.started",
                    "task_id": task_id,
                    "summary_type": summary_type,
                    "version": new_version,
                    "provider": used_provider,
                    "model_id": used_model_id,
                },
            },
            ensure_ascii=False,
        ),
    )

    transcript_text = "\n".join([f"[{t.speaker_label or t.speaker_id or 'Unknown'}] {t.content}" for t in transcripts])

    full_content = ""

    try:

        async def _generate():
            nonlocal full_content
            logger.info(f"[{request_id}] Starting stream generation for {stream_key} (style: {content_style})")
            async for chunk in llm_service.summarize_stream(transcript_text, summary_type, content_style):
                full_content += chunk
                redis_client.publish(
                    stream_key,
                    json.dumps(
                        {
                            "event": "summary.delta",
                            "data": {
                                "type": "summary.delta",
                                "content": chunk,
                                "provider": used_provider,
                                "model_id": used_model_id,
                            },
                        },
                        ensure_ascii=False,
                    ),
                )

            logger.info(f"[{request_id}] Stream generation completed for {stream_key}")

        asyncio.run(_generate())

        # LLM 偶发把整段散文包进 ```markdown 围栏，落库前在源头剥掉（与前端渲染防御同语义）
        # 再剥掉偶发逸出的客套/元描述开场白（先剥围栏再剥开场白）
        full_content = strip_summary_preamble(strip_markdown_fence(full_content))

        with get_sync_db_session() as session:
            # 对比模式下，摘要不设为活跃版本
            is_comparison = comparison_id is not None

            # 非对比模式：在写入新活跃摘要的同一事务中停用旧摘要，确保任意时刻都有活跃摘要；
            # 对比模式不停用旧摘要（对比结果默认不活跃，需用户显式 activate）
            if not is_comparison:
                old_active_stmt = select(Summary).where(
                    Summary.task_id == task_id,
                    Summary.summary_type == summary_type,
                    Summary.is_active.is_(True),
                )
                for old_summary in session.execute(old_active_stmt).scalars().all():
                    old_summary.is_active = False

            new_summary = Summary(
                task_id=task_id,
                summary_type=summary_type,
                version=new_version,
                is_active=not is_comparison,  # 对比版本不设为活跃
                content=full_content,
                model_used=llm_service.model_name if hasattr(llm_service, "model_name") else model,
                prompt_version="v1.0",
                token_count=len(full_content),
                comparison_id=comparison_id,  # 记录对比 ID
            )
            session.add(new_summary)
            if used_provider:
                session.add(
                    LLMUsage(
                        user_id=user_id,
                        task_id=task_id,
                        provider=used_provider,
                        model_id=used_model_id,
                        call_type="summarize",
                        summary_type=summary_type,
                        status="success",
                    )
                )
            session.commit()

            summary_id = str(new_summary.id)

            logger.info(
                f"[{request_id}] Created new summary for task {task_id}, "
                f"type: {summary_type}, version: {new_version}, id: {summary_id}"
            )

        # 检查是否有图片占位符
        placeholders = extract_image_placeholders(full_content)
        has_images = len(placeholders) > 0
        auto_images_enabled = is_auto_images_enabled(summary_type, content_style)

        redis_client.publish(
            stream_key,
            json.dumps(
                {
                    "event": "summary.completed",
                    "data": {
                        "type": "summary.completed",
                        "task_id": task_id,
                        "summary_id": summary_id,
                        "summary_type": summary_type,
                        "version": new_version,
                        "total_length": len(full_content),
                        "provider": used_provider,
                        "model_id": used_model_id,
                        "has_images": has_images or auto_images_enabled,
                        "image_count": len(placeholders),
                    },
                },
                ensure_ascii=False,
            ),
        )
        logger.info(f"[{request_id}] Published summary.completed event for summary {summary_id}")

        # 处理 overview 配图：写 summary.images（pending→ready/failed）+ 全局 WS image_ready。
        # content 永久保留 {{IMAGE:…}} 占位符（不再被 process_summary_images 覆盖）。
        if auto_images_enabled:
            logger.info(
                f"[{request_id}] Processing overview images for task {task_id}: placeholders={len(placeholders)}"
            )
            try:
                asyncio.run(
                    _process_regenerated_images(
                        task_id=task_id,
                        user_id=user_id or "",
                        summary_id=summary_id,
                        content=full_content,
                        content_style=content_style,
                        request_id=request_id,
                    )
                )
            except Exception as img_err:
                # 图片生成失败不影响摘要，只记录日志（单图失败已写入 images[i].status="failed"）。
                logger.warning(f"[{request_id}] Image processing failed for task {task_id}: {img_err}")

    except Exception as e:
        error_code = getattr(e, "code", "UNKNOWN_ERROR")
        if used_provider:
            with get_sync_db_session() as session:
                session.add(
                    LLMUsage(
                        user_id=user_id,
                        task_id=task_id,
                        provider=used_provider,
                        model_id=used_model_id,
                        call_type="summarize",
                        summary_type=summary_type,
                        status="failed",
                    )
                )
                session.commit()
        redis_client.publish(
            stream_key,
            json.dumps(
                {
                    "event": "error",
                    "data": {
                        "type": "error",
                        "code": str(error_code),
                        "message": str(e),
                        "provider": used_provider,
                        "model_id": used_model_id,
                    },
                },
                ensure_ascii=False,
            ),
        )
        raise
    finally:
        redis_client.expire(stream_key, 300)
