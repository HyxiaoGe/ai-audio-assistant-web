"""基于 Redis 的固定窗口、按用户限流，以 FastAPI 依赖形式挂到放大成本/抓取的端点。

不引入第三方依赖、不动中间件、不堆装饰器；Redis 故障时 fail-open（绝不因缓存抖动
把正常流量打成 500）。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import Depends

from app.api.deps import CurrentUser, get_current_user, get_current_user_from_query
from app.core.exceptions import BusinessError
from app.core.redis import get_redis_client
from app.i18n.codes import ErrorCode

logger = logging.getLogger("app.core.rate_limit")


async def _check(key: str, limit: int, window_seconds: int) -> None:
    try:
        client = get_redis_client()
        count = await client.incr(key)
        if count == 1:
            # 仅首次命中时设过期，避免时间分桶的旧 key 永久残留、泄漏 Redis 内存
            await client.expire(key, window_seconds)
    except BusinessError:
        raise
    except Exception as exc:  # fail-open：Redis 故障不应把真实流量打成 500
        logger.warning("rate_limit check skipped (redis error) key=%s: %s", key, exc)
        return
    if count > limit:
        raise BusinessError(ErrorCode.RATE_LIMIT_EXCEEDED, retry_after=str(window_seconds))


def rate_limit(*, limit: int, window_seconds: int = 60, scope: str) -> Callable[..., Awaitable[None]]:
    async def _dep(user: CurrentUser = Depends(get_current_user)) -> None:
        bucket = int(time.time() // window_seconds)
        await _check(f"rl:{scope}:{user.id}:{bucket}", limit, window_seconds)

    return _dep


def rate_limit_query(*, limit: int, window_seconds: int = 60, scope: str) -> Callable[..., Awaitable[None]]:
    async def _dep(user: CurrentUser = Depends(get_current_user_from_query)) -> None:
        bucket = int(time.time() // window_seconds)
        await _check(f"rl:{scope}:{user.id}:{bucket}", limit, window_seconds)

    return _dep
