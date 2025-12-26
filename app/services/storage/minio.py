from __future__ import annotations

from datetime import timedelta

from minio import Minio

from app.config import settings
from app.services.storage.base import StorageService


class MinioStorageService(StorageService):
    def __init__(self) -> None:
        endpoint = settings.MINIO_ENDPOINT
        access_key = settings.MINIO_ACCESS_KEY
        secret_key = settings.MINIO_SECRET_KEY
        use_ssl = settings.MINIO_USE_SSL
        if not endpoint or not access_key or not secret_key or use_ssl is None:
            raise RuntimeError("MinIO settings are not set")
        self._client = Minio(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=bool(use_ssl),
        )

    def presign_put_object(self, object_name: str, expires_in: int) -> str:
        bucket = settings.MINIO_BUCKET
        if not bucket:
            raise RuntimeError("MINIO_BUCKET is not set")
        return self._client.presigned_put_object(
            bucket_name=bucket,
            object_name=object_name,
            expires=timedelta(seconds=expires_in),
        )

    def generate_presigned_url(self, object_name: str, expires_in: int) -> str:
        bucket = settings.MINIO_BUCKET
        if not bucket:
            raise RuntimeError("MINIO_BUCKET is not set")
        return self._client.presigned_get_object(
            bucket_name=bucket,
            object_name=object_name,
            expires=timedelta(seconds=expires_in),
        )
