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
from app.config import settings
from app.core.exceptions import BusinessError
from app.core.redis import get_redis_client
from app.i18n.codes import ErrorCode

logger = logging.getLogger("app.core.rate_limit")

# fail-open 已记过 ERROR 的 scope(每进程每 scope 只记一次,避免 Redis 持续抖动时刷屏)。
_failopen_logged_scopes: set[str] = set()


def _scope_of(key: str) -> str:
    # key 形如 rl:{scope}:... ;取第二段作 scope 用于 log-once
    parts = key.split(":", 2)
    return parts[1] if len(parts) >= 2 else key


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
        scope = _scope_of(key)
        if scope not in _failopen_logged_scopes:
            _failopen_logged_scopes.add(scope)
            # ERROR(非 warning)以便被既有 Kuma/Feishu 日志扫描栈捕获;每 scope 只记一次
            logger.error("rate_limit fail-open (redis error) scope=%s key=%s: %s", scope, key, exc)
        return
    if count > limit:
        raise BusinessError(ErrorCode.RATE_LIMIT_EXCEEDED, retry_after=str(window_seconds))


def _client_ip(request: Request) -> str:
    """解析可信的客户端 IP(给匿名按 IP 限流用)。

    优先级:
    1. CF-Connecting-IP —— cloudflared 是 *.seanfield.org 唯一公网入口,该头由 Cloudflare 边缘
       设置、客户端无法经 CF 伪造,是唯一可从仓内确证可信的来源。
    2. X-Forwarded-For 从右数第 N 跳 —— 仅当显式配置 RATE_LIMIT_TRUSTED_PROXY_HOPS=N>0(默认 0
       即完全不信任 XFF)。最左 token 客户端可伪造、最右跳数取决于不在仓里的 nginx 配置;猜错
       会把所有匿名请求塌进同一个桶 = 自我 DoS,故默认关闭,只有读过 live nginx 配置才设。
    3. socket 地址(request.client.host)—— 粗但永不信任攻击者提供的字节。
    """
    cf_ip = request.headers.get("cf-connecting-ip", "").strip()
    if cf_ip:
        return cf_ip

    hops = settings.RATE_LIMIT_TRUSTED_PROXY_HOPS
    if hops > 0:
        forwarded = request.headers.get("x-forwarded-for", "")
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if len(parts) >= hops:
            return parts[-hops]

    return request.client.host if request.client else "unknown"


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

    IP 解析见 _client_ip:优先 CF-Connecting-IP(不可伪造),XFF 默认不信任(防伪造绕过预算),
    回落 socket 地址。Redis 故障同样 fail-open。
    """

    async def _dep(request: Request) -> None:
        client_ip = _client_ip(request)
        bucket = int(time.time() // window_seconds)
        await _check(f"rl:{scope}:ip:{client_ip}:{bucket}", limit, window_seconds)

    return _dep
