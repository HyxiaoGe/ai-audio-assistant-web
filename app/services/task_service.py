from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from yt_dlp import YoutubeDL

from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.models.task import Task
from app.models.user import User
from app.schemas.task import TaskCreateRequest, TaskDetailResponse, TaskListItem

logger = logging.getLogger(__name__)


class TaskService:
    @staticmethod
    def _extract_youtube_video_id(url: str) -> Optional[str]:
        """从 YouTube URL 中提取视频ID.

        支持的格式：
        - https://www.youtube.com/watch?v=VIDEO_ID
        - https://youtu.be/VIDEO_ID
        - https://www.youtube.com/embed/VIDEO_ID
        """
        patterns = [
            r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def _generate_content_hash(content: str) -> str:
        """生成内容的 SHA256 哈希值."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_youtube_video_sync(url: str) -> Optional[str]:
        """同步验证 YouTube 视频是否可访问（用于在异步上下文中通过 asyncio.to_thread 调用）.

        Args:
            url: YouTube 视频 URL

        Returns:
            视频标题（如果验证成功）

        Raises:
            BusinessError: 如果视频不可访问或验证失败
        """
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "socket_timeout": 15,  # 15 秒超时
        }

        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    raise BusinessError(ErrorCode.YOUTUBE_VIDEO_UNAVAILABLE)

                # 返回视频标题（可选）
                title = info.get("title") if isinstance(info, dict) else None
                return title

        except Exception as exc:
            error_msg = str(exc).lower()
            logger.warning(f"YouTube video validation failed: {exc}")

            # 判断是否为视频不可访问
            if any(
                keyword in error_msg
                for keyword in [
                    "video unavailable",
                    "private video",
                    "video has been removed",
                    "this video isn't available",
                    "video is unavailable",
                    "has been removed",
                    "is not available",
                    "members-only",
                    "premiere",
                ]
            ):
                raise BusinessError(ErrorCode.YOUTUBE_VIDEO_UNAVAILABLE)

            # 视频 ID 不完整或格式错误
            if any(
                keyword in error_msg
                for keyword in [
                    "incomplete youtube id",
                    "truncated",
                    "invalid youtube id",
                    "malformed",
                ]
            ):
                raise BusinessError(ErrorCode.INVALID_URL_FORMAT)

            # 网络超时
            if "timeout" in error_msg or "timed out" in error_msg:
                raise BusinessError(
                    ErrorCode.YOUTUBE_DOWNLOAD_FAILED, reason="网络超时，请稍后重试"
                )

            # 地域限制
            if any(
                keyword in error_msg
                for keyword in [
                    "not available in your country",
                    "geo-restricted",
                    "region",
                ]
            ):
                raise BusinessError(
                    ErrorCode.YOUTUBE_DOWNLOAD_FAILED, reason="该视频存在地域限制，当前地区无法访问"
                )

            # 需要登录
            if any(
                keyword in error_msg
                for keyword in [
                    "sign in",
                    "login",
                    "members only",
                ]
            ):
                raise BusinessError(ErrorCode.YOUTUBE_VIDEO_UNAVAILABLE)

            # 其他下载错误 - 只返回简洁的错误信息
            # 提取错误的关键部分，避免暴露技术细节
            if "error:" in error_msg:
                # 尝试提取 yt-dlp 错误信息中的关键部分
                parts = str(exc).split("ERROR:")
                if len(parts) > 1:
                    # 取第一个 ERROR 后面的内容，限制长度
                    clean_msg = parts[1].strip().split("\n")[0][:100]
                    # 移除技术前缀如 [youtube:truncated_id]
                    clean_msg = re.sub(r"\[[\w:]+\]\s+\w+:\s+", "", clean_msg)
                    raise BusinessError(
                        ErrorCode.YOUTUBE_DOWNLOAD_FAILED, reason=f"视频解析失败：{clean_msg}"
                    )

            # 完全未知的错误，返回通用提示
            raise BusinessError(
                ErrorCode.YOUTUBE_DOWNLOAD_FAILED,
                reason="视频链接无效或暂时无法访问，请检查链接是否正确",
            )

    @staticmethod
    async def _validate_youtube_video(url: str) -> Optional[str]:
        """异步验证 YouTube 视频是否可访问.

        Args:
            url: YouTube 视频 URL

        Returns:
            视频标题（如果验证成功）

        Raises:
            BusinessError: 如果视频不可访问或验证失败
        """
        try:
            # 在线程池中运行同步的 yt-dlp 调用，设置 20 秒总超时
            title = await asyncio.wait_for(
                asyncio.to_thread(TaskService._validate_youtube_video_sync, url), timeout=20.0
            )
            return title
        except asyncio.TimeoutError:
            logger.warning(f"YouTube video validation timeout: {url}")
            raise BusinessError(
                ErrorCode.YOUTUBE_DOWNLOAD_FAILED, reason="视频验证超时，请检查网络连接或稍后重试"
            )

    @staticmethod
    async def create_task(
        db: AsyncSession, user: User, data: TaskCreateRequest, trace_id: Optional[str]
    ) -> Task:
        if data.source_type not in {"upload", "youtube"}:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="source_type")

        if data.source_type == "upload" and not data.file_key:
            raise BusinessError(ErrorCode.MISSING_REQUIRED_PARAMETER, field="file_key")

        # YouTube/Bilibili 任务：验证 URL 并自动生成 content_hash
        if data.source_type == "youtube":
            if not data.source_url:
                raise BusinessError(ErrorCode.MISSING_REQUIRED_PARAMETER, field="source_url")
            lower_url = data.source_url.lower()
            if not lower_url.startswith("http"):
                raise BusinessError(ErrorCode.INVALID_URL_FORMAT)

            # 支持 YouTube 和 Bilibili
            is_youtube = "youtube.com" in lower_url or "youtu.be" in lower_url
            is_bilibili = "bilibili.com" in lower_url or "b23.tv" in lower_url

            if not is_youtube and not is_bilibili:
                raise BusinessError(ErrorCode.UNSUPPORTED_YOUTUBE_URL_FORMAT)

            # 预检查：验证视频是否可访问（避免创建注定失败的任务）
            logger.info(f"Pre-validating YouTube video: {data.source_url}")
            video_title = await TaskService._validate_youtube_video(data.source_url)

            # 如果用户没有提供标题，使用视频标题
            if video_title and not data.title:
                data.title = video_title
                logger.info(f"Auto-filled task title from video: {video_title}")

            # 自动生成 content_hash（基于 YouTube 视频ID）
            video_id = TaskService._extract_youtube_video_id(data.source_url)
            if video_id:
                data.content_hash = TaskService._generate_content_hash(f"youtube:{video_id}")

        # 检查是否有相同内容的任务
        if data.content_hash:
            existing_result = await db.execute(
                select(Task)
                .where(
                    Task.user_id == user.id,
                    Task.content_hash == data.content_hash,
                    Task.deleted_at.is_(None),
                )
                .order_by(Task.created_at.desc())  # 获取最新的任务
            )
            existing_task = existing_result.scalar_one_or_none()

            if existing_task:
                # 已有成功的任务，直接拒绝
                if existing_task.status == "completed":
                    raise BusinessError(ErrorCode.TASK_ALREADY_EXISTS)

                # 正在处理中的任务，提示用户
                processing_statuses = {
                    "pending",
                    "queued",
                    "resolving",
                    "downloading",
                    "downloaded",
                    "transcoding",
                    "uploading",
                    "uploaded",
                    "resolved",
                    "processing",
                    "asr_submitting",
                    "asr_polling",
                    "extracting",
                    "transcribing",
                    "summarizing",
                }
                if existing_task.status in processing_statuses:
                    raise BusinessError(ErrorCode.TASK_PROCESSING)

                # 失败的任务，允许创建新任务（或者可以提示用户是否重试旧任务）
                # 这里暂时允许创建，后续可以优化为提示用户

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

        # 初始化任务阶段
        from app.services.task_stage_service import TaskStageService

        await TaskStageService.initialize_stages(db, task)
        await db.commit()

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
    async def get_task_detail(db: AsyncSession, user: User, task_id: str) -> TaskDetailResponse:
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

        # 生成音频播放 URL（通过后端 API 代理，隐藏存储实现细节）
        audio_url = None
        if task.source_key:
            from app.config import settings

            # 返回完整 URL，包含后端地址
            api_base = settings.API_BASE_URL or "http://localhost:8000"
            audio_url = f"{api_base}/api/v1/media/{task.source_key}"

        # 构建阶段信息
        from app.schemas.task import TaskStageResponse

        stages = []
        if hasattr(task, "stages") and task.stages:
            stages = [
                TaskStageResponse(
                    stage_type=stage.stage_type,
                    status=stage.status,
                    started_at=stage.started_at,
                    completed_at=stage.completed_at,
                    error_code=stage.error_code,
                    error_message=stage.error_message,
                    attempt=stage.attempt,
                )
                for stage in task.stages
                if stage.is_active  # 只返回活跃的阶段
            ]

        return TaskDetailResponse(
            id=task.id,
            title=task.title,
            source_type=task.source_type,
            source_key=task.source_key,
            audio_url=audio_url,
            status=task.status,
            progress=task.progress,
            stage=task.stage,
            duration_seconds=task.duration_seconds,
            language=task.detected_language,
            created_at=task.created_at,
            updated_at=task.updated_at,
            error_message=task.error_message,
            stages=stages,
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

    @staticmethod
    async def retry_task(
        db: AsyncSession, user: User, task_id: str, retry_mode: str = "auto"
    ) -> dict[str, object]:
        """重试失败的任务.

        Args:
            db: 数据库会话
            user: 当前用户
            task_id: 任务ID
            retry_mode: 重试模式 (full/auto/from_transcribe/transcribe_only/summarize_only)

        Returns:
            {
                "action": "retrying" | "duplicate_found",
                "task_id": str,
                "duplicate_task_id": str | None,
                "failed_task_ids": list[str],
                "message": str
            }

        Raises:
            BusinessError: 任务不存在、状态不允许重试、重试次数超限
        """
        # 1. 检查任务是否存在且属于当前用户
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

        # 2. 检查任务状态是否允许重试（只有 failed 状态）
        if task.status != "failed":
            raise BusinessError(ErrorCode.TASK_NOT_RETRYABLE)

        # 3. 检查重试次数是否超限（最多重试5次）
        MAX_RETRY = 5
        if task.retry_count >= MAX_RETRY:
            raise BusinessError(ErrorCode.TASK_RETRY_LIMIT_EXCEEDED)

        # 4. 检查是否有相同内容的成功任务（强制跳转，不允许重复处理）
        if task.content_hash:
            duplicate_result = await db.execute(
                select(Task).where(
                    Task.user_id == user.id,
                    Task.content_hash == task.content_hash,
                    Task.status == "completed",
                    Task.id != task_id,
                    Task.deleted_at.is_(None),
                )
            )
            dup_task = duplicate_result.scalar_one_or_none()
            if dup_task:
                # 查找所有相同内容的失败任务（用于批量清理）
                failed_tasks_result = await db.execute(
                    select(Task.id).where(
                        Task.user_id == user.id,
                        Task.content_hash == task.content_hash,
                        Task.status == "failed",
                        Task.deleted_at.is_(None),
                    )
                )
                failed_task_ids = [row[0] for row in failed_tasks_result.fetchall()]

                return {
                    "action": "duplicate_found",
                    "task_id": task_id,
                    "duplicate_task_id": dup_task.id,
                    "failed_task_ids": failed_task_ids,
                    "message": "该内容已有成功处理的任务",
                }

        # 5. 使用 TaskStageService 准备重试
        from app.core.task_stages import RetryMode
        from app.services.task_stage_service import TaskStageService

        # 转换 retry_mode 字符串为枚举
        try:
            mode = RetryMode(retry_mode)
        except ValueError:
            mode = RetryMode.AUTO

        # 准备重试（清理阶段状态，返回起始阶段）
        start_stage = await TaskStageService.prepare_retry(db, task, mode)

        # 6. 重置任务状态
        task.status = "queued"
        task.progress = 1
        task.stage = start_stage.value
        task.error_code = None
        task.error_message = None
        task.retry_count += 1
        await db.commit()

        # 7. 触发 Celery 任务（传递重试模式和起始阶段）
        from worker.celery_app import celery_app

        task_kwargs = {
            "request_id": task.request_id,
            "retry_mode": retry_mode,
            "start_stage": start_stage.value,
        }

        if task.source_type == "youtube":
            celery_app.send_task(
                "worker.tasks.process_youtube",
                args=[task.id],
                kwargs=task_kwargs,
            )
        else:
            celery_app.send_task(
                "worker.tasks.process_audio",
                args=[task.id],
                kwargs=task_kwargs,
            )

        return {
            "action": "retrying",
            "task_id": task_id,
            "duplicate_task_id": None,
            "message": "任务已重新提交",
        }

    @staticmethod
    async def batch_delete_tasks(
        db: AsyncSession, user: User, task_ids: list[str]
    ) -> dict[str, object]:
        """批量删除任务.

        Args:
            db: 数据库会话
            user: 当前用户
            task_ids: 任务ID列表

        Returns:
            {
                "deleted_count": int,
                "failed_ids": list[str]
            }
        """
        deleted_count = 0
        failed_ids = []

        for task_id in task_ids:
            try:
                result = await db.execute(
                    select(Task).where(
                        Task.id == task_id,
                        Task.user_id == user.id,
                        Task.deleted_at.is_(None),
                    )
                )
                task = result.scalar_one_or_none()
                if task is None:
                    failed_ids.append(task_id)
                    continue

                task.deleted_at = datetime.now(timezone.utc)
                deleted_count += 1
            except Exception:
                failed_ids.append(task_id)
                continue

        await db.commit()

        return {
            "deleted_count": deleted_count,
            "failed_ids": failed_ids,
        }
