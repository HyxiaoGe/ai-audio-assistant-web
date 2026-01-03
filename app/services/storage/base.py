from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class StorageService(ABC):
    """存储服务抽象基类

    定义了云存储服务的标准接口，所有存储厂商实现都必须遵循此接口。
    """

    @property
    @abstractmethod
    def provider(self) -> str:
        """厂商名称

        Returns:
            厂商标识，如 "cos", "oss", "s3", "minio"
        """
        raise NotImplementedError

    @abstractmethod
    def presign_put_object(self, object_name: str, expires_in: int) -> str:
        """生成上传签名 URL

        Args:
            object_name: 对象名称（路径）
            expires_in: 过期时间（秒）

        Returns:
            预签名上传 URL

        Raises:
            Exception: 生成失败
        """
        raise NotImplementedError

    @abstractmethod
    def generate_presigned_url(self, object_name: str, expires_in: int) -> str:
        """生成下载签名 URL

        Args:
            object_name: 对象名称（路径）
            expires_in: 过期时间（秒）

        Returns:
            预签名下载 URL

        Raises:
            Exception: 生成失败
        """
        raise NotImplementedError

    @abstractmethod
    def upload_file(
        self, object_name: str, file_path: str, content_type: Optional[str] = None
    ) -> None:
        """上传文件

        Args:
            object_name: 对象名称（路径）
            file_path: 本地文件路径
            content_type: 内容类型（如 "audio/wav"），None 则自动检测

        Raises:
            Exception: 上传失败
        """
        raise NotImplementedError

    @abstractmethod
    def delete_file(self, object_name: str) -> None:
        """删除文件

        Args:
            object_name: 对象名称（路径）

        Raises:
            NotImplementedError: 暂未实现（骨架方法）
        """
        raise NotImplementedError("删除文件功能待实现")

    @abstractmethod
    def file_exists(self, object_name: str) -> bool:
        """检查文件是否存在

        Args:
            object_name: 对象名称（路径）

        Returns:
            True: 文件存在
            False: 文件不存在

        Raises:
            NotImplementedError: 暂未实现（骨架方法）
        """
        raise NotImplementedError("检查文件存在功能待实现")

    @abstractmethod
    def get_file_info(self, object_name: str) -> Dict[str, Any]:
        """获取文件元数据

        Args:
            object_name: 对象名称（路径）

        Returns:
            文件元数据字典：
            {
                "size": 文件大小（字节）,
                "content_type": 内容类型,
                "last_modified": 最后修改时间,
                "etag": ETag,
                ...
            }

        Raises:
            NotImplementedError: 暂未实现（骨架方法）
        """
        raise NotImplementedError("获取文件信息功能待实现")

    @abstractmethod
    def list_files(self, prefix: str = "", limit: int = 1000) -> List[str]:
        """列出文件

        Args:
            prefix: 前缀过滤（如 "users/123/"）
            limit: 最大返回数量

        Returns:
            文件名列表

        Raises:
            NotImplementedError: 暂未实现（骨架方法）
        """
        raise NotImplementedError("列出文件功能待实现")

    @abstractmethod
    def copy_file(self, src: str, dst: str) -> None:
        """复制文件

        Args:
            src: 源对象名称
            dst: 目标对象名称

        Raises:
            NotImplementedError: 暂未实现（骨架方法）
        """
        raise NotImplementedError("复制文件功能待实现")

    @abstractmethod
    def move_file(self, src: str, dst: str) -> None:
        """移动文件（复制后删除源文件）

        Args:
            src: 源对象名称
            dst: 目标对象名称

        Raises:
            NotImplementedError: 暂未实现（骨架方法）
        """
        raise NotImplementedError("移动文件功能待实现")

    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查

        检查服务是否可用（如 bucket 是否存在、权限是否正确等）

        Returns:
            True: 服务健康
            False: 服务不可用

        Raises:
            NotImplementedError: 暂未实现（骨架方法）
        """
        raise NotImplementedError("健康检查功能待实现")

    @abstractmethod
    def estimate_cost(self, storage_gb: float, requests: int) -> float:
        """估算成本

        Args:
            storage_gb: 存储量（GB）
            requests: 请求次数

        Returns:
            预估成本（单位：元/月）

        Raises:
            NotImplementedError: 暂未实现（骨架方法）
        """
        raise NotImplementedError("成本估算功能待实现")
