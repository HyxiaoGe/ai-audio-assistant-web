"""处理可视化摘要生成的 Celery 任务"""

import asyncio
import logging

from worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="worker.tasks.process_visual_summary", bind=True)
def process_visual_summary(
    self,
    task_id: str,
    visual_type: str,
    content_style: str,
    provider: str | None = None,
    model_id: str | None = None,
    generate_image: bool = True,
    image_format: str = "png",
    user_id: str | None = None,
    request_id: str | None = None,
    regenerate: bool = False,
):
    """可视化摘要生成任务

    Args:
        task_id: 任务 ID
        visual_type: 可视化类型 (mindmap/timeline/flowchart/outline)
        content_style: 内容风格 (meeting/lecture/podcast/video/general)
        provider: LLM provider（可选，outline 类型默认 openrouter）
        model_id: LLM model ID（可选，outline 类型默认 google/gemini-3-pro-image-preview）
        generate_image: 是否生成图片
        image_format: 图片格式 (png/svg)
        user_id: 用户 ID
        request_id: 请求追踪 ID
        regenerate: 是否强制重新生成
    """
    logger.info(
        f"[{request_id}] Starting visual summary generation - "
        f"task_id: {task_id}, type: {visual_type}, style: {content_style}, "
        f"regenerate: {regenerate}"
    )
    logger.info(
        f"[{request_id}] Parameters: provider={provider} (type: {type(provider).__name__}), "
        f"model_id={model_id} (type: {type(model_id).__name__}), "
        f"generate_image={generate_image} (type: {type(generate_image).__name__}), "
        f"image_format={image_format}"
    )

    async def _process():
        import redis.asyncio as aioredis
        from sqlalchemy import select, update

        from app.config import settings
        from app.db import async_session_factory
        from app.models.summary import Summary
        from app.models.transcript import Transcript
        from worker.tasks.summary_visual_generator import generate_visual_summary

        # ===== Step 0: 使用 Redis 锁防止并发重复创建 =====
        summary_type = f"visual_{visual_type}"
        lock_key = f"visual_summary_lock:{task_id}:{visual_type}"

        redis_client = aioredis.from_url(settings.REDIS_URL)
        try:
            # 尝试获取锁，超时 120 秒（LLM 生成可能较慢）
            lock_acquired = await redis_client.set(lock_key, "1", nx=True, ex=120)
            if not lock_acquired:
                logger.info(
                    f"[{request_id}] Another worker is generating visual summary for "
                    f"task {task_id}, type: {visual_type}, skipping"
                )
                return {
                    "task_id": task_id,
                    "visual_type": visual_type,
                    "status": "skipped",
                    "reason": "generation_in_progress",
                }

            async with async_session_factory() as session:
                # 检查是否已存在（如果不是强制重新生成）
                if not regenerate:
                    existing_stmt = (
                        select(Summary)
                        .where(
                            Summary.task_id == task_id,
                            Summary.summary_type == summary_type,
                            Summary.is_active.is_(True),
                        )
                        .limit(1)
                    )
                    existing_result = await session.execute(existing_stmt)
                    existing_summary = existing_result.scalar_one_or_none()

                    if existing_summary:
                        logger.info(
                            f"[{request_id}] Visual summary already exists for task {task_id}, "
                            f"type: {visual_type}, skipping generation"
                        )
                        return {
                            "task_id": task_id,
                            "summary_id": str(existing_summary.id),
                            "visual_type": visual_type,
                            "status": "skipped",
                            "reason": "already_exists",
                        }

                # ===== Step 1: 获取转写片段 =====
                logger.info(f"[{request_id}] Fetching transcripts for task {task_id}")

                transcript_stmt = (
                    select(Transcript)
                    .where(Transcript.task_id == task_id)
                    .order_by(Transcript.sequence)
                )
                transcript_result = await session.execute(transcript_stmt)
                transcripts = transcript_result.scalars().all()

                if not transcripts:
                    logger.error(f"[{request_id}] No transcripts found for task {task_id}")
                    raise ValueError(f"Task {task_id} has no transcripts")

                logger.info(f"[{request_id}] Found {len(transcripts)} transcript segments")

                # 转换为 TranscriptSegment 对象
                from app.services.asr.base import TranscriptSegment

                segments = [
                    TranscriptSegment(
                        start_time=t.start_time,
                        end_time=t.end_time,
                        content=t.content,
                        speaker_id=t.speaker_id,
                        confidence=t.confidence,
                    )
                    for t in transcripts
                ]

                # ===== Step 2: 生成可视化摘要 =====
                logger.info(f"[{request_id}] Generating {visual_type} visual summary")

                if visual_type == "outline":
                    # outline 类型使用 AI 图像生成
                    from worker.tasks.outline_generator import generate_outline_summary

                    summary = await generate_outline_summary(
                        task_id=task_id,
                        segments=segments,
                        content_style=content_style,
                        session=session,
                        user_id=user_id,
                        provider=provider,
                        model_id=model_id,
                        image_format=image_format,
                    )
                else:
                    # 其他类型使用 Mermaid 生成
                    summary = await generate_visual_summary(
                        task_id=task_id,
                        segments=segments,
                        visual_type=visual_type,
                        content_style=content_style,
                        session=session,
                        user_id=user_id,
                        provider=provider,
                        model_id=model_id,
                        generate_image=generate_image,
                        image_format=image_format,
                    )

                # ===== Step 3: Flush 获取生成的 ID =====
                # summary.id 在 flush 之前是 None（服务器端 UUID 生成）
                await session.flush()

                # ===== Step 4: 如果是重新生成，deactivate 旧记录 =====
                if regenerate:
                    logger.info(
                        f"[{request_id}] Deactivating old summaries, new summary.id: {summary.id}"
                    )
                    deactivate_stmt = (
                        update(Summary)
                        .where(Summary.task_id == task_id)
                        .where(Summary.summary_type == summary_type)
                        .where(Summary.id != summary.id)
                        .where(Summary.is_active.is_(True))
                        .values(is_active=False)
                    )
                    await session.execute(deactivate_stmt)
                    logger.info(
                        f"[{request_id}] Deactivated old visual summaries for task {task_id}"
                    )

                # ===== Step 5: 提交数据库事务 =====
                await session.commit()

                logger.info(
                    f"[{request_id}] Visual summary generated successfully - "
                    f"summary_id: {summary.id}, has_image: {summary.image_key is not None}"
                )

                return {
                    "task_id": task_id,
                    "summary_id": str(summary.id),
                    "visual_type": visual_type,
                    "has_image": summary.image_key is not None,
                    "image_key": summary.image_key,
                    "status": "completed",
                }

        finally:
            # 释放锁并关闭 Redis 连接
            await redis_client.delete(lock_key)
            await redis_client.aclose()

    try:
        result = asyncio.run(_process())
        if result.get("status") == "completed":
            logger.info(f"[{request_id}] Visual summary task completed: {result.get('summary_id')}")
        else:
            logger.info(f"[{request_id}] Visual summary task result: {result}")
        return result

    except Exception as e:
        logger.error(
            f"[{request_id}] Visual summary task failed for task {task_id}: {e}",
            exc_info=True,
        )
        # Re-raise for Celery retry mechanism
        raise self.retry(exc=e, countdown=60, max_retries=2)
