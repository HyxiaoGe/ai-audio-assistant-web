"""LLM 服务配置 Schema

目前只有两个 LLM provider：`proxy`（统一走 LiteLLM）和 `image_service`
（Gemini 系生图）。proxy 直接读 settings，不需要注册 schema；image_service
注册 schema 是为了让 `ConfigManager.get_config("llm", "image_service")`
不抛 ValueError，从而走通 SmartFactory 的 user-config 路径（即使当前只用
环境变量，未来通过 config-center DB 配置 endpoint 也能直接复用）。
"""

from __future__ import annotations

from pydantic import Field

from app.core.config_manager import ServiceConfig, register_config_schema


@register_config_schema("llm", "image_service")
class ImageServiceConfig(ServiceConfig):
    """远程 image-service（Gemini 系生图）配置"""

    base_url: str = Field(..., description="image-service 根 URL", min_length=1)
    api_key: str | None = Field(default=None, description="Bearer Token，可空")
    default_model: str = Field(
        default="gemini-3-pro-image-preview",
        description="未指定 model_id 时使用的模型",
    )
