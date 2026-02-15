from __future__ import annotations

from app.services.storage import configs as _configs  # noqa: F401
from app.services.storage.base import StorageService
from app.services.storage.cos import COSStorageService
from app.services.storage.minio import MinioStorageService
from app.services.storage.oss import OSSStorageService
from app.services.storage.tos import TOSStorageService

__all__ = [
    "StorageService",
    "COSStorageService",
    "MinioStorageService",
    "OSSStorageService",
    "TOSStorageService",
]
