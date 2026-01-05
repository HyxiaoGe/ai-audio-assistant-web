"""重新生成单个摘要任务"""

import asyncio
import json
import logging
import time
from typing import Optional

from sqlalchemy import select

from app.core.config_manager import ConfigManager
from app.core.exceptions import BusinessError
from app.core.registry import ServiceRegistry
from app.core.smart_factory import SmartFactory
from app.i18n.codes import ErrorCode
from app.models.summary import Summary
from app.models.task import Task
from app.models.transcript import Transcript
from worker.celery_app import celery_app
from worker.db import get_sync_db_session
from worker.redis_client import get_sync_redis_client

logger = logging.getLogger("worker.regenerate_summary")


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


def _resolve_llm_selection(
    provider: Optional[str],
    model_id: Optional[str],
    user_id: Optional[str],
) -> tuple[str, str]:
    if provider:
        model_id = model_id or _load_llm_model_id(provider, user_id) or provider
        return provider, model_id
    provider = _select_default_llm_provider()
    model_id = _load_llm_model_id(provider, user_id) or provider
    return provider, model_id


@celery_app.task(bind=True, name="worker.tasks.regenerate_summary")
def regenerate_summary(
    self,
    task_id: str,
    summary_type: str,
    model: Optional[str] = None,
    model_id: Optional[str] = None,
    request_id: Optional[str] = None,
    comparison_id: Optional[str] = None,
) -> None:
    """重新生成指定类型的摘要

    Args:
        task_id: 任务 ID
        summary_type: 摘要类型
        model: 指定的 LLM provider（如 "doubao", "deepseek", "openrouter"），None 表示自动选择
        model_id: 指定的模型ID（如 "openai/gpt-4o"，用于 OpenRouter 等支持多模型的服务）
        request_id: 请求 ID（用于日志追踪）
        comparison_id: 对比 ID（用于多模型对比功能）
    """
    logger.info(
        "[%s] Starting summary regeneration for task %s, type: %s, provider: %s, "
        "model_id: %s, comparison_id: %s",
        request_id,
        task_id,
        summary_type,
        model or "auto",
        model_id or "default",
        comparison_id,
    )

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


def _regenerate_summary(
    task_id: str,
    summary_type: str,
    model: Optional[str],
    model_id: Optional[str],
    request_id: Optional[str],
    comparison_id: Optional[str],
) -> None:

    redis_client = get_sync_redis_client()
    stream_key = f"summary_stream:{task_id}:{summary_type}"

    user_id: Optional[str] = None
    with get_sync_db_session() as session:
        transcript_stmt = (
            select(Transcript).where(Transcript.task_id == task_id).order_by(Transcript.sequence)
        )
        transcript_result = session.execute(transcript_stmt)
        transcripts = transcript_result.scalars().all()

        if not transcripts:
            raise BusinessError(ErrorCode.PARAMETER_ERROR, reason="任务没有转写结果，无法生成摘要")

        user_id = session.execute(
            select(Task.user_id).where(Task.id == task_id)
        ).scalar_one_or_none()

        # 将旧摘要标记为非活跃
        summary_stmt = select(Summary).where(
            Summary.task_id == task_id,
            Summary.summary_type == summary_type,
            Summary.is_active.is_(True),
        )
        summary_result = session.execute(summary_stmt)
        old_summaries = summary_result.scalars().all()

        for old_summary in old_summaries:
            old_summary.is_active = False
        session.commit()

        # 计算新版本号
        max_version = max([s.version for s in old_summaries], default=0)
        new_version = max_version + 1

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
        SmartFactory.get_service(
            "llm", provider=provider, model_id=resolved_model_id, user_id=user_id
        )
    )
    used_provider = provider
    used_model_id = resolved_model_id or (
        llm_service.model_name if hasattr(llm_service, "model_name") else provider
    )
    if model:
        logger.info(
            "[%s] Using user-specified LLM - provider: %s, model_id: %s",
            request_id,
            provider,
            resolved_model_id,
        )
    else:
        logger.info("[%s] Auto-selected LLM model: %s", request_id, used_model_id)

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

    transcript_text = "\n".join(
        [f"[{t.speaker_label or t.speaker_id or 'Unknown'}] {t.content}" for t in transcripts]
    )

    full_content = ""

    try:

        async def _generate():
            nonlocal full_content
            logger.info(f"[{request_id}] Starting stream generation for {stream_key}")
            async for chunk in llm_service.summarize_stream(transcript_text, summary_type):
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

        with get_sync_db_session() as session:
            # 对比模式下，摘要不设为活跃版本
            is_comparison = comparison_id is not None

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
            session.commit()

            summary_id = str(new_summary.id)

            logger.info(
                f"[{request_id}] Created new summary for task {task_id}, "
                f"type: {summary_type}, version: {new_version}, id: {summary_id}"
            )

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
                    },
                },
                ensure_ascii=False,
            ),
        )
        logger.info(f"[{request_id}] Published summary.completed event for summary {summary_id}")

    except Exception as e:
        error_code = getattr(e, "code", "UNKNOWN_ERROR")
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
