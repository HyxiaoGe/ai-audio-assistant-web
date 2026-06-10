"""Media file streaming endpoint.

Serves media to the frontend under a single same-origin URL
(`/api/v1/media/<key>`), avoiding cross-origin CORS issues from the OSS host.

Storage is unified on Alibaba OSS. Audio/video resolve via a 307 redirect to a
presigned OSS GET so the browser streams bytes directly from OSS (see
`_oss_direct_redirect`). Small immutable assets (summary images) are instead
server-side proxied from OSS for a stable, long-cacheable same-origin URL (see
`_proxy_media`). Range requests are forwarded transparently for seeking.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, MediaPrincipal, get_current_user, get_db, get_media_principal
from app.config import settings
from app.core.exceptions import BusinessError
from app.core.response import success
from app.core.security import SCOPE_MEDIA, issue_scoped_token
from app.core.smart_factory import SmartFactory
from app.i18n.codes import ErrorCode
from app.models.task import Task

logger = logging.getLogger("app.api.media")

router = APIRouter()

# 预签名 URL 仅用于服务端立即代理，60 秒足够
_PRESIGN_EXPIRES = 60
# OSS 直下重定向：浏览器拿到预签名 GET 后直连 OSS，需覆盖较长播放 / seek 会话
_REDIRECT_PRESIGN_EXPIRES = 3600
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


async def assert_public_media_access(
    db: AsyncSession, public_task_id: str, sub: str, object_key: str
) -> None:
    """公开媒体票(resource pin)的允许集复核。

    每次请求 DB 复核「任务仍公开」——管理员取消公开后已签发的票立即失效
    (残余暴露只剩已发出的 OSS 预签名音频 URL 和浏览器已缓存的图)。
    允许集 = {task.source_key} ∪ summary_images/{owner}/{task_id}/* ,集外一律
    RESOURCE_NOT_FOUND(不泄露存在性)。
    """
    # 双保险:公开票 sub=任务 owner,key 第二段必须仍等于 sub(防 pin 票横向越权)
    assert_owns_media_key(object_key, sub)
    task = (
        await db.execute(
            select(Task).where(
                Task.id == public_task_id,
                Task.user_id == sub,
                Task.is_public.is_(True),
                Task.status == "completed",
                Task.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if task is None:
        raise BusinessError(ErrorCode.RESOURCE_NOT_FOUND)
    allowed = object_key == task.source_key or object_key.startswith(
        f"summary_images/{task.user_id}/{task.id}/"
    )
    if not allowed:
        raise BusinessError(ErrorCode.RESOURCE_NOT_FOUND)


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

    OSS 不可用或对象不在 OSS（尚未迁移 / 暂态错误）时返回 None，由调用方回落到服务端代理。
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
    """服务端从 OSS 读取并流式代理，转发 Range 以支持 seek。

    用于小而不可变的图片（summary images）：同源 URL 稳定、可被浏览器长缓存，优于
    每次重签的 307。音频/视频走 _oss_direct_redirect 的 307 直下，不经此路径。
    """
    forward_headers: dict[str, str] = {}
    if range_header:
        forward_headers["Range"] = range_header

    try:
        storage = await SmartFactory.get_service("storage", provider="oss")
    except Exception as exc:
        logger.warning("acquire oss for proxy failed: %s", exc)
        raise BusinessError(ErrorCode.FILE_STORAGE_SERVICE_ERROR) from exc

    try:
        presigned = storage.generate_presigned_url(file_path, _PRESIGN_EXPIRES)
    except Exception as exc:
        logger.warning("presign failed for %s: %s", file_path, exc)
        raise BusinessError(ErrorCode.FILE_STORAGE_SERVICE_ERROR) from exc

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
        logger.info("media %s not found on oss", file_path)
        raise BusinessError(ErrorCode.RESOURCE_NOT_FOUND)

    if upstream.status_code >= 400:
        body_preview = ""
        with contextlib.suppress(Exception):
            body_preview = (await upstream.aread()).decode("utf-8", errors="replace")[:200]
        await upstream.aclose()
        await client.aclose()
        logger.warning(
            "upstream oss returned %s for %s: %s",
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
    principal: MediaPrincipal = Depends(get_media_principal),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Serve a media file: redirect to OSS when present, else server-side proxy from OSS.

    Access requires a valid token (Authorization header or `?token=` query, the
    latter for browser `<audio>`/`<img>` elements that cannot send headers).
    普通票/登录态走 owner 命名空间校验;公开任务 pin 票走「仍公开 + 允许集」DB 复核。
    """
    if principal.public_task_id is not None:
        await assert_public_media_access(db, principal.public_task_id, principal.user.id, file_path)
    else:
        assert_owns_media_key(file_path, principal.user.id)
    return await serve_media_object(file_path, request.headers.get("range"))
