from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.config import settings
from app.core.exceptions import BusinessError
from app.core.response import success
from app.core.smart_factory import SmartFactory
from app.i18n.codes import ErrorCode
from app.models.task import Task
from app.models.user import User
from app.schemas.upload import UploadPresignRequest

router = APIRouter(prefix="/upload")


def _get_allowed_extensions() -> list[str]:
    raw = settings.UPLOAD_ALLOWED_EXTENSIONS
    if not raw:
        raise RuntimeError("UPLOAD_ALLOWED_EXTENSIONS is not set")
    items = [item.strip().lower() for item in raw.split(",") if item.strip()]
    if not items:
        raise RuntimeError("UPLOAD_ALLOWED_EXTENSIONS is empty")
    return items


def _get_max_size_bytes() -> int:
    max_size = settings.UPLOAD_MAX_SIZE_BYTES
    if max_size is None:
        raise RuntimeError("UPLOAD_MAX_SIZE_BYTES is not set")
    if max_size <= 0:
        raise RuntimeError("UPLOAD_MAX_SIZE_BYTES must be positive")
    return max_size


def _get_presign_expires() -> int:
    expires = settings.UPLOAD_PRESIGN_EXPIRES
    if expires is None:
        raise RuntimeError("UPLOAD_PRESIGN_EXPIRES is not set")
    if expires <= 0:
        raise RuntimeError("UPLOAD_PRESIGN_EXPIRES must be positive")
    return expires


def _format_size_bytes(size_bytes: int) -> str:
    size_mb = max(1, size_bytes // (1024 * 1024))
    return f"{size_mb}MB"


def _build_file_key(filename: str, user_id: str) -> str:
    now = datetime.now(timezone.utc)
    ext = Path(filename).suffix.lower()
    file_id = uuid4().hex
    return f"upload/{user_id}/{now:%Y/%m/%d}/{file_id}{ext}"


async def _build_upload_url(file_key: str, expires_in: int) -> str:
    # 使用 SmartFactory 获取 storage 服务（默认使用 COS）
    storage = await SmartFactory.get_service("storage", provider="cos")
    return storage.presign_put_object(file_key, expires_in)


def _ensure_extension_allowed(filename: str, allowed: Iterable[str]) -> None:
    if "." not in filename:
        raise BusinessError(ErrorCode.UNSUPPORTED_FILE_FORMAT, allowed=", ".join(allowed))
    ext = filename.rsplit(".", 1)[-1].lower()
    if ext not in allowed:
        raise BusinessError(ErrorCode.UNSUPPORTED_FILE_FORMAT, allowed=", ".join(allowed))


@router.post("/presign")
async def presign_upload(
    data: UploadPresignRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    allowed = _get_allowed_extensions()
    _ensure_extension_allowed(data.filename, allowed)

    max_size = _get_max_size_bytes()
    if data.size_bytes > max_size:
        raise BusinessError(ErrorCode.FILE_TOO_LARGE, max_size=_format_size_bytes(max_size))

    existing = await db.execute(
        select(Task.id).where(
            Task.content_hash == data.content_hash,
            Task.user_id == user.id,
            Task.deleted_at.is_(None),
        )
    )
    task_id = existing.scalar_one_or_none()
    if task_id:
        return success(data={"exists": True, "task_id": task_id})

    expires_in = _get_presign_expires()
    file_key = _build_file_key(data.filename, user.id)
    upload_url = await _build_upload_url(file_key, expires_in)

    return success(
        data={
            "exists": False,
            "upload_url": upload_url,
            "file_key": file_key,
            "expires_in": expires_in,
        }
    )
