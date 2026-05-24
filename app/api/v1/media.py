"""Media file streaming endpoint.

Proxies media files from whatever storage backend hosts them, so the frontend
sees a single same-origin URL (`/api/v1/media/<key>`) regardless of provider.
Avoids cross-origin CORS issues from cloud storage hosts like TOS/COS/OSS.

Implementation strategy: internally generate a short-lived presigned URL via the
storage service, then proxy GET it with `httpx.AsyncClient.stream`. Forwards
Range requests transparently for audio/video seeking.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.core.exceptions import BusinessError
from app.core.smart_factory import SmartFactory
from app.i18n.codes import ErrorCode

logger = logging.getLogger("app.api.media")

router = APIRouter()

# 预签名 URL 仅用于服务端立即代理，60 秒足够
_PRESIGN_EXPIRES = 60
# 单次代理请求最长 10 分钟（覆盖大体积音频/视频）
_PROXY_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=10.0, pool=10.0)
_CHUNK_SIZE = 64 * 1024

_FORWARD_RESPONSE_HEADERS = {
    "content-type",
    "content-length",
    "content-range",
    "accept-ranges",
    "last-modified",
    "etag",
    "cache-control",
}


@router.get("/{file_path:path}")
async def stream_media(file_path: str, request: Request) -> StreamingResponse:
    """Stream a media file from whatever storage backend hosts it.

    Unauthenticated by design — the `<audio>` / `<img>` element used by the
    browser cannot send Authorization headers; security relies on object keys
    being UUID-prefixed (effectively unguessable).
    """
    try:
        storage = await SmartFactory.get_service("storage")
    except Exception as exc:
        logger.warning("Failed to acquire storage for media proxy: %s", exc)
        raise BusinessError(ErrorCode.FILE_STORAGE_SERVICE_ERROR) from exc

    try:
        presigned = storage.generate_presigned_url(file_path, _PRESIGN_EXPIRES)
    except Exception as exc:
        logger.warning("presign failed for %s on %s: %s", file_path, getattr(storage, "provider", "?"), exc)
        raise BusinessError(ErrorCode.FILE_STORAGE_SERVICE_ERROR) from exc

    forward_headers: dict[str, str] = {}
    range_header = request.headers.get("range")
    if range_header:
        forward_headers["Range"] = range_header

    client = httpx.AsyncClient(timeout=_PROXY_TIMEOUT, follow_redirects=True)
    try:
        upstream = await client.send(
            client.build_request("GET", presigned, headers=forward_headers),
            stream=True,
        )
    except httpx.HTTPError as exc:
        await client.aclose()
        logger.warning("upstream GET failed for %s: %s", file_path, exc)
        raise BusinessError(ErrorCode.FILE_STORAGE_SERVICE_ERROR) from exc

    if upstream.status_code == 404:
        await upstream.aclose()
        await client.aclose()
        raise BusinessError(ErrorCode.RESOURCE_NOT_FOUND)

    if upstream.status_code >= 400:
        body_preview = ""
        with contextlib.suppress(Exception):
            body_preview = (await upstream.aread()).decode("utf-8", errors="replace")[:200]
        await upstream.aclose()
        await client.aclose()
        logger.warning(
            "upstream returned %s for %s: %s",
            upstream.status_code,
            file_path,
            body_preview,
        )
        raise BusinessError(ErrorCode.FILE_STORAGE_SERVICE_ERROR)

    response_headers = {
        k: v for k, v in upstream.headers.items() if k.lower() in _FORWARD_RESPONSE_HEADERS
    }
    media_type = response_headers.get("content-type") or "application/octet-stream"

    async def iter_body() -> AsyncIterator[bytes]:
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
