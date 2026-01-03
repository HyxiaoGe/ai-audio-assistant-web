from __future__ import annotations

import asyncio
import logging
import mimetypes
import tempfile
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.core.smart_factory import SmartFactory

logger = logging.getLogger("app.avatar")


def _download_avatar(url: str) -> Tuple[Optional[str], Optional[str]]:
    if not url:
        return None, None
    req = urllib.request.Request(url, headers={"User-Agent": "ai-audio-assistant/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        content_type = resp.headers.get_content_type()
        suffix = mimetypes.guess_extension(content_type) or ""
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(resp.read())
            return tmp.name, content_type


async def _download_avatar_async(url: str) -> Tuple[Optional[str], Optional[str]]:
    return await asyncio.to_thread(_download_avatar, url)


def _build_avatar_key(user_id: str, content_type: Optional[str]) -> str:
    extension = mimetypes.guess_extension(content_type or "") or ".png"
    return f"users/{user_id}/avatar{extension}"


class AvatarService:
    @staticmethod
    async def sync_avatar(
        db: AsyncSession, user: User, avatar_url: Optional[str]
    ) -> None:
        if not avatar_url:
            return
        try:
            file_path, content_type = await _download_avatar_async(avatar_url)
        except Exception as exc:
            logger.warning("avatar download failed: %s", exc)
            return
        if not file_path:
            return
        try:
            # 使用 SmartFactory 获取 storage 服务（默认使用 COS）
            storage = await SmartFactory.get_service("storage", provider="cos")
            object_key = _build_avatar_key(user.id, content_type)
            storage.upload_file(object_key, file_path, content_type)
            if user.avatar_url != object_key:
                user.avatar_url = object_key
                await db.commit()
        except Exception as exc:
            logger.warning("avatar upload failed: %s", exc)
        finally:
            Path(file_path).unlink(missing_ok=True)
