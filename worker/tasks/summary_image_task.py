"""渐进式展示：completed 之后的 overview 异步配图段。

在主任务标 completed 之后由 process_youtube / regenerate 触发：复用 generate_images_parallel
的逐张回调，每张完成 -> 回写 Summary.images 对应项(ready/failed) -> 发 image_ready 到全局 WS
user:{uid}:updates。单图失败只写 images[i].status="failed"，绝不影响已 completed 的 task/摘要。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from worker.celery_app import celery_app
from worker.db import get_sync_db_session
from worker.tasks.image_generator import (
    apply_image_result_to_summary,
    extract_image_placeholders,
    generate_images_parallel,
    get_auto_images_config,
    publish_image_ready_global,
)

logger = logging.getLogger("worker.summary_image_task")


async def _run_summary_images(
    *,
    task_id: str,
    user_id: str,
    summary_id: str,
    content: str,
    content_style: str | None,
) -> None:
    """异步生成 overview 配图，逐张回写 Summary.images + 发全局 WS image_ready。"""
    placeholders = extract_image_placeholders(content)
    if not placeholders:
        logger.info("Task %s: no image placeholders for async images; nothing to do", task_id)
        return

    config = get_auto_images_config()
    max_images = config.get("max_images", 3)
    timeout = config.get("timeout_seconds", 60)

    def on_image_ready(result: dict[str, Any], current: int, total: int) -> None:
        # 1) 回写 images[i]（独立 session，best-effort）
        updated: dict[str, object] | None = None
        try:
            with get_sync_db_session() as session:
                updated = apply_image_result_to_summary(session, summary_id, result)
        except Exception:
            logger.warning("Task %s: persist image result failed, suppressed", task_id, exc_info=True)
        # 2) 发全局 WS image_ready（即便回写失败也按结果广播，前端就地替换）
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


@celery_app.task(
    name="worker.tasks.generate_summary_images_async",
    bind=True,
    max_retries=0,
    soft_time_limit=900,
    hard_time_limit=1000,
)
def generate_summary_images_async(
    self,
    task_id: str,
    user_id: str,
    summary_id: str,
    content: str,
    content_style: str | None = None,
) -> None:
    """Celery 入口：completed 之后异步生成 overview 配图。"""
    asyncio.run(
        _run_summary_images(
            task_id=task_id,
            user_id=user_id,
            summary_id=summary_id,
            content=content,
            content_style=content_style,
        )
    )
