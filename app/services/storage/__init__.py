from __future__ import annotations

from app.services.storage.base import StorageService
from app.services.storage.minio import MinioStorageService
from app.services.storage.oss import OSSStorageService
from app.services.storage.tos import TOSStorageService

__all__ = ["StorageService", "MinioStorageService", "OSSStorageService", "TOSStorageService"]
