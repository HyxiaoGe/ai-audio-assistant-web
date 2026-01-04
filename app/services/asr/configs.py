"""ASR 服务配置 Schema

定义各 ASR 厂商的配置结构，使用 Pydantic 进行严格的类型验证。
"""

from __future__ import annotations

from pydantic import Field

from app.core.config_manager import ServiceConfig, register_config_schema


@register_config_schema("asr", "tencent")
class TencentASRConfig(ServiceConfig):
    """腾讯云 ASR 服务配置

    Attributes:
        secret_id: 腾讯云 Secret ID
        secret_key: 腾讯云 Secret Key
        region: 地域（如 "ap-guangzhou"）
        engine_model_type: 引擎模型类型（如 "16k_zh"）
        channel_num: 声道数（1 或 2）
        res_text_format: 结果文本格式（0-3）
        speaker_dia: 是否开启说话人分离（0 或 1）
        speaker_number: 说话人数量（0-10）
        poll_interval: 轮询间隔（秒）
        max_wait: 最大等待时间（秒）
    """

    secret_id: str = Field(..., description="腾讯云 Secret ID", min_length=1)
    secret_key: str = Field(..., description="腾讯云 Secret Key", min_length=1)
    region: str = Field(..., description="地域", min_length=1)
    engine_model_type: str = Field(
        default="16k_zh",
        description="引擎模型类型",
    )
    channel_num: int = Field(default=1, description="声道数", ge=1, le=2)
    res_text_format: int = Field(default=0, description="结果文本格式", ge=0, le=3)
    speaker_dia: int = Field(default=0, description="是否开启说话人分离", ge=0, le=1)
    speaker_number: int = Field(default=0, description="说话人数量", ge=0, le=10)
    poll_interval: int = Field(default=5, description="轮询间隔（秒）", ge=1, le=60)
    max_wait: int = Field(default=3600, description="最大等待时间（秒）", ge=60)

    class Config:
        """Pydantic 配置"""

        schema_extra = {
            "example": {
                "secret_id": "your-tencent-secret-id",
                "secret_key": "your-tencent-secret-key",
                "region": "ap-guangzhou",
                "engine_model_type": "16k_zh",
                "channel_num": 1,
                "res_text_format": 0,
                "speaker_dia": 1,
                "speaker_number": 2,
                "poll_interval": 5,
                "max_wait": 3600,
                "enabled": True,
                "timeout": 30,
                "retry_count": 3,
            }
        }


@register_config_schema("asr", "aliyun")
class AliyunASRConfig(ServiceConfig):
    """阿里云 ASR 服务配置

    Attributes:
        access_key_id: 阿里云 Access Key ID
        access_key_secret: 阿里云 Access Key Secret
        region: 地域（如 "cn-shanghai"）
        app_key: 应用 Key（可选）
    """

    access_key_id: str = Field(..., description="阿里云 Access Key ID", min_length=1)
    access_key_secret: str = Field(..., description="阿里云 Access Key Secret", min_length=1)
    region: str = Field(default="cn-shanghai", description="地域")
    app_key: str = Field(default="", description="应用 Key（可选）")

    class Config:
        """Pydantic 配置"""

        schema_extra = {
            "example": {
                "access_key_id": "your-aliyun-access-key-id",
                "access_key_secret": "your-aliyun-access-key-secret",
                "region": "cn-shanghai",
                "app_key": "your-app-key",
                "enabled": True,
                "timeout": 30,
                "retry_count": 3,
            }
        }
