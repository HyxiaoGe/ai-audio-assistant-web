"""Build same-origin media URLs for the frontend.

Always return a relative path under `/api/v1/media/...` so the browser hits
the audio-backend through the same-origin nginx proxy, avoiding CORS issues
from cloud storage hosts (TOS/COS/OSS). The proxy then resolves the object
to whichever storage backend actually holds it.

`user_id` is accepted for compatibility with existing callers; it currently
has no effect because the resolution happens server-side at request time.
"""

from __future__ import annotations

from app.config import settings

DEFAULT_MEDIA_DOWNLOAD_EXPIRES = 3600
MEDIA_PROXY_PREFIX = "/api/v1/media"


def get_media_download_expires() -> int:
    expires = settings.MEDIA_DOWNLOAD_EXPIRES or settings.UPLOAD_PRESIGN_EXPIRES or DEFAULT_MEDIA_DOWNLOAD_EXPIRES
    return int(expires)


async def build_media_download_url(object_key: str, user_id: str) -> str:  # noqa: ARG001
    return f"{MEDIA_PROXY_PREFIX}/{object_key.lstrip('/')}"
