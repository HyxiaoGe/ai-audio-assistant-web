"""Build same-origin media URLs for the frontend.

Always return a relative path under `/api/v1/media/...` so the browser hits
the audio-backend through the same-origin nginx proxy, avoiding CORS issues
from cloud storage hosts (TOS/COS/OSS). The proxy then resolves the object
to whichever storage backend actually holds it.

`user_id` is accepted for compatibility with existing callers; it currently
has no effect because the resolution happens server-side at request time.
"""

from __future__ import annotations

import logging

from app.config import settings
from app.core.smart_factory import SmartFactory

logger = logging.getLogger("app.services.media_url")

DEFAULT_MEDIA_DOWNLOAD_EXPIRES = 3600
MEDIA_PROXY_PREFIX = "/api/v1/media"


def get_media_download_expires() -> int:
    expires = settings.MEDIA_DOWNLOAD_EXPIRES or settings.UPLOAD_PRESIGN_EXPIRES or DEFAULT_MEDIA_DOWNLOAD_EXPIRES
    return int(expires)


async def build_media_download_url(object_key: str, user_id: str) -> str:  # noqa: ARG001
    return f"{MEDIA_PROXY_PREFIX}/{object_key.lstrip('/')}"


async def build_presigned_media_url(object_key: str, expires_in: int) -> str | None:
    """生成短 TTL 的 OSS 预签名 GET 直链(浏览器直连 OSS 取字节,绕开同源代理/隧道)。

    仅供「公开通道」(/api/v1/public/*)出参使用:私有通道的同源代理 URL
    (Cache-Control private + token-in-query)是刻意裁决,别替换。
    签名是纯本地 HMAC、无网络往返(与 app/api/v1/media.py 307 路径同款调用);
    刻意不做 file_exists HEAD——对象缺失时浏览器拿 OSS 404,与代理路径行为一致,
    且省掉列表场景下每图一次同步 HEAD。任一环节失败返回 None,
    调用方自行回落同源代理 URL,绝不让上层 500。
    """
    try:
        storage = await SmartFactory.get_service("storage", provider="oss")
        return str(storage.generate_presigned_url(object_key, expires_in))
    except Exception as exc:
        logger.warning("presign public media url failed for %s: %s", object_key, exc)
        return None
