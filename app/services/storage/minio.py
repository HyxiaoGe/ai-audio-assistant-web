from __future__ import annotations

from datetime import timedelta
import mimetypes
from typing import Any, Dict, List

from minio import Minio
from minio.error import S3Error

from app.config import settings
from app.core.fault_tolerance import RetryConfig, retry
from app.core.monitoring import monitor
from app.core.registry import ServiceMetadata, register_service
from app.services.storage.base import StorageService


@register_service(
    "storage",
    "minio",
    metadata=ServiceMetadata(
        name="minio",
        service_type="storage",
        priority=20,
        description="MinIO 对象存储服务",
    ),
)
class MinioStorageService(StorageService):
    @property
    def provider(self) -> str:
        return "minio"

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

    @retry(
        RetryConfig(max_attempts=3, initial_delay=0.5, max_delay=5.0),
        exceptions=(S3Error, ConnectionError, TimeoutError),
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

    @monitor("storage", "minio")
    @retry(
        RetryConfig(max_attempts=3, initial_delay=0.5, max_delay=5.0),
        exceptions=(S3Error, ConnectionError, TimeoutError),
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

    @monitor("storage", "minio")
    @retry(
        RetryConfig(max_attempts=3, initial_delay=1.0, max_delay=10.0),
        exceptions=(S3Error, ConnectionError, TimeoutError),
    )
    def upload_file(
        self, object_name: str, file_path: str, content_type: str | None = None
    ) -> None:
        bucket = settings.MINIO_BUCKET
        if not bucket:
            raise RuntimeError("MINIO_BUCKET is not set")
        resolved_type = content_type or mimetypes.guess_type(file_path)[0]
        self._client.fput_object(
            bucket_name=bucket,
            object_name=object_name,
            file_path=file_path,
            content_type=resolved_type or "application/octet-stream",
        )

    @retry(
        RetryConfig(max_attempts=3, initial_delay=0.5, max_delay=5.0),
        exceptions=(S3Error, ConnectionError, TimeoutError),
    )
    def delete_file(self, object_name: str) -> None:
        """删除文件

        Args:
            object_name: 对象名称（文件路径）
        """
        bucket = settings.MINIO_BUCKET
        if not bucket:
            raise RuntimeError("MINIO_BUCKET is not set")
        self._client.remove_object(bucket_name=bucket, object_name=object_name)

    def file_exists(self, object_name: str) -> bool:
        """检查文件是否存在

        Args:
            object_name: 对象名称（文件路径）

        Returns:
            True 如果文件存在，否则 False
        """
        bucket = settings.MINIO_BUCKET
        if not bucket:
            raise RuntimeError("MINIO_BUCKET is not set")
        try:
            self._client.stat_object(bucket_name=bucket, object_name=object_name)
            return True
        except S3Error as e:
            if e.code == "NoSuchKey":
                return False
            raise

    def get_file_info(self, object_name: str) -> Dict[str, Any]:
        """获取文件元数据

        Args:
            object_name: 对象名称（文件路径）

        Returns:
            文件元数据字典
        """
        bucket = settings.MINIO_BUCKET
        if not bucket:
            raise RuntimeError("MINIO_BUCKET is not set")
        stat = self._client.stat_object(bucket_name=bucket, object_name=object_name)
        return {
            "object_name": stat.object_name,
            "size": stat.size,
            "etag": stat.etag,
            "content_type": stat.content_type,
            "last_modified": stat.last_modified.isoformat() if stat.last_modified else None,
            "metadata": stat.metadata,
        }

    def list_files(self, prefix: str = "", limit: int = 1000) -> List[str]:
        """列出文件

        Args:
            prefix: 前缀过滤
            limit: 最大返回数量

        Returns:
            文件名列表
        """
        bucket = settings.MINIO_BUCKET
        if not bucket:
            raise RuntimeError("MINIO_BUCKET is not set")
        objects = self._client.list_objects(
            bucket_name=bucket,
            prefix=prefix,
            recursive=True,
        )
        result = []
        for obj in objects:
            result.append(obj.object_name)
            if len(result) >= limit:
                break
        return result

    @retry(
        RetryConfig(max_attempts=3, initial_delay=0.5, max_delay=5.0),
        exceptions=(S3Error, ConnectionError, TimeoutError),
    )
    def copy_file(self, src: str, dst: str) -> None:
        """复制文件

        Args:
            src: 源文件路径
            dst: 目标文件路径
        """
        bucket = settings.MINIO_BUCKET
        if not bucket:
            raise RuntimeError("MINIO_BUCKET is not set")
        from minio.commonconfig import CopySource
        self._client.copy_object(
            bucket_name=bucket,
            object_name=dst,
            source=CopySource(bucket, src),
        )

    @retry(
        RetryConfig(max_attempts=3, initial_delay=0.5, max_delay=5.0),
        exceptions=(S3Error, ConnectionError, TimeoutError),
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
        """健康检查：验证 MinIO 服务配置是否正确

        Returns:
            True 如果服务健康，否则 False
        """
        try:
            # 检查必要的配置是否存在
            if not self._client:
                return False
            # 可以尝试列出 buckets 来验证连接
            # 但为了避免实际网络调用，这里只检查客户端是否已初始化
            return True
        except Exception:
            return False

    def estimate_cost(self, storage_gb: float, requests: int) -> float:
        """估算成本（人民币元）

        MinIO 是自建对象存储，成本主要是硬件和运维成本
        这里提供一个简化的估算模型

        Args:
            storage_gb: 存储量（GB）
            requests: 请求次数

        Returns:
            估算成本（人民币元/月）
        """
        # MinIO 自建成本估算（参考值）
        # 存储成本：约 0.01 元/GB/月（基于硬盘成本摊销）
        # 请求成本：忽略不计（自建服务无请求费用）
        storage_cost_per_gb = 0.01

        storage_cost = storage_gb * storage_cost_per_gb
        requests_cost = 0.0  # 自建服务无请求费用

        return storage_cost + requests_cost
