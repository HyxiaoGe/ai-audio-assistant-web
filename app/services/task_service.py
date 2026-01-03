from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.models.task import Task
from app.models.user import User
from app.schemas.task import TaskCreateRequest, TaskDetailResponse, TaskListItem


class TaskService:
    @staticmethod
    async def create_task(
        db: AsyncSession, user: User, data: TaskCreateRequest, trace_id: Optional[str]
    ) -> Task:
        if data.source_type not in {"upload", "youtube"}:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="source_type")

        if data.source_type == "upload" and not data.file_key:
            raise BusinessError(ErrorCode.MISSING_REQUIRED_PARAMETER, field="file_key")
        if data.source_type == "youtube":
            if not data.source_url:
                raise BusinessError(ErrorCode.MISSING_REQUIRED_PARAMETER, field="source_url")
            lower_url = data.source_url.lower()
            if not lower_url.startswith("http"):
                raise BusinessError(ErrorCode.INVALID_URL_FORMAT)
            if "youtube.com" not in lower_url and "youtu.be" not in lower_url:
                raise BusinessError(ErrorCode.UNSUPPORTED_YOUTUBE_URL_FORMAT)

        if data.content_hash:
            existing = await db.execute(
                select(Task.id).where(
                    Task.user_id == user.id,
                    Task.content_hash == data.content_hash,
                    Task.deleted_at.is_(None),
                )
            )
            if existing.scalar_one_or_none():
                raise BusinessError(ErrorCode.TASK_ALREADY_EXISTS)

        task = Task(
            user_id=user.id,
            content_hash=data.content_hash,
            title=data.title,
            source_type=data.source_type,
            source_url=data.source_url if data.source_type == "youtube" else None,
            source_key=data.file_key if data.source_type == "upload" else None,
            source_metadata={},
            options=data.options.model_dump(),
            status="queued",
            progress=1,
            stage="queued",
            request_id=trace_id,
            created_at=datetime.now(timezone.utc),
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)

        from worker.celery_app import celery_app

        if data.source_type == "youtube":
            celery_app.send_task(
                "worker.tasks.process_youtube",
                args=[task.id],
                kwargs={"request_id": trace_id},
            )
        else:
            celery_app.send_task(
                "worker.tasks.process_audio",
                args=[task.id],
                kwargs={"request_id": trace_id},
            )
        return task

    @staticmethod
    async def list_tasks(
        db: AsyncSession,
        user: User,
        page: int,
        page_size: int,
        status_filter: str,
    ) -> tuple[list[TaskListItem], int]:
        base_query = select(Task).where(
            Task.user_id == user.id,
            Task.deleted_at.is_(None),
        )
        if status_filter == "processing":
            base_query = base_query.where(
                Task.status.in_(
                    [
                        "pending",
                        "queued",
                        "resolving",
                        "downloading",
                        "downloaded",
                        "transcoding",
                        "uploading",
                        "uploaded",
                        "resolved",
                        "extracting",
                        "asr_submitting",
                        "asr_polling",
                        "transcribing",
                        "summarizing",
                    ]
                )
            )
        elif status_filter != "all":
            base_query = base_query.where(Task.status == status_filter)

        count_query = select(func.count()).select_from(base_query.subquery())
        total = int((await db.execute(count_query)).scalar_one())

        items_query = (
            base_query.order_by(Task.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = (await db.execute(items_query)).scalars().all()
        items = [
            TaskListItem(
                id=row.id,
                title=row.title,
                source_type=row.source_type,
                status=row.status,
                progress=row.progress,
                duration_seconds=row.duration_seconds,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]
        return items, total

    @staticmethod
    async def get_task_detail(
        db: AsyncSession, user: User, task_id: str
    ) -> TaskDetailResponse:
        result = await db.execute(
            select(Task).where(
                Task.id == task_id,
                Task.user_id == user.id,
                Task.deleted_at.is_(None),
            )
        )
        task = result.scalar_one_or_none()
        if task is None:
            raise BusinessError(ErrorCode.TASK_NOT_FOUND)
        return TaskDetailResponse(
            id=task.id,
            title=task.title,
            source_type=task.source_type,
            source_key=task.source_key,
            status=task.status,
            progress=task.progress,
            stage=task.stage,
            duration_seconds=task.duration_seconds,
            language=task.detected_language,
            created_at=task.created_at,
            updated_at=task.updated_at,
            error_message=task.error_message,
        )

    @staticmethod
    async def delete_task(db: AsyncSession, user: User, task_id: str) -> None:
        result = await db.execute(
            select(Task).where(
                Task.id == task_id,
                Task.user_id == user.id,
                Task.deleted_at.is_(None),
            )
        )
        task = result.scalar_one_or_none()
        if task is None:
            raise BusinessError(ErrorCode.TASK_NOT_FOUND)
        task.deleted_at = datetime.now(timezone.utc)
        await db.commit()
