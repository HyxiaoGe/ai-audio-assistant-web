"""Media file streaming endpoint.

Proxies media files from whatever storage backend hosts them, so the frontend
sees a single same-origin URL (`/api/v1/media/<key>`) regardless of provider.
Avoids cross-origin CORS issues from cloud storage hosts like TOS/COS/OSS.

Implementation strategy: try each candidate storage backend in priority order
(MinIO first — it's the canonical frontend-playback target by convention;
remaining healthy backends after that as fallback for legacy/dual-upload data).
For each candidate, generate a short-lived presigned URL and proxy the request.
On upstream 404 (object missing on this backend), try the next backend.
Forwards Range requests transparently for audio/video seeking.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse

from app.api.deps import CurrentUser, get_current_user, get_media_user
from app.config import settings
from app.core.exceptions import BusinessError
from app.core.health_checker import HealthChecker
from app.core.registry import ServiceRegistry
from app.core.response import success
from app.core.security import SCOPE_MEDIA, issue_scoped_token
from app.core.smart_factory import SmartFactory
from app.i18n.codes import ErrorCode

logger = logging.getLogger("app.api.media")

router = APIRouter()

# 预签名 URL 仅用于服务端立即代理，60 秒足够
_PRESIGN_EXPIRES = 60
# OSS 直下重定向：浏览器拿到预签名 GET 后直连 OSS，需覆盖较长播放 / seek 会话
_REDIRECT_PRESIGN_EXPIRES = 3600
# 单次代理请求最长 10 分钟（覆盖大体积音频/视频）
_PROXY_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=10.0, pool=10.0)
_CHUNK_SIZE = 64 * 1024

# 统一存储后 OSS 为前端可播放资源的主存储；cos/minio 仅作过渡期兜底，
# 覆盖尚未迁移、仅存在于历史后端的对象。
_PRIMARY_PROVIDER = "oss"

_FORWARD_RESPONSE_HEADERS = {
    "content-type",
    "content-length",
    "content-range",
    "accept-ranges",
    "last-modified",
    "etag",
    "cache-control",
}

# 每个媒体对象 key 都把所属用户编码在 `<prefix>/<user_id>/...` 第二段，
# 据此校验归属，杜绝越权读取他人对象（以及目录穿越）。
_OWNED_PREFIXES: tuple[str, ...] = ("upload", "youtube", "visuals", "summary_images")


def assert_owns_media_key(object_key: str, user_id: str) -> None:
    """校验调用方拥有该媒体对象，否则抛 RESOURCE_NOT_FOUND（不泄露存在性/权限信息）。"""
    if ".." in object_key or object_key.startswith("/"):
        raise BusinessError(ErrorCode.RESOURCE_NOT_FOUND)
    parts = object_key.split("/")
    if len(parts) < 3 or parts[0] not in _OWNED_PREFIXES or parts[1] != user_id:
        raise BusinessError(ErrorCode.RESOURCE_NOT_FOUND)


def _candidate_providers() -> list[str]:
    """Return storage providers to try, in priority order.

    MinIO first (canonical), then any other healthy storage backend.
    Falls back to the full registry list if no health data is available
    (e.g. before HealthChecker has run).
    """
    healthy = HealthChecker.get_healthy_services("storage")
    pool = healthy or ServiceRegistry.list_services("storage")
    ordered: list[str] = []
    if _PRIMARY_PROVIDER in pool:
        ordered.append(_PRIMARY_PROVIDER)
    ordered.extend(name for name in pool if name != _PRIMARY_PROVIDER)
    return ordered


@router.post("/ticket")
async def mint_media_ticket(
    user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """签发短期 media 票据，供前端 <img>/<audio> 用 ?token= 访问媒体资源。

    必须用 Authorization header 鉴权（不接受 ?token= 自举），票据仅绑定调用方用户。
    """
    token = issue_scoped_token(sub=user.id, scope=SCOPE_MEDIA, ttl=settings.MEDIA_TOKEN_TTL)
    return success(data={"token": token, "expires_in": settings.MEDIA_TOKEN_TTL})


async def _oss_direct_redirect(file_path: str) -> RedirectResponse | None:
    """对象已在 OSS 时，签发长效预签名 GET 并 307 重定向，让浏览器直连 OSS 取字节。

    OSS 不可用或对象不在 OSS（未迁移 / 仅存在于历史后端）时返回 None，由调用方回落到代理。
    媒体元素（<img>/<audio>）跟随 307 直接拉 OSS，且不触发 CORS，故无需 OSS GET CORS。
    """
    try:
        oss = await SmartFactory.get_service("storage", provider="oss")
    except Exception as exc:
        logger.warning("acquire oss for redirect failed: %s", exc)
        return None
    try:
        # file_exists 是同步网络 HEAD（oss2 阻塞调用），落在每个媒体请求的最热路径上，
        # 必须卸到线程池，避免阻塞事件循环。generate_presigned_url 是纯本地 HMAC 签名，无需线程化。
        if not await asyncio.to_thread(oss.file_exists, file_path):
            return None
        url = oss.generate_presigned_url(file_path, _REDIRECT_PRESIGN_EXPIRES)
    except Exception as exc:
        logger.warning("oss exists/presign failed for %s: %s", file_path, exc)
        return None
    return RedirectResponse(url, status_code=307)


async def _proxy_media(file_path: str, range_header: str | None) -> StreamingResponse:
    """过渡期兜底：服务端从存储后端读取并流式代理，转发 Range 以支持 seek。

    覆盖尚未迁移到 OSS、仅存在于 cos/minio 的历史对象；全量切到 OSS 后此路径不再触发。
    """
    forward_headers: dict[str, str] = {}
    if range_header:
        forward_headers["Range"] = range_header

    providers = _candidate_providers()
    if not providers:
        raise BusinessError(ErrorCode.FILE_STORAGE_SERVICE_ERROR)

    tried: list[str] = []
    for provider in providers:
        try:
            storage = await SmartFactory.get_service("storage", provider=provider)
        except Exception as exc:
            logger.warning("acquire storage %s failed: %s", provider, exc)
            continue

        try:
            presigned = storage.generate_presigned_url(file_path, _PRESIGN_EXPIRES)
        except Exception as exc:
            logger.warning("presign failed on %s for %s: %s", provider, file_path, exc)
            continue

        client = httpx.AsyncClient(timeout=_PROXY_TIMEOUT, follow_redirects=True)
        try:
            upstream = await client.send(
                client.build_request("GET", presigned, headers=forward_headers),
                stream=True,
            )
        except httpx.HTTPError as exc:
            await client.aclose()
            logger.warning("upstream GET on %s failed for %s: %s", provider, file_path, exc)
            continue

        if upstream.status_code == 404:
            await upstream.aclose()
            await client.aclose()
            tried.append(provider)
            logger.info("media %s not found on %s, trying next backend", file_path, provider)
            continue

        if upstream.status_code >= 400:
            body_preview = ""
            with contextlib.suppress(Exception):
                body_preview = (await upstream.aread()).decode("utf-8", errors="replace")[:200]
            await upstream.aclose()
            await client.aclose()
            logger.warning(
                "upstream %s returned %s for %s: %s",
                provider,
                upstream.status_code,
                file_path,
                body_preview,
            )
            raise BusinessError(ErrorCode.FILE_STORAGE_SERVICE_ERROR)

        response_headers = {k: v for k, v in upstream.headers.items() if k.lower() in _FORWARD_RESPONSE_HEADERS}
        media_type = response_headers.get("content-type") or "application/octet-stream"

        async def iter_body(
            upstream: httpx.Response = upstream, client: httpx.AsyncClient = client
        ) -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream.aiter_bytes(_CHUNK_SIZE):
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(
            iter_body(),
            status_code=upstream.status_code,
            headers=response_headers,
            media_type=media_type,
        )

    logger.info("media %s not found on any backend (tried=%s)", file_path, tried)
    raise BusinessError(ErrorCode.RESOURCE_NOT_FOUND)


async def serve_media_object(
    file_path: str, range_header: str | None = None, *, allow_redirect: bool = True
) -> Response:
    """统一媒体取用：优先 OSS 直下（307 重定向，浏览器直连），否则回落服务端代理。

    调用方须先用 assert_owns_media_key 完成归属校验。
    allow_redirect=False 时强制走服务端代理（小图片场景：同源 URL 稳定、可被浏览器长缓存，
    优于每次重签 URL 导致缓存失效的 307）。
    """
    if allow_redirect:
        redirect = await _oss_direct_redirect(file_path)
        if redirect is not None:
            return redirect
    return await _proxy_media(file_path, range_header)


@router.get("/{file_path:path}")
async def stream_media(
    file_path: str,
    request: Request,
    user: CurrentUser = Depends(get_media_user),
) -> Response:
    """Serve a media file: redirect to OSS when present, else proxy from a backend.

    Access requires a valid token (Authorization header or `?token=` query, the
    latter for browser `<audio>`/`<img>` elements that cannot send headers) and
    is gated to the owner encoded in the object key — see assert_owns_media_key.
    """
    assert_owns_media_key(file_path, user.id)
    return await serve_media_object(file_path, request.headers.get("range"))
