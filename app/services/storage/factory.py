from __future__ import annotations

from app.services.storage.base import StorageService
from app.services.storage.cos import COSStorageService
from app.services.storage.minio import MinioStorageService
from app.config import settings


def get_storage_service() -> StorageService:
    provider = settings.STORAGE_PROVIDER or "minio"
    if provider == "cos":
        return COSStorageService()
    return MinioStorageService()
