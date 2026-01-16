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
    "moonshot",
    metadata=ServiceMetadata(
        name="moonshot",
        service_type="llm",
        priority=15,
        description="Moonshot LLM 服务 (月之暗面 Kimi)",
        display_name="Kimi",
        cost_per_million_tokens=24.0,  # 约 24 元/百万tokens（moonshot-v1-8k: 0.012¥/1K * 2）
        rate_limit=60,
    ),
)
class MoonshotLLMService(LLMService):
    """Moonshot LLM 服务实现（API 兼容 OpenAI）。"""

    _circuit_breaker = CircuitBreaker.get_or_create(
        "moonshot_llm",
        CircuitBreakerConfig(
            failure_threshold=5,
            success_threshold=2,
            timeout=60.0,
            expected_exception=(BusinessError, httpx.HTTPError),
        ),
    )

    def __init__(self, config: Optional[object] = None) -> None:
        api_key = get_config_value(config, "api_key", settings.MOONSHOT_API_KEY)
        base_url = get_config_value(config, "base_url", settings.MOONSHOT_BASE_URL)
        model = get_config_value(config, "model", settings.MOONSHOT_MODEL)
        max_tokens = get_config_value(config, "max_tokens", settings.MOONSHOT_MAX_TOKENS)

        if not api_key or not base_url or not model:
            raise RuntimeError("Moonshot settings are not set")

        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._max_tokens = max_tokens

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "moonshot"

    @retry(
        RetryConfig(
            max_attempts=3,
            initial_delay=1.0,
            max_delay=10.0,
            exponential_base=2.0,
            jitter=True,
        ),
        exceptions=(httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError),
    )
    @_circuit_breaker.protected
    async def _call_api(self, payload: dict, headers: dict) -> str:
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=60.0) as client:
                response = await client.post("/chat/completions", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not isinstance(content, str) or not content.strip():
                raise BusinessError(ErrorCode.AI_SUMMARY_GENERATION_FAILED, reason="empty response")
            return content.strip()
        except httpx.TimeoutException as exc:
            raise BusinessError(
                ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                reason=f"Moonshot request timeout: {exc}",
            ) from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code == 429:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                    reason=f"Moonshot rate limit exceeded (HTTP {status_code})",
                ) from exc
            if 500 <= status_code < 600:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                    reason=f"Moonshot server error (HTTP {status_code})",
                ) from exc
            raise BusinessError(
                ErrorCode.AI_SUMMARY_GENERATION_FAILED,
                reason=f"Moonshot request failed (HTTP {status_code}): {exc.response.text}",
            ) from exc
        except httpx.HTTPError as exc:
            raise BusinessError(
                ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                reason=f"Moonshot network error: {exc}",
            ) from exc

    @monitor("llm", "moonshot")
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
        headers = {"Authorization": f"Bearer {self._api_key}"}

        return await self._call_api(payload, headers)

    @monitor("llm", "moonshot")
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
        headers = {"Authorization": f"Bearer {self._api_key}"}

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
                            delta = chunk_data.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")

                            if content:
                                yield content
                        except json.JSONDecodeError:
                            continue
        except httpx.HTTPError as exc:
            raise BusinessError(
                ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                reason=f"Moonshot stream error: {exc}",
            ) from exc

    @monitor("llm", "moonshot")
    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        if not messages:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="messages")

        payload = {
            "model": self._model,
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", self._max_tokens),
            "temperature": kwargs.get("temperature", 0.7),
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}

        return await self._call_api(payload, headers)

    @monitor("llm", "moonshot")
    async def chat_stream(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> AsyncIterator[str]:
        if not messages:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="messages")

        payload = {
            "model": self._model,
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", self._max_tokens),
            "temperature": kwargs.get("temperature", 0.7),
            "stream": True,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}

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
                            delta = chunk_data.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")

                            if content:
                                yield content
                        except json.JSONDecodeError:
                            continue
        except httpx.HTTPError as exc:
            raise BusinessError(
                ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                reason=f"Moonshot stream error: {exc}",
            ) from exc

    async def health_check(self) -> bool:
        try:
            if not self._api_key or not self._base_url or not self._model:
                return False
            return True
        except Exception:
            return False

    @monitor("llm", "moonshot")
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
        headers = {"Authorization": f"Bearer {self._api_key}"}

        return await self._call_api(payload, headers)

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        if "128k" in self._model:
            price_per_1k = 0.06
        elif "32k" in self._model:
            price_per_1k = 0.024
        else:
            price_per_1k = 0.012

        input_cost = (input_tokens / 1000) * price_per_1k
        output_cost = (output_tokens / 1000) * price_per_1k
        return input_cost + output_cost
