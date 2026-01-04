from __future__ import annotations

from celery import Celery

from app.config import settings


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

from app.services.asr import aliyun, tencent  # noqa: F401, E402

# Import all service modules to trigger @register_service decorators
# This ensures services are registered in the ServiceRegistry
from app.services.llm import deepseek, doubao, moonshot, qwen  # noqa: F401, E402
from app.services.storage import cos, minio, oss  # noqa: F401, E402

# Import tasks to register them with Celery
# Must import after celery_app is created to avoid circular imports
from worker.tasks import download_youtube  # noqa: F401, E402
from worker.tasks import process_audio  # noqa: F401, E402
from worker.tasks import process_youtube  # noqa: F401, E402
from worker.tasks import regenerate_summary  # noqa: F401, E402
