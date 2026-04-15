"""LiteLLM Proxy LLM 服务实现

通过 LiteLLM Proxy 统一路由所有 LLM 请求，支持多模型切换。
Proxy 提供 OpenAI 兼容的 /v1/chat/completions 端点。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import settings
from app.core.exceptions import BusinessError
from app.core.fault_tolerance import CircuitBreaker, CircuitBreakerConfig, RetryConfig, retry
from app.core.monitoring import monitor
from app.core.registry import ServiceMetadata, register_service
from app.i18n.codes import ErrorCode
from app.prompts import get_prompt_manager
from app.services.llm.base import LLMService

logger = logging.getLogger(__name__)


@register_service(
    "llm",
    "proxy",
    metadata=ServiceMetadata(
        name="proxy",
        service_type="llm",
        priority=1,
        description="LiteLLM Proxy LLM 服务（统一路由）",
        display_name="LiteLLM Proxy",
        cost_per_million_tokens=0.0,
        rate_limit=120,
    ),
)
class ProxyLLMService(LLMService):
    """通过 LiteLLM Proxy 统一路由的 LLM 服务

    所有请求转发到 LiteLLM Proxy 的 OpenAI 兼容端点，
    由 Proxy 负责选择后端模型、管理 API Key、计费等。
    """

    _circuit_breaker = CircuitBreaker.get_or_create(
        "proxy_llm",
        CircuitBreakerConfig(
            failure_threshold=5,
            success_threshold=2,
            timeout=60.0,
            expected_exception=(BusinessError, httpx.HTTPError),
        ),
    )

    def __init__(self, config: object | None = None, model_id: str | None = None) -> None:
        from app.services.config_utils import get_config_value

        base_url = get_config_value(config, "base_url", settings.LITELLM_BASE_URL)
        api_key = get_config_value(config, "api_key", settings.LITELLM_API_KEY)
        model = model_id or get_config_value(config, "model", settings.LITELLM_MODEL)
        max_tokens = get_config_value(config, "max_tokens", settings.LITELLM_MAX_TOKENS)

        if not api_key:
            raise RuntimeError("LITELLM_API_KEY is not set")

        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "proxy"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    @retry(
        RetryConfig(max_attempts=3, initial_delay=0.5, max_delay=5.0),
        exceptions=(httpx.TimeoutException, httpx.NetworkError),
    )
    @monitor("llm", "proxy")
    async def _call_api(self, payload: dict) -> str:
        """非流式调用 LiteLLM Proxy"""
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=120.0) as client:
                response = await client.post(
                    "/v1/chat/completions",
                    json=payload,
                    headers=self._headers(),
                )
                response.raise_for_status()
                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                if not content:
                    raise BusinessError(
                        ErrorCode.AI_SUMMARY_GENERATION_FAILED,
                        reason="LiteLLM Proxy returned empty content",
                    )
                return content

        except httpx.TimeoutException as exc:
            raise BusinessError(
                ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                reason=f"LiteLLM Proxy request timeout: {exc}",
            ) from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code == 429:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                    reason=f"LiteLLM Proxy rate limit exceeded (HTTP {status_code})",
                ) from exc
            elif 500 <= status_code < 600:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                    reason=f"LiteLLM Proxy server error (HTTP {status_code})",
                ) from exc
            else:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_GENERATION_FAILED,
                    reason=f"LiteLLM Proxy request failed (HTTP {status_code}): {exc.response.text}",
                ) from exc
        except httpx.HTTPError as exc:
            raise BusinessError(
                ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                reason=f"LiteLLM Proxy network error: {exc}",
            ) from exc

    async def _stream_api(self, payload: dict) -> AsyncIterator[str]:
        """流式调用 LiteLLM Proxy，解析 SSE"""
        payload["stream"] = True
        try:
            async with (
                httpx.AsyncClient(base_url=self._base_url, timeout=120.0) as client,
                client.stream("POST", "/v1/chat/completions", json=payload, headers=self._headers()) as response,
            ):
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data: "):
                        line = line[6:]
                    if line == "[DONE]":
                        break
                    try:
                        chunk_data = json.loads(line)
                        delta = chunk_data.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        continue

        except httpx.TimeoutException as exc:
            raise BusinessError(
                ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                reason=f"LiteLLM Proxy stream timeout: {exc}",
            ) from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code == 429:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                    reason=f"LiteLLM Proxy rate limit exceeded (HTTP {status_code})",
                ) from exc
            elif 500 <= status_code < 600:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                    reason=f"LiteLLM Proxy server error (HTTP {status_code})",
                ) from exc
            else:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_GENERATION_FAILED,
                    reason=f"LiteLLM Proxy stream failed (HTTP {status_code})",
                ) from exc
        except httpx.HTTPError as exc:
            raise BusinessError(
                ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                reason=f"LiteLLM Proxy network error: {exc}",
            ) from exc

    # ------------------------------------------------------------------
    # Public interface (LLMService abstract methods)
    # ------------------------------------------------------------------

    @monitor("llm", "proxy")
    async def summarize(self, text: str, summary_type: str, content_style: str = "meeting") -> str:
        if not text:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="text")

        prompt_manager = get_prompt_manager()
        prompt_config = prompt_manager.get_prompt(
            category="summary",
            prompt_type=summary_type,
            locale="zh-CN",
            variables={
                "transcript": text,
                "content_style": content_style,
                "quality_notice": "",
            },
        )

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": prompt_config["system"]},
                {"role": "user", "content": prompt_config["user_prompt"]},
            ],
            "max_tokens": prompt_config["model_params"].get("max_tokens", self._max_tokens),
            "temperature": prompt_config["model_params"].get("temperature", 0.7),
        }
        return await self._call_api(payload)

    @monitor("llm", "proxy")
    async def summarize_stream(
        self, text: str, summary_type: str, content_style: str = "meeting"
    ) -> AsyncIterator[str]:
        if not text:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="text")

        prompt_manager = get_prompt_manager()
        prompt_config = prompt_manager.get_prompt(
            category="summary",
            prompt_type=summary_type,
            locale="zh-CN",
            variables={
                "transcript": text,
                "content_style": content_style,
                "quality_notice": "",
            },
        )

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": prompt_config["system"]},
                {"role": "user", "content": prompt_config["user_prompt"]},
            ],
            "max_tokens": prompt_config["model_params"].get("max_tokens", self._max_tokens),
            "temperature": prompt_config["model_params"].get("temperature", 0.7),
        }
        async for chunk in self._stream_api(payload):
            yield chunk

    @monitor("llm", "proxy")
    async def generate(
        self,
        prompt: str,
        system_message: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> str:
        messages: list[dict[str, str]] = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens or self._max_tokens,
            "temperature": temperature or 0.7,
        }
        return await self._call_api(payload)

    @monitor("llm", "proxy")
    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        if not messages:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="messages")

        payload = {
            "model": self._model,
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", self._max_tokens),
            "temperature": kwargs.get("temperature", 0.7),
        }
        return await self._call_api(payload)

    @monitor("llm", "proxy")
    async def chat_stream(self, messages: list[dict[str, str]], **kwargs: Any) -> AsyncIterator[str]:
        if not messages:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="messages")

        payload = {
            "model": self._model,
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", self._max_tokens),
            "temperature": kwargs.get("temperature", 0.7),
        }
        async for chunk in self._stream_api(payload):
            yield chunk

    async def health_check(self) -> bool:
        """健康检查：尝试向 LiteLLM Proxy 发送轻量请求"""
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=10.0) as client:
                response = await client.get("/health", headers=self._headers())
                return response.status_code == 200
        except Exception:
            return False

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """成本估算 -- LiteLLM Proxy 统一管理计费，这里返回 0"""
        return 0.0
