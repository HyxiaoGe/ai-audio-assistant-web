"""OpenRouter LLM 服务实现（OpenAI 兼容接口）"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Optional

import httpx

from app.config import settings
from app.core.exceptions import BusinessError
from app.core.fault_tolerance import CircuitBreaker, CircuitBreakerConfig, RetryConfig, retry
from app.core.monitoring import monitor
from app.core.registry import ServiceMetadata, register_service
from app.i18n.codes import ErrorCode
from app.prompts import get_prompt_manager
from app.services.config_utils import get_config_value
from app.services.llm.base import LLMService


@register_service(
    "llm",
    "openrouter",
    metadata=ServiceMetadata(
        name="openrouter",
        service_type="llm",
        priority=7,
        description="OpenRouter LLM 服务（统一模型路由）",
        display_name="OpenRouter",
        cost_per_million_tokens=0.0,
        rate_limit=60,
    ),
)
class OpenRouterLLMService(LLMService):
    """OpenRouter LLM 服务实现（OpenAI 兼容接口）"""

    _circuit_breaker = CircuitBreaker.get_or_create(
        "openrouter_llm",
        CircuitBreakerConfig(
            failure_threshold=5,
            success_threshold=2,
            timeout=60.0,
            expected_exception=(BusinessError, httpx.HTTPError),
        ),
    )

    def __init__(self, model_id: Optional[str] = None, config: Optional[object] = None) -> None:
        api_key = get_config_value(config, "api_key", settings.OPENROUTER_API_KEY)
        base_url = get_config_value(
            config, "base_url", settings.OPENROUTER_BASE_URL or "https://openrouter.ai/api/v1"
        )
        model = model_id or get_config_value(config, "model", settings.OPENROUTER_MODEL)
        max_tokens = get_config_value(config, "max_tokens", settings.OPENROUTER_MAX_TOKENS or 4096)
        http_referer = get_config_value(
            config, "http_referer", settings.OPENROUTER_HTTP_REFERER or settings.API_BASE_URL
        )
        app_title = get_config_value(config, "app_title", settings.OPENROUTER_APP_TITLE)

        if not api_key:
            raise RuntimeError("OpenRouter API key is not set")
        if not model:
            raise RuntimeError("OpenRouter model_id is required")

        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._max_tokens = max_tokens
        self._http_referer = http_referer
        self._app_title = app_title

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "openrouter"

    def _build_headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if self._http_referer:
            headers["HTTP-Referer"] = self._http_referer
        if self._app_title:
            headers["X-Title"] = self._app_title
        return headers

    @retry(
        RetryConfig(max_attempts=3, initial_delay=0.5, max_delay=5.0),
        exceptions=(httpx.TimeoutException, httpx.NetworkError),
    )
    @_circuit_breaker.protected
    async def _call_llm_api(self, payload: dict, headers: dict) -> str:
        """调用 OpenRouter API（非流式）"""
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=120.0) as client:
                response = await client.post("/chat/completions", json=payload, headers=headers)
                response.raise_for_status()

                result = response.json()
                if "error" in result:
                    raise BusinessError(
                        ErrorCode.AI_SUMMARY_GENERATION_FAILED,
                        reason=str(result.get("error")),
                    )

                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                if not content:
                    raise BusinessError(
                        ErrorCode.AI_SUMMARY_GENERATION_FAILED,
                        reason="OpenRouter returned empty content",
                    )

                return content

        except httpx.TimeoutException as exc:
            raise BusinessError(
                ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                reason=f"OpenRouter request timeout: {exc}",
            ) from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code == 429:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                    reason=f"OpenRouter rate limit exceeded (HTTP {status_code})",
                ) from exc
            elif 500 <= status_code < 600:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                    reason=f"OpenRouter server error (HTTP {status_code})",
                ) from exc
            else:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_GENERATION_FAILED,
                    reason=f"OpenRouter request failed (HTTP {status_code}): {exc.response.text}",
                ) from exc
        except httpx.HTTPError as exc:
            raise BusinessError(
                ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                reason=f"OpenRouter network error: {exc}",
            ) from exc

    @monitor("llm", "openrouter")
    async def summarize(self, text: str, summary_type: str, content_style: str = "meeting") -> str:
        if not text:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="text")

        prompt_manager = get_prompt_manager()
        prompt_config = prompt_manager.get_prompt(
            category="summary",
            prompt_type=summary_type,
            locale="zh-CN",
            variables={"transcript": text, "content_style": content_style, "quality_notice": ""}  # V1.2: 质量感知功能预留,
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

        return await self._call_llm_api(payload, self._build_headers())

    @monitor("llm", "openrouter")
    async def summarize_stream(
        self, text: str, summary_type: str, content_style: str = "meeting"
    ) -> AsyncIterator[str]:
        """流式生成摘要"""
        if not text:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="text")

        prompt_manager = get_prompt_manager()
        prompt_config = prompt_manager.get_prompt(
            category="summary",
            prompt_type=summary_type,
            locale="zh-CN",
            variables={"transcript": text, "content_style": content_style, "quality_notice": ""}  # V1.2: 质量感知功能预留,
        )

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": prompt_config["system"]},
                {"role": "user", "content": prompt_config["user_prompt"]},
            ],
            "max_tokens": prompt_config["model_params"].get("max_tokens", self._max_tokens),
            "temperature": prompt_config["model_params"].get("temperature", 0.7),
            "stream": True,
        }

        headers = self._build_headers()

        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=120.0) as client:
                async with client.stream(
                    "POST", "/chat/completions", json=payload, headers=headers
                ) as response:
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
                            if "error" in chunk_data:
                                raise BusinessError(
                                    ErrorCode.AI_SUMMARY_GENERATION_FAILED,
                                    reason=str(chunk_data.get("error")),
                                )

                            delta = chunk_data.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")

                            if content:
                                yield content
                        except json.JSONDecodeError:
                            continue

        except httpx.TimeoutException as exc:
            raise BusinessError(
                ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                reason=f"OpenRouter stream request timeout: {exc}",
            ) from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code == 429:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                    reason=f"OpenRouter rate limit exceeded (HTTP {status_code})",
                ) from exc
            elif 500 <= status_code < 600:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                    reason=f"OpenRouter server error (HTTP {status_code})",
                ) from exc
            else:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_GENERATION_FAILED,
                    reason=f"OpenRouter stream request failed (HTTP {status_code})",
                ) from exc
        except httpx.HTTPError as exc:
            raise BusinessError(
                ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                reason=f"OpenRouter stream network error: {exc}",
            ) from exc

    @monitor("llm", "openrouter")
    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        """通用对话功能"""
        if not messages:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="messages")

        payload = {
            "model": self._model,
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", self._max_tokens),
            "temperature": kwargs.get("temperature", 0.7),
        }

        return await self._call_llm_api(payload, self._build_headers())

    @monitor("llm", "openrouter")
    async def chat_stream(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> AsyncIterator[str]:
        """流式对话功能"""
        if not messages:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="messages")

        payload = {
            "model": self._model,
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", self._max_tokens),
            "temperature": kwargs.get("temperature", 0.7),
            "stream": True,
        }

        headers = self._build_headers()

        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=120.0) as client:
                async with client.stream(
                    "POST", "/chat/completions", json=payload, headers=headers
                ) as response:
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
                            if "error" in chunk_data:
                                raise BusinessError(
                                    ErrorCode.AI_SUMMARY_GENERATION_FAILED,
                                    reason=str(chunk_data.get("error")),
                                )

                            delta = chunk_data.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")

                            if content:
                                yield content
                        except json.JSONDecodeError:
                            continue

        except httpx.HTTPError as exc:
            raise BusinessError(
                ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                reason=f"OpenRouter stream error: {exc}",
            ) from exc

    async def health_check(self) -> bool:
        """健康检查：验证 API 配置是否正确"""
        try:
            return bool(self._api_key and self._model)
        except Exception:
            return False

    @monitor("llm", "openrouter")
    async def generate(
        self,
        prompt: str,
        system_message: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> str:
        """通用文本生成（用于章节划分等场景）

        Args:
            prompt: 用户提示词
            system_message: 系统消息（可选）
            temperature: 温度参数（可选）
            max_tokens: 最大token数（可选）
            **kwargs: 额外参数

        Returns:
            生成的文本内容
        """
        messages = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens or self._max_tokens,
            "temperature": temperature if temperature is not None else 0.7,
        }

        return await self._call_llm_api(payload, self._build_headers())

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """估算成本（人民币元）

        OpenRouter 价格随模型变化，统一返回 0 以避免误导。
        """
        return 0.0
