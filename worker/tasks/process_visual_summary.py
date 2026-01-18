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
):
    """可视化摘要生成任务

    Args:
        task_id: 任务 ID
        visual_type: 可视化类型 (mindmap/timeline/flowchart)
        content_style: 内容风格 (meeting/lecture/podcast/video/general)
        provider: LLM provider（可选）
        model_id: LLM model ID（可选）
        generate_image: 是否生成图片
        image_format: 图片格式 (png/svg)
        user_id: 用户 ID
        request_id: 请求追踪 ID
    """
    logger.info(
        f"[{request_id}] Starting visual summary generation - "
        f"task_id: {task_id}, type: {visual_type}, style: {content_style}"
    )

    async def _process():
        from sqlalchemy import select

        from app.db import async_session_factory
        from app.models.transcript import Transcript
        from worker.tasks.summary_visual_generator import generate_visual_summary

        async with async_session_factory() as session:
            try:
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

                # ===== Step 3: 提交数据库事务 =====
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

            except Exception as e:
                logger.error(
                    f"[{request_id}] Failed to generate visual summary: {e}",
                    exc_info=True,
                )
                await session.rollback()
                raise

    try:
        result = asyncio.run(_process())
        logger.info(f"[{request_id}] Visual summary task completed: {result['summary_id']}")
        return result

    except Exception as e:
        logger.error(
            f"[{request_id}] Visual summary task failed for task {task_id}: {e}",
            exc_info=True,
        )
        # Re-raise for Celery retry mechanism
        raise self.retry(exc=e, countdown=60, max_retries=2)
