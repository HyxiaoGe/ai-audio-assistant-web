"""基于 Redis 的固定窗口、按用户限流，以 FastAPI 依赖形式挂到放大成本/抓取的端点。

不引入第三方依赖、不动中间件、不堆装饰器；Redis 故障时 fail-open（绝不因缓存抖动
把正常流量打成 500）。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import Depends, Request

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


def rate_limit_query(
    *,
    limit: int,
    window_seconds: int = 60,
    scope: str,
    auth: Callable[..., Awaitable[CurrentUser]] = get_current_user_from_query,
) -> Callable[..., Awaitable[None]]:
    """按用户限流（用户从 query/header token 解析）。

    ``auth`` 让调用方指定具体的鉴权依赖（如 SSE 端点传 ``get_stream_user`` 以接受
    短期 stream 票据）；默认沿用 ``get_current_user_from_query``。FastAPI 会缓存同一
    依赖的结果，故与端点主鉴权共用同一个 ``auth`` 时只解析一次。
    """

    async def _dep(user: CurrentUser = Depends(auth)) -> None:
        bucket = int(time.time() // window_seconds)
        await _check(f"rl:{scope}:{user.id}:{bucket}", limit, window_seconds)

    return _dep


def rate_limit_by_ip(*, limit: int, window_seconds: int = 60, scope: str) -> Callable[..., Awaitable[None]]:
    """匿名公开端点按客户端 IP 固定窗口限流(无 user 可依)。

    经 nginx/cloudflared 反代,直连 socket 是代理地址,故优先取 X-Forwarded-For
    第一跳,无则回退 request.client.host。XFF 可伪造,这里只做滥用阻尼非安全边界;
    Redis 故障同样 fail-open。
    """

    async def _dep(request: Request) -> None:
        forwarded = request.headers.get("x-forwarded-for", "")
        client_ip = forwarded.split(",")[0].strip() if forwarded else ""
        if not client_ip:
            client_ip = request.client.host if request.client else "unknown"
        bucket = int(time.time() // window_seconds)
        await _check(f"rl:{scope}:ip:{client_ip}:{bucket}", limit, window_seconds)

    return _dep
