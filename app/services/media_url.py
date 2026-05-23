from __future__ import annotations

from app.config import settings
from app.core.smart_factory import SmartFactory

DEFAULT_MEDIA_DOWNLOAD_EXPIRES = 3600


def get_media_download_expires() -> int:
    expires = settings.MEDIA_DOWNLOAD_EXPIRES or settings.UPLOAD_PRESIGN_EXPIRES or DEFAULT_MEDIA_DOWNLOAD_EXPIRES
    return int(expires)


async def build_media_download_url(object_key: str, user_id: str) -> str:
    storage = await SmartFactory.get_service("storage", user_id=user_id)
    return storage.generate_presigned_url(object_key, get_media_download_expires())
