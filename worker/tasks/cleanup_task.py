from __future__ import annotations

import asyncio
import logging
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.smart_factory import SmartFactory
from app.models.llm_usage import LLMUsage
from app.models.notification import Notification
from app.models.rag_chunk import RagChunk
from app.models.summary import Summary
from app.models.task import Task
from app.models.task_stage import TaskStage
from app.models.transcript import Transcript
from app.services.storage.base import StorageService
from worker.celery_app import celery_app
from worker.db import get_sync_db_session

logger = logging.getLogger("worker.cleanup_task")


def _load_task(session: Session, task_id: str, user_id: str) -> Optional[Task]:
    result = session.execute(select(Task).where(Task.id == task_id, Task.user_id == user_id))
    return result.scalar_one_or_none()


def _delete_storage_object(provider: Optional[str], source_key: str, user_id: str) -> None:
    try:
        storage: StorageService = asyncio.run(
            SmartFactory.get_service("storage", provider=provider, user_id=user_id)
        )
    except Exception as exc:
        logger.warning(
            "Cleanup storage init failed for provider=%s: %s",
            provider or "default",
            exc,
            exc_info=True,
        )
        return

    try:
        storage.delete_file(source_key)
        logger.info(
            "Cleanup storage deleted file provider=%s key=%s",
            storage.provider,
            source_key,
        )
    except Exception as exc:
        logger.warning(
            "Cleanup storage delete failed provider=%s key=%s: %s",
            storage.provider,
            source_key,
            exc,
            exc_info=True,
        )


@celery_app.task(
    name="worker.tasks.cleanup_task_data",
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def cleanup_task_data(self, task_id: str, user_id: str) -> None:
    logger.info("Cleanup task started: task_id=%s user_id=%s", task_id, user_id)

    with get_sync_db_session() as session:
        task = _load_task(session, task_id, user_id)
        if task is None:
            logger.warning("Cleanup task skipped: task not found: %s", task_id)
            return
        if task.deleted_at is None:
            logger.info("Cleanup task skipped: task not deleted: %s", task_id)
            return
        source_key = task.source_key

    if source_key:
        _delete_storage_object(None, source_key, user_id)
        _delete_storage_object("minio", source_key, user_id)

    with get_sync_db_session() as session:
        session.execute(delete(Transcript).where(Transcript.task_id == task_id))
        session.execute(delete(Summary).where(Summary.task_id == task_id))
        session.execute(delete(TaskStage).where(TaskStage.task_id == task_id))
        session.execute(delete(RagChunk).where(RagChunk.task_id == task_id))
        session.execute(delete(LLMUsage).where(LLMUsage.task_id == task_id))
        session.execute(delete(Notification).where(Notification.task_id == task_id))
        session.commit()

    logger.info("Cleanup task finished: task_id=%s", task_id)
