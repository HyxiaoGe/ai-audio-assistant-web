from __future__ import annotations

from typing import Optional

from redis.asyncio import Redis

from app.config import settings

_redis_client: Optional[Redis] = None


def _get_redis_url() -> str:
    redis_url = settings.REDIS_URL
    if not redis_url:
        raise RuntimeError("REDIS_URL is not set")
    return redis_url


def get_redis_client() -> Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_url(_get_redis_url(), decode_responses=True)
    return _redis_client


async def publish_message(channel: str, message: str) -> None:
    client = get_redis_client()
    await client.publish(channel, message)
