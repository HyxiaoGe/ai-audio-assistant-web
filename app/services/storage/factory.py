from __future__ import annotations

from app.services.storage.base import StorageService
from app.services.storage.minio import MinioStorageService


def get_storage_service() -> StorageService:
    return MinioStorageService()
