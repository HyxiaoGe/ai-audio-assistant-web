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
    "doubao",
    metadata=ServiceMetadata(
        name="doubao",
        service_type="llm",
        priority=10,
        description="豆包 LLM 服务 (字节跳动)",
        display_name="豆包",
        cost_per_million_tokens=0.8,  # 约 0.8 元/百万tokens
        rate_limit=60,
    ),
)
class DoubaoLLMService(LLMService):
    # 熔断器配置：5次失败后熔断，60秒后尝试恢复
    _circuit_breaker = CircuitBreaker.get_or_create(
        "doubao_llm",
        CircuitBreakerConfig(
            failure_threshold=5,
            success_threshold=2,
            timeout=60.0,
            expected_exception=(BusinessError, httpx.HTTPError),
        ),
    )

    def __init__(self, config: Optional[object] = None) -> None:
        api_key = get_config_value(config, "api_key", settings.DOUBAO_API_KEY)
        base_url = get_config_value(config, "base_url", settings.DOUBAO_BASE_URL)
        model = get_config_value(config, "model", settings.DOUBAO_MODEL)
        max_tokens = get_config_value(config, "max_tokens", settings.DOUBAO_MAX_TOKENS)
        if not api_key or not base_url or not model or not max_tokens:
            raise RuntimeError("Doubao settings are not set")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._max_tokens = max_tokens

    @property
    def model_name(self) -> str:
        return self._model

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
    async def _call_llm_api(self, payload: dict, headers: dict) -> str:
        """调用 LLM API（带重试和熔断保护）"""
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
                reason=f"LLM request timeout: {exc}",
            ) from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code == 429:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                    reason=f"LLM rate limit exceeded (HTTP {status_code})",
                ) from exc
            elif 500 <= status_code < 600:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                    reason=f"LLM server error (HTTP {status_code})",
                ) from exc
            else:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_GENERATION_FAILED,
                    reason=f"LLM request failed (HTTP {status_code}): {exc.response.text}",
                ) from exc
        except httpx.HTTPError as exc:
            raise BusinessError(
                ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                reason=f"LLM network error: {exc}",
            ) from exc

    @monitor("llm", "doubao")
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
            },  # V1.2: 质量感知功能预留,
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

        return await self._call_llm_api(payload, headers)

    @monitor("llm", "doubao")
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
            variables={
                "transcript": text,
                "content_style": content_style,
                "quality_notice": "",
            },  # V1.2: 质量感知功能预留,
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

        except httpx.TimeoutException as exc:
            raise BusinessError(
                ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                reason=f"LLM stream request timeout: {exc}",
            ) from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code == 429:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                    reason=f"LLM rate limit exceeded (HTTP {status_code})",
                ) from exc
            elif 500 <= status_code < 600:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                    reason=f"LLM server error (HTTP {status_code})",
                ) from exc
            else:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_GENERATION_FAILED,
                    reason=f"LLM stream request failed (HTTP {status_code})",
                ) from exc
        except httpx.HTTPError as exc:
            raise BusinessError(
                ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                reason=f"LLM stream network error: {exc}",
            ) from exc

    @property
    def provider(self) -> str:
        return "doubao"

    @monitor("llm", "doubao")
    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        """通用对话功能

        Args:
            messages: 对话消息列表，格式：[{"role": "user", "content": "..."}]
            **kwargs: 额外参数（max_tokens, temperature 等）

        Returns:
            LLM 返回的文本内容
        """
        if not messages:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="messages")

        payload = {
            "model": self._model,
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", self._max_tokens),
            "temperature": kwargs.get("temperature", 0.7),
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}

        return await self._call_llm_api(payload, headers)

    @monitor("llm", "doubao")
    async def chat_stream(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> AsyncIterator[str]:
        """流式对话功能

        Args:
            messages: 对话消息列表
            **kwargs: 额外参数

        Yields:
            流式返回的文本片段
        """
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

        except httpx.TimeoutException as exc:
            raise BusinessError(
                ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                reason=f"LLM stream request timeout: {exc}",
            ) from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code == 429:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                    reason=f"LLM rate limit exceeded (HTTP {status_code})",
                ) from exc
            elif 500 <= status_code < 600:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                    reason=f"LLM server error (HTTP {status_code})",
                ) from exc
            else:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_GENERATION_FAILED,
                    reason=f"LLM stream request failed (HTTP {status_code})",
                ) from exc
        except httpx.HTTPError as exc:
            raise BusinessError(
                ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                reason=f"LLM stream network error: {exc}",
            ) from exc

    async def health_check(self) -> bool:
        """健康检查：验证 API 配置是否正确

        Returns:
            True 如果服务健康，否则 False
        """
        try:
            # 检查必要的配置是否存在
            if not self._api_key or not self._base_url or not self._model:
                return False

            # 简单的连接测试：发送一个最小的请求
            # 注意：这里不做实际的 API 调用，只验证配置完整性
            # 实际的 API 调用可能会消耗配额，应该谨慎
            return True

        except Exception:
            return False

    @monitor("llm", "doubao")
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

        return await self._call_llm_api(payload, headers)

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """估算成本（人民币元）

        豆包定价（参考 2024 年）：
        - 输入: ¥0.0008 / 1K tokens
        - 输出: ¥0.002 / 1K tokens

        Args:
            input_tokens: 输入 token 数
            output_tokens: 输出 token 数

        Returns:
            估算成本（人民币元）
        """
        # 价格单位：元/1K tokens
        input_price_per_1k = 0.0008
        output_price_per_1k = 0.002

        input_cost = (input_tokens / 1000) * input_price_per_1k
        output_cost = (output_tokens / 1000) * output_price_per_1k

        return input_cost + output_cost
