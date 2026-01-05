from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Dict, List

import tos
from tos.exceptions import TosClientError, TosServerError

from app.config import settings
from app.core.fault_tolerance import RetryConfig, retry
from app.core.monitoring import monitor
from app.core.registry import ServiceMetadata, register_service
from app.services.storage.base import StorageService


@register_service(
    "storage",
    "tos",
    metadata=ServiceMetadata(
        name="tos",
        service_type="storage",
        priority=20,
        description="火山引擎 TOS 对象存储服务",
        display_name="火山引擎 TOS",
        cost_per_million_tokens=0.12,  # 约 0.12 元/GB/月（标准存储）
        rate_limit=2000,
    ),
)
class TOSStorageService(StorageService):
    @property
    def provider(self) -> str:
        return "tos"

    def __init__(self) -> None:
        access_key = settings.TOS_ACCESS_KEY
        secret_key = settings.TOS_SECRET_KEY
        endpoint = settings.TOS_ENDPOINT
        region = settings.TOS_REGION
        bucket = settings.TOS_BUCKET

        if not access_key or not secret_key or not endpoint or not region or not bucket:
            raise RuntimeError(
                "TOS settings are not set (TOS_ACCESS_KEY/TOS_SECRET_KEY/"
                "TOS_ENDPOINT/TOS_REGION/TOS_BUCKET)"
            )

        self._bucket = bucket
        self._client = tos.TosClientV2(access_key, secret_key, endpoint, region)

    @retry(
        RetryConfig(max_attempts=3, initial_delay=0.5, max_delay=5.0),
        exceptions=(TosClientError, TosServerError, ConnectionError, TimeoutError),
    )
    def presign_put_object(self, object_name: str, expires_in: int) -> str:
        output = self._client.pre_signed_url(
            tos.HttpMethodType.Http_Method_Put,
            bucket=self._bucket,
            key=object_name,
            expires=expires_in,
        )
        return output.signed_url

    @monitor("storage", "tos")
    @retry(
        RetryConfig(max_attempts=3, initial_delay=0.5, max_delay=5.0),
        exceptions=(TosClientError, TosServerError, ConnectionError, TimeoutError),
    )
    def generate_presigned_url(self, object_name: str, expires_in: int) -> str:
        output = self._client.pre_signed_url(
            tos.HttpMethodType.Http_Method_Get,
            bucket=self._bucket,
            key=object_name,
            expires=expires_in,
        )
        return output.signed_url

    @monitor("storage", "tos")
    @retry(
        RetryConfig(max_attempts=3, initial_delay=1.0, max_delay=10.0),
        exceptions=(TosClientError, TosServerError, ConnectionError, TimeoutError),
    )
    def upload_file(
        self, object_name: str, file_path: str, content_type: str | None = None
    ) -> None:
        resolved_type = content_type or mimetypes.guess_type(file_path)[0]
        size_bytes = Path(file_path).stat().st_size
        self._client.put_object_from_file(
            bucket=self._bucket,
            key=object_name,
            file_path=file_path,
            content_length=size_bytes,
            content_type=resolved_type or "application/octet-stream",
        )

    @retry(
        RetryConfig(max_attempts=3, initial_delay=0.5, max_delay=5.0),
        exceptions=(TosClientError, TosServerError, ConnectionError, TimeoutError),
    )
    def delete_file(self, object_name: str) -> None:
        self._client.delete_object(self._bucket, object_name)

    def file_exists(self, object_name: str) -> bool:
        try:
            self._client.head_object(self._bucket, object_name)
            return True
        except TosServerError as exc:
            if exc.status_code == 404:
                return False
            raise

    def get_file_info(self, object_name: str) -> Dict[str, Any]:
        info = self._client.head_object(self._bucket, object_name)
        return {
            "object_name": object_name,
            "size": info.content_length,
            "etag": info.etag,
            "content_type": info.content_type,
            "last_modified": info.last_modified.isoformat() if info.last_modified else None,
            "metadata": info.meta,
        }

    def list_files(self, prefix: str = "", limit: int = 1000) -> List[str]:
        listed = self._client.list_objects(self._bucket, prefix=prefix, max_keys=limit)
        return [item.key for item in listed.contents]

    @retry(
        RetryConfig(max_attempts=3, initial_delay=0.5, max_delay=5.0),
        exceptions=(TosClientError, TosServerError, ConnectionError, TimeoutError),
    )
    def copy_file(self, src: str, dst: str) -> None:
        self._client.copy_object(self._bucket, dst, self._bucket, src)

    @retry(
        RetryConfig(max_attempts=3, initial_delay=0.5, max_delay=5.0),
        exceptions=(TosClientError, TosServerError, ConnectionError, TimeoutError),
    )
    def move_file(self, src: str, dst: str) -> None:
        self.copy_file(src, dst)
        self.delete_file(src)

    async def health_check(self) -> bool:
        try:
            return bool(self._client and self._bucket)
        except Exception:
            return False

    def estimate_cost(self, storage_gb: float, requests: int) -> float:
        storage_cost_per_gb = 0.12
        request_cost_per_10k = 0.01
        storage_cost = storage_gb * storage_cost_per_gb
        requests_cost = (requests / 10000.0) * request_cost_per_10k
        return storage_cost + requests_cost
