from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Dict, List

from qcloud_cos import CosConfig, CosS3Client
from qcloud_cos.cos_exception import CosServiceError

from app.config import settings
from app.core.fault_tolerance import RetryConfig, retry
from app.core.monitoring import monitor
from app.core.registry import ServiceMetadata, register_service
from app.services.storage.base import StorageService


@register_service(
    "storage",
    "cos",
    metadata=ServiceMetadata(
        name="cos",
        service_type="storage",
        priority=10,
        description="腾讯云 COS 存储服务",
    ),
)
class COSStorageService(StorageService):
    @property
    def provider(self) -> str:
        return "cos"

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

    @retry(
        RetryConfig(max_attempts=3, initial_delay=0.5, max_delay=5.0),
        exceptions=(CosServiceError, ConnectionError, TimeoutError),
    )
    def presign_put_object(self, object_name: str, expires_in: int) -> str:
        return self._client.get_presigned_url(
            Method="PUT",
            Bucket=self._bucket,
            Key=object_name,
            Expired=expires_in,
        )

    @monitor("storage", "cos")
    @retry(
        RetryConfig(max_attempts=3, initial_delay=0.5, max_delay=5.0),
        exceptions=(CosServiceError, ConnectionError, TimeoutError),
    )
    def generate_presigned_url(self, object_name: str, expires_in: int) -> str:
        # 使用公开 URL（bucket 是 public-read，不需要签名）
        if settings.COS_PUBLIC_READ:
            return f"{self._scheme}://{self._bucket}.cos.{self._region}.myqcloud.com/{object_name}"
        return self._client.get_presigned_url(
            Method="GET",
            Bucket=self._bucket,
            Key=object_name,
            Expired=expires_in,
        )

    @retry(
        RetryConfig(max_attempts=3, initial_delay=0.5, max_delay=5.0),
        exceptions=(CosServiceError, ConnectionError, TimeoutError),
    )
    def generate_internal_url(self, object_name: str) -> str:
        """
        生成COS内网访问URL（带签名），用于腾讯云内部服务（如ASR）访问

        ASR服务需要通过签名验证访问COS，即使bucket是public-read
        使用内网域名可以提高访问速度
        """
        # 生成内网域名的预签名URL（有效期1小时）
        return self._client.get_presigned_url(
            Method="GET",
            Bucket=self._bucket,
            Key=object_name,
            Expired=3600,
            Params={"domain": f"{self._bucket}.cos-internal.{self._region}.myqcloud.com"},
        )

    @monitor("storage", "cos")
    @retry(
        RetryConfig(max_attempts=3, initial_delay=1.0, max_delay=10.0),
        exceptions=(CosServiceError, ConnectionError, TimeoutError),
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

    @retry(
        RetryConfig(max_attempts=3, initial_delay=0.5, max_delay=5.0),
        exceptions=(CosServiceError, ConnectionError, TimeoutError),
    )
    def delete_file(self, object_name: str) -> None:
        """删除文件

        Args:
            object_name: 对象名称（文件路径）
        """
        self._client.delete_object(Bucket=self._bucket, Key=object_name)

    def file_exists(self, object_name: str) -> bool:
        """检查文件是否存在

        Args:
            object_name: 对象名称（文件路径）

        Returns:
            True 如果文件存在，否则 False
        """
        try:
            self._client.head_object(Bucket=self._bucket, Key=object_name)
            return True
        except CosServiceError as e:
            if e.get_status_code() == 404:
                return False
            raise

    def get_file_info(self, object_name: str) -> Dict[str, Any]:
        """获取文件元数据

        Args:
            object_name: 对象名称（文件路径）

        Returns:
            文件元数据字典
        """
        response = self._client.head_object(Bucket=self._bucket, Key=object_name)
        return {
            "object_name": object_name,
            "size": int(response.get("Content-Length", 0)),
            "etag": response.get("ETag", "").strip('"'),
            "content_type": response.get("Content-Type", ""),
            "last_modified": response.get("Last-Modified", ""),
            "metadata": response.get("x-cos-meta", {}),
        }

    def list_files(self, prefix: str = "", limit: int = 1000) -> List[str]:
        """列出文件

        Args:
            prefix: 前缀过滤
            limit: 最大返回数量

        Returns:
            文件名列表
        """
        result: list[str] = []
        marker = ""

        while len(result) < limit:
            response = self._client.list_objects(
                Bucket=self._bucket,
                Prefix=prefix,
                Marker=marker,
                MaxKeys=min(limit - len(result), 1000),
            )

            contents = response.get("Contents", [])
            if not contents:
                break

            for item in contents:
                result.append(item["Key"])
                if len(result) >= limit:
                    break

            # 检查是否还有更多对象
            if response.get("IsTruncated") == "false":
                break
            marker = response.get("NextMarker", "")

        return result

    @retry(
        RetryConfig(max_attempts=3, initial_delay=0.5, max_delay=5.0),
        exceptions=(CosServiceError, ConnectionError, TimeoutError),
    )
    def copy_file(self, src: str, dst: str) -> None:
        """复制文件

        Args:
            src: 源文件路径
            dst: 目标文件路径
        """
        copy_source = {
            "Bucket": self._bucket,
            "Key": src,
            "Region": self._region,
        }
        self._client.copy_object(
            Bucket=self._bucket,
            Key=dst,
            CopySource=copy_source,
        )

    @retry(
        RetryConfig(max_attempts=3, initial_delay=0.5, max_delay=5.0),
        exceptions=(CosServiceError, ConnectionError, TimeoutError),
    )
    def move_file(self, src: str, dst: str) -> None:
        """移动文件（复制后删除源文件）

        Args:
            src: 源文件路径
            dst: 目标文件路径
        """
        self.copy_file(src, dst)
        self.delete_file(src)

    async def health_check(self) -> bool:
        """健康检查：验证 COS 服务配置是否正确

        Returns:
            True 如果服务健康，否则 False
        """
        try:
            # 检查必要的配置是否存在
            if not self._bucket or not self._region:
                return False
            if not self._client:
                return False
            return True
        except Exception:
            return False

    def estimate_cost(self, storage_gb: float, requests: int) -> float:
        """估算成本（人民币元）

        腾讯云 COS 标准存储定价（2024 年参考）：
        - 存储费用: ¥0.118/GB/月
        - 请求费用: ¥0.01/万次（读写请求）

        Args:
            storage_gb: 存储量（GB）
            requests: 请求次数

        Returns:
            估算成本（人民币元/月）
        """
        # 价格（元）
        storage_cost_per_gb = 0.118  # 标准存储
        request_cost_per_10k = 0.01  # 每万次请求

        storage_cost = storage_gb * storage_cost_per_gb
        requests_cost = (requests / 10000) * request_cost_per_10k

        return storage_cost + requests_cost
