"""
Synchronous Redis client for Celery workers.

Worker tasks use sync Redis operations to avoid asyncio event loop conflicts.
The FastAPI app continues to use async Redis (app/core/redis.py).
"""

from __future__ import annotations

from redis import Redis

from app.config import settings

_sync_redis_client: Redis | None = None


def _get_redis_url() -> str:
    redis_url = settings.REDIS_URL
    if not redis_url:
        raise RuntimeError("REDIS_URL is not set")
    return redis_url


def get_sync_redis_client() -> Redis:
    """Get or create sync Redis client for worker tasks."""
    global _sync_redis_client
    if _sync_redis_client is None:
        _sync_redis_client = Redis.from_url(_get_redis_url(), decode_responses=True)
    return _sync_redis_client


def publish_message_sync(channel: str, message: str) -> None:
    """
    Publish message to Redis channel synchronously.

    Used by worker tasks to send progress updates without asyncio.
    """
    client = get_sync_redis_client()
    client.publish(channel, message)


def publish_task_update_sync(task_id: str, user_id: str, message: str) -> None:
    """
    Publish task update to the user's global channel.

    Task-progress is delivered solely through the single user channel
    (legacy /ws/tasks/{id} and the tasks:{id} dual-publish were retired);
    the task_id arg is kept for call-site compatibility.

    Args:
        task_id: Task ID (no longer used for a separate channel).
        user_id: User ID for user-global channel.
        message: JSON message to publish.
    """
    client = get_sync_redis_client()
    client.publish(f"user:{user_id}:updates", message)


def publish_user_notification_sync(user_id: str, message: str) -> None:
    """
    Publish notification to user's global channel.

    Used for non-task notifications like YouTube sync completion.

    Args:
        user_id: User ID for user-global channel
        message: JSON message to publish
    """
    client = get_sync_redis_client()
    client.publish(f"user:{user_id}:updates", message)
