"""Storage 服务配置 Schema

定义各云存储厂商的配置结构，使用 Pydantic 进行严格的类型验证。
"""

from __future__ import annotations

from pydantic import Field, validator

from app.core.config_manager import ServiceConfig, register_config_schema


@register_config_schema("storage", "cos")
class COSConfig(ServiceConfig):
    """腾讯云 COS 存储服务配置

    Attributes:
        region: 地域（如 "ap-guangzhou"）
        bucket: 存储桶名称
        secret_id: 腾讯云 Secret ID
        secret_key: 腾讯云 Secret Key
        use_ssl: 是否使用 SSL（True/False）
        public_read: 是否公开读（True/False）
    """

    region: str = Field(..., description="地域", min_length=1)
    bucket: str = Field(..., description="存储桶名称", min_length=1)
    secret_id: str = Field(..., description="腾讯云 Secret ID", min_length=1)
    secret_key: str = Field(..., description="腾讯云 Secret Key", min_length=1)
    use_ssl: bool = Field(default=True, description="是否使用 SSL")
    public_read: bool = Field(default=False, description="是否公开读")

    class Config:
        """Pydantic 配置"""

        schema_extra = {
            "example": {
                "region": "ap-guangzhou",
                "bucket": "my-audio-bucket",
                "secret_id": "your-tencent-secret-id",
                "secret_key": "your-tencent-secret-key",
                "use_ssl": True,
                "public_read": False,
                "enabled": True,
                "timeout": 30,
                "retry_count": 3,
            }
        }


@register_config_schema("storage", "minio")
class MinioConfig(ServiceConfig):
    """MinIO 对象存储服务配置

    Attributes:
        endpoint: MinIO 服务端点（如 "localhost:9000"）
        access_key: Access Key
        secret_key: Secret Key
        bucket: 存储桶名称
        use_ssl: 是否使用 SSL（True/False）
    """

    endpoint: str = Field(..., description="MinIO 服务端点", min_length=1)
    access_key: str = Field(..., description="Access Key", min_length=1)
    secret_key: str = Field(..., description="Secret Key", min_length=1)
    bucket: str = Field(..., description="存储桶名称", min_length=1)
    use_ssl: bool = Field(default=False, description="是否使用 SSL")

    @validator("endpoint")
    def validate_endpoint(cls, v: str) -> str:
        """验证 endpoint 格式（不应包含协议前缀）"""
        if v.startswith(("http://", "https://")):
            raise ValueError(
                "endpoint should not include http:// or https:// prefix. "
                "Use use_ssl=True for HTTPS connections."
            )
        return v

    class Config:
        """Pydantic 配置"""

        schema_extra = {
            "example": {
                "endpoint": "localhost:9000",
                "access_key": "minioadmin",
                "secret_key": "minioadmin",
                "bucket": "audio-assistant",
                "use_ssl": False,
                "enabled": True,
                "timeout": 30,
                "retry_count": 3,
            }
        }


@register_config_schema("storage", "tos")
class TOSConfig(ServiceConfig):
    """火山引擎 TOS 存储服务配置

    Attributes:
        endpoint: TOS 服务端点（如 "tos-cn-beijing.volces.com"）
        region: 地域（如 "cn-beijing"）
        bucket: 存储桶名称
        access_key: Access Key
        secret_key: Secret Key
    """

    endpoint: str = Field(..., description="TOS 服务端点", min_length=1)
    region: str = Field(..., description="地域", min_length=1)
    bucket: str = Field(..., description="存储桶名称", min_length=1)
    access_key: str = Field(..., description="Access Key", min_length=1)
    secret_key: str = Field(..., description="Secret Key", min_length=1)

    @validator("endpoint")
    def validate_endpoint(cls, v: str) -> str:
        """验证 endpoint 格式（不应包含协议前缀）"""
        if v.startswith(("http://", "https://")):
            raise ValueError("endpoint should not include http:// or https:// prefix.")
        return v

    class Config:
        """Pydantic 配置"""

        schema_extra = {
            "example": {
                "endpoint": "tos-cn-beijing.volces.com",
                "region": "cn-beijing",
                "bucket": "my-audio-bucket",
                "access_key": "your-tos-access-key",
                "secret_key": "your-tos-secret-key",
                "enabled": True,
                "timeout": 30,
                "retry_count": 3,
            }
        }
