"""阿里云 OSS 对象存储服务实现"""

from __future__ import annotations

import mimetypes
from typing import Any, Dict, List

import oss2
from oss2.exceptions import NoSuchKey, OssError

from app.config import settings
from app.core.fault_tolerance import RetryConfig, retry
from app.core.monitoring import monitor
from app.core.registry import ServiceMetadata, register_service
from app.services.storage.base import StorageService


@register_service(
    "storage",
    "oss",
    metadata=ServiceMetadata(
        name="oss",
        service_type="storage",
        priority=15,
        description="阿里云 OSS 对象存储服务",
        display_name="阿里云 OSS",
        cost_per_million_tokens=0.12,  # 约 0.12 元/GB/月（标准存储）
        rate_limit=2000,  # 2000 req/s
    ),
)
class OSSStorageService(StorageService):
    """阿里云 OSS 对象存储服务实现

    官方文档：https://help.aliyun.com/zh/oss/developer-reference/getting-started-with-oss-sdk-for-python
    """

    @property
    def provider(self) -> str:
        return "oss"

    def __init__(self) -> None:
        endpoint = settings.OSS_ENDPOINT
        region = settings.OSS_REGION
        access_key_id = settings.ALIYUN_ACCESS_KEY_ID
        access_key_secret = settings.ALIYUN_ACCESS_KEY_SECRET
        bucket_name = settings.OSS_BUCKET
        use_ssl = settings.OSS_USE_SSL

        if not endpoint or not access_key_id or not access_key_secret or not bucket_name:
            raise RuntimeError(
                "OSS settings are not set "
                "(需要配置 OSS_ENDPOINT, OSS_BUCKET 和 ALIYUN_ACCESS_KEY_ID/SECRET)"
            )

        # 创建认证对象
        auth = oss2.Auth(access_key_id, access_key_secret)

        # 创建 Bucket 对象
        self._bucket = oss2.Bucket(auth, endpoint, bucket_name)
        self._endpoint = endpoint
        self._bucket_name = bucket_name
        self._region = region or "cn-hangzhou"
        self._use_ssl = bool(use_ssl) if use_ssl is not None else True

    @retry(
        RetryConfig(max_attempts=3, initial_delay=0.5, max_delay=5.0),
        exceptions=(OssError, ConnectionError, TimeoutError),
    )
    def presign_put_object(self, object_name: str, expires_in: int) -> str:
        """生成上传签名 URL

        Args:
            object_name: 对象名称（路径）
            expires_in: 过期时间（秒）

        Returns:
            预签名上传 URL
        """
        # 生成 PUT 方法的签名 URL
        url = self._bucket.sign_url("PUT", object_name, expires_in, slash_safe=True)
        return url

    @monitor("storage", "oss")
    @retry(
        RetryConfig(max_attempts=3, initial_delay=0.5, max_delay=5.0),
        exceptions=(OssError, ConnectionError, TimeoutError),
    )
    def generate_presigned_url(self, object_name: str, expires_in: int) -> str:
        """生成下载签名 URL

        Args:
            object_name: 对象名称（路径）
            expires_in: 过期时间（秒）

        Returns:
            预签名下载 URL
        """
        # 生成 GET 方法的签名 URL
        url = self._bucket.sign_url("GET", object_name, expires_in, slash_safe=True)
        return url

    @monitor("storage", "oss")
    @retry(
        RetryConfig(max_attempts=3, initial_delay=1.0, max_delay=10.0),
        exceptions=(OssError, ConnectionError, TimeoutError),
    )
    def upload_file(
        self, object_name: str, file_path: str, content_type: str | None = None
    ) -> None:
        """上传文件

        Args:
            object_name: 对象名称（路径）
            file_path: 本地文件路径
            content_type: 内容类型（如 "audio/wav"），None 则自动检测
        """
        resolved_type = content_type or mimetypes.guess_type(file_path)[0]

        # 设置文件元数据
        headers = {}
        if resolved_type:
            headers["Content-Type"] = resolved_type

        # 上传文件
        with open(file_path, "rb") as f:
            self._bucket.put_object(object_name, f, headers=headers)

    @retry(
        RetryConfig(max_attempts=3, initial_delay=0.5, max_delay=5.0),
        exceptions=(OssError, ConnectionError, TimeoutError),
    )
    def delete_file(self, object_name: str) -> None:
        """删除文件

        Args:
            object_name: 对象名称（文件路径）
        """
        self._bucket.delete_object(object_name)

    def file_exists(self, object_name: str) -> bool:
        """检查文件是否存在

        Args:
            object_name: 对象名称（文件路径）

        Returns:
            True 如果文件存在，否则 False
        """
        try:
            self._bucket.get_object_meta(object_name)
            return True
        except NoSuchKey:
            return False
        except OssError as e:
            # 404 表示文件不存在
            if e.status == 404:
                return False
            raise

    def get_file_info(self, object_name: str) -> Dict[str, Any]:
        """获取文件元数据

        Args:
            object_name: 对象名称（文件路径）

        Returns:
            文件元数据字典
        """
        # 获取详细的对象信息
        result = self._bucket.get_object_meta(object_name)

        return {
            "object_name": object_name,
            "size": int(result.headers.get("Content-Length", 0)),
            "etag": result.headers.get("ETag", "").strip('"'),
            "content_type": result.headers.get("Content-Type", ""),
            "last_modified": result.headers.get("Last-Modified", ""),
            "metadata": result.headers,
        }

    def list_files(self, prefix: str = "", limit: int = 1000) -> List[str]:
        """列出文件

        Args:
            prefix: 前缀过滤
            limit: 最大返回数量

        Returns:
            文件名列表
        """
        result = []

        # 使用迭代器方式列举对象
        for obj in oss2.ObjectIterator(self._bucket, prefix=prefix, max_keys=limit):
            result.append(obj.key)
            if len(result) >= limit:
                break

        return result

    @retry(
        RetryConfig(max_attempts=3, initial_delay=0.5, max_delay=5.0),
        exceptions=(OssError, ConnectionError, TimeoutError),
    )
    def copy_file(self, src: str, dst: str) -> None:
        """复制文件

        Args:
            src: 源文件路径
            dst: 目标文件路径
        """
        # 在同一个 bucket 内复制
        self._bucket.copy_object(self._bucket_name, src, dst)

    @retry(
        RetryConfig(max_attempts=3, initial_delay=0.5, max_delay=5.0),
        exceptions=(OssError, ConnectionError, TimeoutError),
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
        """健康检查：验证 OSS 服务配置是否正确

        Returns:
            True 如果服务健康，否则 False
        """
        try:
            # 检查必要的配置是否存在
            if not self._bucket:
                return False

            # 可以尝试获取 bucket 信息来验证连接
            # 但为了避免实际网络调用，这里只检查客户端是否已初始化
            return True
        except Exception:
            return False

    def estimate_cost(self, storage_gb: float, requests: int) -> float:
        """估算成本（人民币元）

        阿里云 OSS 定价（标准存储，2024 年参考）：
        - 存储费用: ¥0.12/GB/月
        - GET 请求: ¥0.01/万次
        - PUT 请求: ¥0.01/万次

        Args:
            storage_gb: 存储量（GB）
            requests: 请求次数

        Returns:
            估算成本（人民币元/月）
        """
        # 存储成本
        storage_cost_per_gb = 0.12
        storage_cost = storage_gb * storage_cost_per_gb

        # 请求成本（简化：假设一半 GET，一半 PUT）
        request_cost_per_10k = 0.01
        requests_cost = (requests / 10000) * request_cost_per_10k

        return storage_cost + requests_cost
