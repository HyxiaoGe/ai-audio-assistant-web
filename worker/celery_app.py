from __future__ import annotations

import asyncio

from celery import Celery
from celery.schedules import crontab

from app.config import settings
from app.core.config_manager import ConfigManager
from app.db import async_session_factory


def _get_redis_url() -> str:
    redis_url = settings.REDIS_URL
    if not redis_url:
        raise RuntimeError("REDIS_URL is not set")
    return redis_url


celery_app = Celery(
    "ai_audio_assistant",
    broker=_get_redis_url(),
    backend=_get_redis_url(),
)

celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.accept_content = ["json"]
celery_app.conf.timezone = "UTC"

# Celery Beat 定时任务配置
celery_app.conf.beat_schedule = {
    # ASR 配额预警检查 - 每小时执行一次
    "check-asr-quota-alerts": {
        "task": "worker.tasks.quota_alert.check_asr_quota_alerts",
        "schedule": crontab(minute=0),  # 每小时整点执行
        "options": {"queue": "default"},
    },
    # YouTube 视频同步 - 每天 UTC 3:00 执行
    "sync-youtube-videos-daily": {
        "task": "worker.tasks.sync_youtube_videos.sync_all_subscriptions_videos",
        "schedule": crontab(hour=3, minute=0),  # 每天 UTC 3:00
        "options": {"queue": "default"},
        "kwargs": {"max_videos_per_channel": 20},
    },
    # YouTube 智能同步检查 - 每小时 :30 执行
    "check-youtube-scheduled-syncs": {
        "task": "worker.tasks.sync_youtube_videos.check_scheduled_syncs",
        "schedule": crontab(minute=30),  # 每小时 30 分执行
        "options": {"queue": "default"},
    },
}

ConfigManager.configure_db(
    async_session_factory, cache_ttl_seconds=settings.CONFIG_CENTER_CACHE_TTL
)
if settings.CONFIG_CENTER_DB_ENABLED:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(ConfigManager.refresh_from_db())
    else:
        loop.create_task(ConfigManager.refresh_from_db())

from app.services.asr import aliyun  # noqa: F401, E402
from app.services.asr import configs as asr_configs  # noqa: F401, E402
from app.services.asr import tencent, volcengine  # noqa: F401, E402

# Import all service modules to trigger @register_service decorators
# This ensures services are registered in the ServiceRegistry
from app.services.llm import configs as llm_configs  # noqa: F401, E402
from app.services.llm import deepseek, doubao, moonshot, qwen  # noqa: F401, E402
from app.services.storage import configs as storage_configs  # noqa: F401, E402
from app.services.storage import cos, minio, oss, tos  # noqa: F401, E402

# Import tasks to register them with Celery
# Must import after celery_app is created to avoid circular imports
from worker.tasks import cleanup_task  # noqa: F401, E402
from worker.tasks import download_youtube  # noqa: F401, E402
from worker.tasks import process_audio  # noqa: F401, E402
from worker.tasks import process_visual_summary  # noqa: F401, E402
from worker.tasks import process_youtube  # noqa: F401, E402
from worker.tasks import quota_alert  # noqa: F401, E402
from worker.tasks import regenerate_summary  # noqa: F401, E402
from worker.tasks import sync_youtube_subscriptions  # noqa: F401, E402
from worker.tasks import sync_youtube_videos  # noqa: F401, E402
from worker.tasks import youtube_auto_transcribe  # noqa: F401, E402
