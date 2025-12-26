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

celery_app.autodiscover_tasks(["worker.tasks"])
