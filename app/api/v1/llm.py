"""LLM 模型管理 API"""

from __future__ import annotations

from typing import Any, cast

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.core.config_manager import ConfigManager
from app.core.health_checker import HealthChecker, HealthStatus
from app.core.registry import ServiceRegistry
from app.core.response import success
from app.core.smart_factory import SmartFactory

router = APIRouter(prefix="/llm", tags=["llm"])

# 按 provider 的显示名称国际化映射
DISPLAY_NAMES_I18N = {
    "deepseek": {"zh": "深度求索", "en": "DeepSeek"},
    "qwen": {"zh": "通义千问", "en": "Qwen"},
    "doubao": {"zh": "豆包", "en": "Doubao"},
    "moonshot": {"zh": "Kimi", "en": "Kimi"},
    "openrouter": {"zh": "OpenRouter", "en": "OpenRouter"},
}

# 按 model_id 的显示名称映射（用于 OpenRouter 等支持多模型的服务）
MODEL_DISPLAY_NAMES_I18N = {
    # OpenAI 模型（通过 OpenRouter）
    "openai/gpt-5.2-chat": {"zh": "GPT-5.2 Chat", "en": "GPT-5.2 Chat"},
    "openai/gpt-5.2-pro": {"zh": "GPT-5.2 Pro", "en": "GPT-5.2 Pro"},
    "openai/gpt-5.2": {"zh": "GPT-5.2", "en": "GPT-5.2"},
    "openai/gpt-4o": {"zh": "GPT-4o", "en": "GPT-4o"},
    "openai/gpt-4o-mini": {"zh": "GPT-4o Mini", "en": "GPT-4o Mini"},
    "openai/gpt-4-turbo": {"zh": "GPT-4 Turbo", "en": "GPT-4 Turbo"},
    "openai/o1": {"zh": "o1", "en": "o1"},
    "openai/o1-mini": {"zh": "o1 Mini", "en": "o1 Mini"},
    "openai/o1-preview": {"zh": "o1 Preview", "en": "o1 Preview"},
    # Anthropic 模型（通过 OpenRouter）
    "anthropic/claude-opus-4.5": {"zh": "Claude Opus 4.5", "en": "Claude Opus 4.5"},
    "anthropic/claude-haiku-4.5": {"zh": "Claude Haiku 4.5", "en": "Claude Haiku 4.5"},
    "anthropic/claude-sonnet-4.5": {"zh": "Claude Sonnet 4.5", "en": "Claude Sonnet 4.5"},
    "anthropic/claude-3.5-sonnet": {"zh": "Claude 3.5 Sonnet", "en": "Claude 3.5 Sonnet"},
    "anthropic/claude-3.5-sonnet:beta": {"zh": "Claude 3.5 Sonnet", "en": "Claude 3.5 Sonnet"},
    "anthropic/claude-3-5-sonnet-20241022": {"zh": "Claude 3.5 Sonnet", "en": "Claude 3.5 Sonnet"},
    "anthropic/claude-3.5-haiku": {"zh": "Claude 3.5 Haiku", "en": "Claude 3.5 Haiku"},
    "anthropic/claude-3-5-haiku-20241022": {"zh": "Claude 3.5 Haiku", "en": "Claude 3.5 Haiku"},
    "anthropic/claude-3-opus": {"zh": "Claude 3 Opus", "en": "Claude 3 Opus"},
    "anthropic/claude-3-opus-20240229": {"zh": "Claude 3 Opus", "en": "Claude 3 Opus"},
    # Google 模型（通过 OpenRouter）
    "google/gemini-3-flash-preview": {
        "zh": "Gemini 3 Flash Preview",
        "en": "Gemini 3 Flash Preview",
    },
    "google/gemini-3-pro-preview": {"zh": "Gemini 3 Pro Preview", "en": "Gemini 3 Pro Preview"},
    "google/gemini-3-pro-image-preview": {
        "zh": "Gemini 3 Pro Image Preview",
        "en": "Gemini 3 Pro Image Preview",
    },
    "google/gemini-2.0-flash-exp": {"zh": "Gemini 2.0 Flash", "en": "Gemini 2.0 Flash"},
    "google/gemini-exp-1206": {"zh": "Gemini Exp 1206", "en": "Gemini Exp 1206"},
    "google/gemini-pro-1.5": {"zh": "Gemini 1.5 Pro", "en": "Gemini 1.5 Pro"},
    "google/gemini-pro-1.5-exp": {"zh": "Gemini 1.5 Pro Exp", "en": "Gemini 1.5 Pro Exp"},
    "google/gemini-flash-1.5": {"zh": "Gemini 1.5 Flash", "en": "Gemini 1.5 Flash"},
    "google/gemini-flash-1.5-8b": {"zh": "Gemini 1.5 Flash 8B", "en": "Gemini 1.5 Flash 8B"},
    # xAI Grok 模型（通过 OpenRouter）
    "x-ai/grok-4.1-fast": {"zh": "Grok 4.1 Fast", "en": "Grok 4.1 Fast"},
    "x-ai/grok-4-fast": {"zh": "Grok 4 Fast", "en": "Grok 4 Fast"},
    "x-ai/grok-4": {"zh": "Grok 4", "en": "Grok 4"},
    "x-ai/grok-2": {"zh": "Grok 2", "en": "Grok 2"},
    "x-ai/grok-2-vision": {"zh": "Grok 2 Vision", "en": "Grok 2 Vision"},
    "x-ai/grok-beta": {"zh": "Grok Beta", "en": "Grok Beta"},
}

OPENROUTER_RECOMMENDED_MODELS = [
    "openai/gpt-5.2-chat",
    "anthropic/claude-opus-4.5",
    "google/gemini-3-flash-preview",
    "x-ai/grok-4.1-fast",
]

OPENROUTER_PROVIDER_PREFIXES = [
    "openai/",
    "anthropic/",
    "google/",
    "x-ai/",
]


async def _fetch_openrouter_latest_models() -> list[str]:
    headers: dict[str, str] = {}
    if settings.OPENROUTER_API_KEY:
        headers["Authorization"] = f"Bearer {settings.OPENROUTER_API_KEY}"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                "https://openrouter.ai/api/v1/models",
                headers=headers,
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
    except Exception:
        return []

    models = payload.get("data", [])
    latest_by_prefix: dict[str, tuple[int, str]] = {}
    for item in models:
        model_id = item.get("id", "")
        created = item.get("created", 0)
        for prefix in OPENROUTER_PROVIDER_PREFIXES:
            if model_id.startswith(prefix):
                current = latest_by_prefix.get(prefix)
                if current is None or created > current[0]:
                    latest_by_prefix[prefix] = (created, model_id)
                break

    return [
        latest_by_prefix[prefix][1]
        for prefix in OPENROUTER_PROVIDER_PREFIXES
        if prefix in latest_by_prefix
    ]


@router.get("/models")
async def get_available_models(request: Request) -> JSONResponse:
    """获取所有可用的 LLM 模型列表

    Returns:
        包含模型列表的响应，每个模型包含：
        - provider: 提供商标识（如 "doubao", "deepseek"）
        - model_id: 模型 ID（如 "deepseek-chat", "qwen3-max"）
        - display_name: 用户友好的显示名称（支持国际化）
        - description: 模型描述
        - cost_per_million_tokens: 每百万 token 成本（元）
        - priority: 优先级（越小越高）
        - status: 健康状态（healthy/unhealthy/unknown）
        - is_recommended: 是否推荐使用
        - is_available: 是否可用
    """
    # 获取当前语言设置（已由 LocaleMiddleware 标准化为 zh 或 en）
    lang = getattr(request.state, "locale", "zh")

    # 获取所有注册的 LLM 服务
    all_services = ServiceRegistry.list_services("llm")

    models = []
    for provider in all_services:
        # 获取服务元数据
        service_class, metadata, _ = ServiceRegistry._services["llm"][provider]

        # 获取或触发健康检查
        health_result = HealthChecker.get_status("llm", provider)
        if not health_result:
            # 如果没有健康检查结果，主动触发一次
            await HealthChecker.check_service("llm", provider, force=True)
            health_result = HealthChecker.get_status("llm", provider)

        status = health_result.status.value if health_result else HealthStatus.UNKNOWN.value

        # OpenRouter 支持多模型：返回推荐模型列表，避免固定 env
        if provider == "openrouter":
            model_candidates = OPENROUTER_RECOMMENDED_MODELS
            if settings.OPENROUTER_DYNAMIC_MODELS:
                fetched = await _fetch_openrouter_latest_models()
                if fetched:
                    model_candidates = fetched
            for model_id in model_candidates:
                display_name = MODEL_DISPLAY_NAMES_I18N.get(model_id, {}).get(
                    lang, MODEL_DISPLAY_NAMES_I18N.get(model_id, {}).get("zh", model_id)
                )
                models.append(
                    {
                        "provider": provider,
                        "model_id": model_id,
                        "display_name": display_name,
                        "description": metadata.description,
                        "cost_per_million_tokens": metadata.cost_per_million_tokens,
                        "priority": metadata.priority,
                        "status": status,
                        "is_available": status == HealthStatus.HEALTHY.value,
                    }
                )
            continue

        # 获取模型 ID（需要实例化服务）
        model_id = None
        try:
            config = ConfigManager.get_config("llm", provider)
            model_id = getattr(config, "model", None)
        except Exception:
            model_id = None
        if not model_id:
            model_id = provider
        try:
            service = await SmartFactory.get_service("llm", provider=provider, model_id=model_id)
            model_id = service.model_name
        except Exception:
            # 如果获取失败，使用 provider 作为 fallback
            model_id = provider

        # 根据语言获取 display_name
        # 优先使用 model_id 映射（用于 OpenRouter 等多模型服务）
        display_name = None
        if model_id and model_id in MODEL_DISPLAY_NAMES_I18N:
            display_name = MODEL_DISPLAY_NAMES_I18N[model_id].get(lang)

        # 如果没有 model_id 映射，使用 provider 映射
        if not display_name:
            display_name = DISPLAY_NAMES_I18N.get(provider, {}).get(
                lang,
                DISPLAY_NAMES_I18N.get(provider, {}).get(
                    "zh", metadata.display_name or provider.capitalize()
                ),
            )

        models.append(
            {
                "provider": provider,
                "model_id": model_id,
                "display_name": display_name,
                "description": metadata.description,
                "cost_per_million_tokens": metadata.cost_per_million_tokens,
                "priority": metadata.priority,
                "status": status,
                "is_available": status == HealthStatus.HEALTHY.value,
            }
        )

    # 找出优先级最高的（priority 数字最小的）
    healthy_models = [m for m in models if m["is_available"]]
    if healthy_models:
        min_priority = min(cast(int, m["priority"]) for m in healthy_models)
        for model in models:
            # 只有优先级最高且可用的模型才推荐
            model["is_recommended"] = (
                model["is_available"] and cast(int, model["priority"]) == min_priority
            )
    else:
        # 如果没有健康的模型，都不推荐
        for model in models:
            model["is_recommended"] = False

    # 按优先级排序（优先级数字越小越靠前）
    models.sort(key=lambda x: (x["status"] != "healthy", x["priority"]))

    return success(data={"models": models})
