from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from app.schemas.task import YouTubeVideoInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, is_admin_user
from app.config import settings
from app.core.exceptions import BusinessError
from app.core.registry import ServiceRegistry
from app.i18n.codes import ErrorCode
from app.models.task import Task
from app.schemas.task import TaskCreateRequest, TaskDetailResponse, TaskListItem
from app.services.asr_quota_service import check_any_provider_available
from app.services.media_url import build_media_download_url

logger = logging.getLogger(__name__)

# 上传对象 key 由 presign 接口签发，形如
# ``upload/{user_id}/{YYYY}/{MM}/{DD}/{uuid4hex}{ext}``（见 app/api/v1/upload.py）。
# 客户端只能引用自己前缀下、且严格匹配该形态的 key，否则可越权读/写/删他人对象。
_UPLOAD_KEY_RE = re.compile(r"^upload/[^/]+/\d{4}/\d{2}/\d{2}/[0-9a-f]{32}(?:\.[A-Za-z0-9]+)?$")

# 仅允许从这些受信媒体站点的主机（或其子域）拉取，杜绝 SSRF 打内网 / 云元数据端点。
_ALLOWED_INGEST_HOSTS: tuple[str, ...] = ("youtube.com", "youtu.be", "bilibili.com", "b23.tv")

# 处于"处理中"（非终态 completed/failed）的任务状态全集——任务流水线的中间态。
# list_tasks 的 status="processing" 伞形筛选、以及 tasks.py 路由的状态白名单都从这里派生，
# 单一事实源避免新增流水线阶段（如 polishing）时漏改其中一处，导致该状态在筛选里隐身。
PROCESSING_STATUSES: tuple[str, ...] = (
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
    "polishing",
    "summarizing",
)


class TaskService:
    @staticmethod
    def _extract_youtube_video_id(url: str) -> str | None:
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
    def _validate_provider_selection(data: TaskCreateRequest) -> None:
        """校验用户在 options 里指定的 provider 是否为已注册且可用的服务。

        与配额无关，对所有用户（含管理员）一律校验——配额可以对管理员放行，但
        「未知 provider」必须尽早在创建阶段挡掉，否则会一路漏到 worker，让
        SmartFactory.get_service 抛裸 ValueError 并白白触发 3 次 Celery 重试。

        - asr_provider：必须是已注册的 ASR 服务（修复管理员绕过校验的问题）
        - asr_variant：必须是已知计费变体（file / file_fast），否则会漏到 worker 让 consume_quota
          因 get_pricing_config 返回 None 而早抛 ValueError，免费额度周期分拆漏写、成本台账少记
        - provider（LLM）：必须是支持文本生成的 LLM 服务（排除 image_service 这类只生图的）
        """
        options = data.options.model_dump() if data.options else {}

        asr_variant = options.get("asr_variant")
        if asr_variant:
            from app.services.asr_quota_service import KNOWN_VARIANTS

            if asr_variant not in KNOWN_VARIANTS:
                # 40000 的 i18n 模板是 "{detail}"，必须用 detail= 传原因，否则前端只会收到裸 "{detail}"
                raise BusinessError(
                    ErrorCode.PARAMETER_ERROR,
                    detail=f"未知 asr_variant: {asr_variant}（可用: {sorted(KNOWN_VARIANTS)}）",
                )

        asr_provider = options.get("asr_provider")
        if asr_provider:
            asr_providers = ServiceRegistry.list_services("asr")
            # 仅在确有已注册 ASR 服务时才校验；空注册表交给后续配额预检处理
            if asr_providers and asr_provider not in asr_providers:
                raise BusinessError(ErrorCode.ASR_PROVIDER_NOT_AVAILABLE, provider=asr_provider)

        # LLM 的 provider 字段来自 /llm/models 的「展示分组」标签（deepseek/openai/litellm…），
        # 并非注册服务名：文本 LLM 已统一经 proxy 路由，真正的选择键是 model_id
        # （worker 侧 _resolve_llm_selection 会把展示名归一到注册的 proxy 服务）。
        # 因此这里不再按注册服务名校验 provider（否则用户一选具体模型就被误拒为 40000），
        # 只兜底确认确有可用的文本 LLM 后端，避免无任何后端可用时白创建任务。
        llm_selected = (
            options.get("llm_provider")
            or options.get("provider")
            or options.get("llm_model_id")
            or options.get("model_id")
        )
        if llm_selected and not ServiceRegistry.list_text_llm_providers():
            raise BusinessError(
                ErrorCode.PARAMETER_ERROR,
                detail="当前没有可用的文本 LLM 服务，请稍后重试",
            )

    @staticmethod
    async def _check_asr_quota_precheck(db: AsyncSession, user: CurrentUser, data: TaskCreateRequest) -> None:
        """ASR 配额预检

        在任务创建前检查是否有可用的 ASR 配额，避免创建注定失败的任务。

        策略：
        1. 管理员用户跳过配额检查
        2. 如果用户指定了 asr_provider，检查该提供商配额
        3. 否则检查是否有任意可用提供商
        4. 配额耗尽时返回友好的错误信息
        """
        # 管理员不受配额限制
        if is_admin_user(user):
            logger.debug("Admin user %s skipping ASR quota precheck", user.email)
            return

        # 获取用户指定的 ASR 配置
        options = data.options.model_dump() if data.options else {}
        asr_provider = options.get("asr_provider")
        asr_variant = options.get("asr_variant", "file")

        all_providers = ServiceRegistry.list_services("asr")
        if not all_providers:
            # 没有配置任何 ASR 提供商，跳过检查
            logger.warning("No ASR providers registered, skipping quota precheck")
            return

        if asr_provider:
            # 用户指定了特定提供商，检查该提供商
            if asr_provider not in all_providers:
                raise BusinessError(
                    ErrorCode.ASR_PROVIDER_NOT_AVAILABLE,
                    provider=asr_provider,
                )

            # 检查指定提供商的配额
            has_quota, _ = await check_any_provider_available(db, str(user.id), variant=asr_variant)
            if not has_quota:
                # 检查指定的提供商是否在可用列表中
                from app.services.asr_quota_service import select_available_provider

                available = await select_available_provider(db, [asr_provider], str(user.id), variant=asr_variant)
                if not available:
                    raise BusinessError(
                        ErrorCode.ASR_QUOTA_EXCEEDED,
                        provider=asr_provider,
                    )
        else:
            # 没有指定提供商，检查是否有任意可用提供商
            has_quota, available_providers = await check_any_provider_available(db, str(user.id), variant=asr_variant)
            if not has_quota:
                raise BusinessError(
                    ErrorCode.ALL_ASR_QUOTAS_EXCEEDED,
                )

            logger.debug(
                "ASR quota precheck passed, available providers: %s",
                available_providers,
            )

    @staticmethod
    def _validate_upload_file_key(file_key: str, user_id: str) -> None:
        """校验客户端提交的上传 key 归属当前用户且形态合法。

        防止认证用户把 ``source_key`` 指向他人对象（跨租户读 / 任意写 / 任意删）。
        """
        if ".." in file_key or not file_key.startswith(f"upload/{user_id}/") or _UPLOAD_KEY_RE.match(file_key) is None:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="file_key")

    @staticmethod
    def validate_ingest_url(url: str | None) -> None:
        """对用户提交的拉取 URL 做严格校验（SSRF 防护），非法时抛 BusinessError。

        白名单主机（精确或子域）是主防线，同时也挡掉十进制 / 十六进制 IP 编码；
        IP 字面量分支是对环回 / 链路本地 / RFC1918 / 元数据等规范写法的额外兜底。
        全程不做 DNS 解析，保持纯函数、无网络副作用。
        """
        if not url:
            raise BusinessError(ErrorCode.MISSING_REQUIRED_PARAMETER, field="source_url")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise BusinessError(ErrorCode.INVALID_URL_FORMAT)
        # hostname 已剥离 userinfo / port，挫败 user@host 与 host:port 绕过
        host = (parsed.hostname or "").lower()
        if not host:
            raise BusinessError(ErrorCode.INVALID_URL_FORMAT)
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            # 任意 IP 字面量都不可能是受信媒体主机
            raise BusinessError(ErrorCode.UNSUPPORTED_YOUTUBE_URL_FORMAT)
        if not any(host == h or host.endswith("." + h) for h in _ALLOWED_INGEST_HOSTS):
            raise BusinessError(ErrorCode.UNSUPPORTED_YOUTUBE_URL_FORMAT)

    @staticmethod
    async def create_task(db: AsyncSession, user: CurrentUser, data: TaskCreateRequest, trace_id: str | None) -> Task:
        if data.source_type not in {"upload", "youtube"}:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="source_type")

        if data.source_type == "upload":
            if not data.file_key:
                raise BusinessError(ErrorCode.MISSING_REQUIRED_PARAMETER, field="file_key")
            # 校验客户端提交的 file_key 属于当前用户且形态合法，避免越权访问共享存储对象
            TaskService._validate_upload_file_key(data.file_key, str(user.id))

        # YouTube/Bilibili 任务：验证 URL 并自动生成 content_hash
        if data.source_type == "youtube":
            if not data.source_url:
                raise BusinessError(ErrorCode.MISSING_REQUIRED_PARAMETER, field="source_url")
            # 严格校验拉取 URL（白名单主机 + 拒绝 IP 字面量），防 SSRF。纯函数、无网络副作用。
            TaskService.validate_ingest_url(data.source_url)

            # 不在创建路径上做同步 yt-dlp 校验。旧实现 await extract_info、给 20s 总超时阻塞请求：
            # 国内直连 YouTube 抖动一旦 >20s 即误杀，任务还没入库就抛 51300，前端只见「卡顿/失败」
            # 且 DB 里查不到（不可见失败）。改为立即入库 queued 并派发，由 worker 的 RESOLVE_YOUTUBE
            # 阶段真正解析、回填标题、即便失败也记成「可见可重试」的 failed 任务——与订阅自动转写
            # youtube_auto_transcribe 同一模式。content_hash 仅靠正则取 video_id（不依赖网络），去重照常生效。
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

        # provider 合法性校验（对所有用户，含管理员）：未知 provider 尽早挡在创建阶段
        TaskService._validate_provider_selection(data)

        # ASR 配额预检：确保有可用的 ASR 提供商
        await TaskService._check_asr_quota_precheck(db, user, data)

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
            created_at=datetime.now(UTC),
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
        user: CurrentUser,
        page: int,
        page_size: int,
        status_filter: str,
    ) -> tuple[list[TaskListItem], int]:
        base_query = select(Task).where(
            Task.user_id == user.id,
            Task.deleted_at.is_(None),
        )
        if status_filter == "processing":
            base_query = base_query.where(Task.status.in_(PROCESSING_STATUSES))
        elif status_filter != "all":
            base_query = base_query.where(Task.status == status_filter)

        count_query = select(func.count()).select_from(base_query.subquery())
        total = int((await db.execute(count_query)).scalar_one())

        items_query = base_query.order_by(Task.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
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
    async def get_status_counts(db: AsyncSession, user: CurrentUser) -> dict[str, int]:
        """一次 GROUP BY 统计当前用户各状态任务数，按列表筛选的同一伞形规则分桶。

        与 list_tasks 共用 PROCESSING_STATUSES 单一事实源，避免计数与筛选漂移；
        替代前端为四个 tab 各发一次 page_size=1 列表查询（列表页加载 5 连发 → 2）。
        """
        rows = (
            await db.execute(
                select(Task.status, func.count())
                .where(
                    Task.user_id == user.id,
                    Task.deleted_at.is_(None),
                )
                .group_by(Task.status)
            )
        ).all()
        processing_set = set(PROCESSING_STATUSES)
        counts = {"all": 0, "processing": 0, "completed": 0, "failed": 0}
        for status, count in rows:
            count = int(count)
            counts["all"] += count
            if status == "completed":
                counts["completed"] += count
            elif status == "failed":
                counts["failed"] += count
            elif status in processing_set:
                counts["processing"] += count
        return counts

    @staticmethod
    async def get_task_detail(db: AsyncSession, user: CurrentUser, task_id: str) -> TaskDetailResponse:
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

        audio_url = None
        if task.source_key:
            audio_url = await build_media_download_url(task.source_key, user.id)

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

        # 获取 YouTube 视频信息（如果是 YouTube 来源）
        youtube_info = None
        if task.source_type == "youtube" and task.source_url:
            youtube_info = await TaskService._get_youtube_video_info(db, user.id, task.source_url)

        return TaskDetailResponse(
            id=task.id,
            title=task.title,
            source_type=task.source_type,
            source_key=task.source_key,
            source_url=task.source_url,
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
            youtube_info=youtube_info,
            detected_summary_style=TaskDetailResponse.detected_summary_style_from_options(task.options),
        )

    @staticmethod
    async def _get_youtube_video_info(db: AsyncSession, user_id: str, source_url: str) -> YouTubeVideoInfo | None:
        """获取 YouTube 视频信息.

        优先从本地缓存获取，缓存未命中时尝试从 YouTube API 获取。
        """
        from app.models.account import Account
        from app.models.youtube_subscription import YouTubeSubscription
        from app.models.youtube_video import YouTubeVideo
        from app.schemas.task import YouTubeVideoInfo

        # 从 URL 提取 video_id
        video_id = TaskService._extract_youtube_video_id(source_url)
        if not video_id:
            return None

        # 1. 先查询本地缓存
        result = await db.execute(
            select(YouTubeVideo).where(
                YouTubeVideo.user_id == user_id,
                YouTubeVideo.video_id == video_id,
            )
        )
        video = result.scalar_one_or_none()

        if video:
            # 缓存命中，查询频道信息
            channel_title = None
            channel_thumbnail = None
            sub_result = await db.execute(
                select(YouTubeSubscription).where(
                    YouTubeSubscription.user_id == user_id,
                    YouTubeSubscription.channel_id == video.channel_id,
                )
            )
            subscription = sub_result.scalar_one_or_none()
            if subscription:
                channel_title = subscription.channel_title
                channel_thumbnail = subscription.channel_thumbnail

            return YouTubeVideoInfo(
                video_id=video.video_id,
                channel_id=video.channel_id,
                channel_title=channel_title,
                channel_thumbnail=channel_thumbnail,
                title=video.title,
                description=video.description,
                thumbnail_url=video.thumbnail_url,
                published_at=video.published_at,
                duration_seconds=video.duration_seconds,
                view_count=video.view_count,
                like_count=video.like_count,
                comment_count=video.comment_count,
            )

        # 2. 缓存未命中，尝试从 YouTube API 获取
        account_result = await db.execute(
            select(Account).where(
                Account.user_id == user_id,
                Account.provider == "youtube",
            )
        )
        account = account_result.scalar_one_or_none()

        if account and account.access_token:
            try:
                from google.oauth2.credentials import Credentials

                from app.services.youtube.data_service import YouTubeDataService

                credentials = Credentials(  # nosec B106 - token_uri is not a password
                    token=account.access_token,
                    refresh_token=account.refresh_token,
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=settings.GOOGLE_CLIENT_ID,
                    client_secret=settings.GOOGLE_CLIENT_SECRET,
                )

                data_service = YouTubeDataService(credentials)
                video_info = data_service.get_video_full_info(video_id)

                if video_info:
                    return YouTubeVideoInfo(
                        video_id=video_info["video_id"],
                        channel_id=video_info.get("channel_id") or "",
                        channel_title=video_info.get("channel_title"),
                        channel_thumbnail=None,  # API 不返回频道缩略图
                        title=video_info.get("title") or "",
                        description=video_info.get("description"),
                        thumbnail_url=video_info.get("thumbnail_url"),
                        published_at=video_info.get("published_at"),
                        duration_seconds=video_info.get("duration_seconds"),
                        view_count=video_info.get("view_count"),
                        like_count=video_info.get("like_count"),
                        comment_count=video_info.get("comment_count"),
                    )
            except Exception as e:
                logger.warning(f"Failed to fetch video info from YouTube API: {e}")

        # 3. 无法获取详细信息，返回基本信息
        return YouTubeVideoInfo(
            video_id=video_id,
            channel_id="",
            title="",
        )

    @staticmethod
    async def delete_task(db: AsyncSession, user: CurrentUser, task_id: str) -> None:
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
        task.deleted_at = datetime.now(UTC)
        await db.commit()

        from worker.celery_app import celery_app

        delay_seconds = max(settings.TASK_CLEANUP_DELAY_SECONDS, 0)
        task_args = [task.id, str(task.user_id)]
        if delay_seconds > 0:
            celery_app.send_task(
                "worker.tasks.cleanup_task_data",
                args=task_args,
                countdown=delay_seconds,
            )
        else:
            celery_app.send_task("worker.tasks.cleanup_task_data", args=task_args)

    @staticmethod
    async def retry_task(db: AsyncSession, user: CurrentUser, task_id: str) -> dict[str, object]:
        """重试失败的任务.

        Args:
            db: 数据库会话
            user: 当前用户
            task_id: 任务ID
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

        task_kwargs = {"request_id": task.request_id}

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
    async def batch_delete_tasks(db: AsyncSession, user: CurrentUser, task_ids: list[str]) -> dict[str, object]:
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

                task.deleted_at = datetime.now(UTC)
                deleted_count += 1
            except Exception:
                failed_ids.append(task_id)
                continue

        await db.commit()

        return {
            "deleted_count": deleted_count,
            "failed_ids": failed_ids,
        }
