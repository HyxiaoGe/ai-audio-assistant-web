"""LLM 服务配置 Schema

定义 LLM 系服务的配置结构。其它 LLM provider（doubao/deepseek/qwen/moonshot/
openrouter/proxy）暂未在此注册 schema —— 它们直接走 `_CONFIG_MAPPING` →
settings 的兜底路径，配置中心也不需要给它们写库。

`image_service` 单独注册的原因：避免 `ConfigManager.get_config("llm",
"image_service")` 抛 ValueError，让 SmartFactory 的 user-config 路径能正常工作
（虽然当前只用环境变量，未来如果通过 config-center DB 配置远端 image-service
endpoint，也能直接复用）。
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
