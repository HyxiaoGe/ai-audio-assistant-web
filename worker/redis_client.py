"""
Synchronous Redis client for Celery workers.

Worker tasks use sync Redis operations to avoid asyncio event loop conflicts.
The FastAPI app continues to use async Redis (app/core/redis.py).
"""

from __future__ import annotations

from typing import Optional

from redis import Redis

from app.config import settings

_sync_redis_client: Optional[Redis] = None


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
    Publish task update to both task-specific and user-global channels.

    This ensures:
    1. Legacy single-task WebSocket clients (/ws/tasks/{id}) receive updates
    2. Global WebSocket clients (/ws/user) receive updates for all their tasks

    Args:
        task_id: Task ID for task-specific channel
        user_id: User ID for user-global channel
        message: JSON message to publish
    """
    client = get_sync_redis_client()
    # Publish to task-specific channel (legacy support)
    client.publish(f"tasks:{task_id}", message)
    # Publish to user-global channel (new global WebSocket)
    client.publish(f"user:{user_id}:updates", message)
