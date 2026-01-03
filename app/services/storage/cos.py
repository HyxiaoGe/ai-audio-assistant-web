from __future__ import annotations

import mimetypes
from pathlib import Path

from qcloud_cos import CosConfig, CosS3Client

from app.config import settings
from app.services.storage.base import StorageService


class COSStorageService(StorageService):
    def __init__(self) -> None:
        region = settings.COS_REGION
        bucket = settings.COS_BUCKET
        secret_id = settings.COS_SECRET_ID or settings.TENCENT_SECRET_ID
        secret_key = settings.COS_SECRET_KEY or settings.TENCENT_SECRET_KEY
        use_ssl = settings.COS_USE_SSL

        if not region or not bucket or not secret_id or not secret_key:
            raise RuntimeError("COS settings are not set")

        self._bucket = bucket
        self._region = region
        self._scheme = "https" if use_ssl is not False else "http"
        config = CosConfig(
            Region=region,
            SecretId=secret_id,
            SecretKey=secret_key,
            Scheme=self._scheme,
        )
        self._client = CosS3Client(config)

    def presign_put_object(self, object_name: str, expires_in: int) -> str:
        return self._client.get_presigned_url(
            Method="PUT",
            Bucket=self._bucket,
            Key=object_name,
            Expired=expires_in,
        )

    def generate_presigned_url(self, object_name: str, expires_in: int) -> str:
        if settings.COS_PUBLIC_READ:
            return f"{self._scheme}://{self._bucket}.cos.{self._region}.myqcloud.com/{object_name}"
        return self._client.get_presigned_url(
            Method="GET",
            Bucket=self._bucket,
            Key=object_name,
            Expired=expires_in,
        )

    def upload_file(
        self, object_name: str, file_path: str, content_type: str | None = None
    ) -> None:
        resolved_type = content_type or mimetypes.guess_type(file_path)[0]
        content_length = str(Path(file_path).stat().st_size)
        with open(file_path, "rb") as handle:
            self._client.put_object(
                Bucket=self._bucket,
                Key=object_name,
                Body=handle,
                ContentLength=content_length,
                ContentType=resolved_type or "application/octet-stream",
                ACL="public-read" if settings.COS_PUBLIC_READ else "private",
            )
