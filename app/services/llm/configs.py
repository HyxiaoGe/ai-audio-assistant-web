"""LLM 服务配置 Schema

定义各 LLM 厂商的配置结构，使用 Pydantic 进行严格的类型验证。
"""

from __future__ import annotations

from typing import Optional

from pydantic import Field, validator

from app.core.config_manager import ServiceConfig, register_config_schema


@register_config_schema("llm", "doubao")
class DoubaoConfig(ServiceConfig):
    """豆包 LLM 服务配置

    Attributes:
        api_key: API 密钥
        base_url: API 基础 URL
        model: 模型名称（如 "doubao-1.5-pro-32k-250115"）
        max_tokens: 最大 token 数量
        temperature: 温度参数（0.0-2.0），控制随机性
        top_p: 核采样参数（0.0-1.0）
    """

    api_key: str = Field(..., description="Doubao API 密钥", min_length=1)
    base_url: str = Field(..., description="Doubao API 基础 URL")
    model: str = Field(..., description="模型名称", min_length=1)
    max_tokens: int = Field(default=2000, description="最大 token 数", ge=1, le=32000)
    temperature: float = Field(default=0.7, description="温度参数", ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, description="核采样参数", ge=0.0, le=1.0)

    @validator("base_url")
    def validate_base_url(cls, v: str) -> str:
        """验证 base_url 格式"""
        if not v.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return v.rstrip("/")

    class Config:
        """Pydantic 配置"""

        schema_extra = {
            "example": {
                "api_key": "your-doubao-api-key",
                "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                "model": "doubao-1.5-pro-32k-250115",
                "max_tokens": 2000,
                "temperature": 0.7,
                "top_p": 1.0,
                "enabled": True,
                "timeout": 60,
                "retry_count": 3,
            }
        }


@register_config_schema("llm", "qwen")
class QwenConfig(ServiceConfig):
    """千问 LLM 服务配置

    Attributes:
        api_key: API 密钥
        model: 模型名称（如 "qwen-turbo", "qwen-plus"）
        max_tokens: 最大 token 数量
        temperature: 温度参数（0.0-2.0），控制随机性
        top_p: 核采样参数（0.0-1.0）
    """

    api_key: str = Field(..., description="Qwen API 密钥", min_length=1)
    model: str = Field(..., description="模型名称", min_length=1)
    max_tokens: Optional[int] = Field(default=1500, description="最大 token 数", ge=1)
    temperature: float = Field(default=0.7, description="温度参数", ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, description="核采样参数", ge=0.0, le=1.0)

    class Config:
        """Pydantic 配置"""

        schema_extra = {
            "example": {
                "api_key": "your-qwen-api-key",
                "model": "qwen-turbo",
                "max_tokens": 1500,
                "temperature": 0.7,
                "top_p": 1.0,
                "enabled": True,
                "timeout": 30,
                "retry_count": 3,
            }
        }


@register_config_schema("llm", "moonshot")
class MoonshotConfig(ServiceConfig):
    """Moonshot LLM 服务配置

    Attributes:
        api_key: API 密钥
        base_url: API 基础 URL
        model: 模型名称（moonshot-v1-8k/32k/128k）
        max_tokens: 最大 token 数量
        temperature: 温度参数（0.0-2.0）
        top_p: 核采样参数（0.0-1.0）
        timeout: 请求超时时间（秒）
    """

    api_key: str = Field(..., description="Moonshot API 密钥", min_length=1)
    base_url: str = Field(
        default="https://api.moonshot.cn/v1",
        description="Moonshot API 基础 URL",
    )
    model: str = Field(
        default="moonshot-v1-8k",
        description="模型名称",
        min_length=1,
    )
    max_tokens: int = Field(default=4096, description="最大 token 数", ge=1)
    temperature: float = Field(default=0.7, description="温度参数", ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, description="核采样参数", ge=0.0, le=1.0)
    timeout: int = Field(default=60, description="请求超时时间", gt=0)

    @validator("base_url")
    def validate_base_url(cls, v: str) -> str:
        """验证 base_url 格式"""
        if not v.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return v.rstrip("/")

    class Config:
        """Pydantic 配置"""

        schema_extra = {
            "example": {
                "api_key": "your-moonshot-api-key",
                "base_url": "https://api.moonshot.cn/v1",
                "model": "moonshot-v1-8k",
                "max_tokens": 4096,
                "temperature": 0.7,
                "top_p": 1.0,
                "timeout": 60.0,
                "enabled": True,
                "retry_count": 3,
            }
        }


@register_config_schema("llm", "openrouter")
class OpenRouterConfig(ServiceConfig):
    """OpenRouter LLM 服务配置

    Attributes:
        api_key: API 密钥
        base_url: API 基础 URL
        model: 模型名称（如 "openai/gpt-4o"）
        max_tokens: 最大 token 数量
        http_referer: 可选，用于 OpenRouter 归因的站点 URL
        app_title: 可选，用于 OpenRouter 归因的应用名称
    """

    api_key: str = Field(..., description="OpenRouter API 密钥", min_length=1)
    base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        description="OpenRouter API 基础 URL",
    )
    model: Optional[str] = Field(default=None, description="默认模型名称")
    max_tokens: int = Field(default=4096, description="最大 token 数", ge=1)
    http_referer: Optional[str] = Field(default=None, description="HTTP-Referer 头")
    app_title: Optional[str] = Field(default=None, description="X-Title 头")

    @validator("base_url")
    def validate_base_url(cls, v: str) -> str:
        """验证 base_url 格式"""
        if not v.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return v.rstrip("/")

    class Config:
        """Pydantic 配置"""

        schema_extra = {
            "example": {
                "api_key": "your-openrouter-api-key",
                "base_url": "https://openrouter.ai/api/v1",
                "model": "openai/gpt-4o",
                "max_tokens": 4096,
                "http_referer": "https://your-site.example",
                "app_title": "AI Audio Assistant",
                "enabled": True,
                "timeout": 60,
                "retry_count": 3,
            }
        }
