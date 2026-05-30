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

import contextlib
import logging
from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, get_current_user_from_query
from app.core.exceptions import BusinessError
from app.core.health_checker import HealthChecker
from app.core.registry import ServiceRegistry
from app.core.smart_factory import SmartFactory
from app.i18n.codes import ErrorCode

logger = logging.getLogger("app.api.media")

router = APIRouter()

# 预签名 URL 仅用于服务端立即代理，60 秒足够
_PRESIGN_EXPIRES = 60
# 单次代理请求最长 10 分钟（覆盖大体积音频/视频）
_PROXY_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=10.0, pool=10.0)
_CHUNK_SIZE = 64 * 1024

# MinIO 是前端可播放资源的约定主存储（YouTube/图片都会双写一份到 MinIO），
# 其它云存储仅作为兜底，覆盖历史数据或单写到云端的对象。
_PRIMARY_PROVIDER = "minio"

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


@router.get("/{file_path:path}")
async def stream_media(
    file_path: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user_from_query),
) -> StreamingResponse:
    """Stream a media file from whatever storage backend hosts it.

    Access requires a valid token (Authorization header or `?token=` query, the
    latter for browser `<audio>`/`<img>` elements that cannot send headers) and
    is gated to the owner encoded in the object key — see assert_owns_media_key.
    """
    assert_owns_media_key(file_path, user.id)

    forward_headers: dict[str, str] = {}
    range_header = request.headers.get("range")
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

        response_headers = {
            k: v for k, v in upstream.headers.items() if k.lower() in _FORWARD_RESPONSE_HEADERS
        }
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
