"""通义千问 LLM 服务实现（DashScope API）"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from app.config import settings
from app.core.exceptions import BusinessError
from app.core.fault_tolerance import CircuitBreaker, CircuitBreakerConfig, RetryConfig, retry
from app.core.monitoring import monitor
from app.core.registry import ServiceMetadata, register_service
from app.i18n.codes import ErrorCode
from app.prompts import get_prompt_manager
from app.services.llm.base import LLMService


@register_service(
    "llm",
    "qwen",
    metadata=ServiceMetadata(
        name="qwen",
        service_type="llm",
        priority=8,  # 高性价比
        description="通义千问 LLM 服务 (阿里云)",
        display_name="通义千问",
        cost_per_million_tokens=0.4,  # 约 0.4 元/百万tokens（qwen2.5-72b-instruct）
        rate_limit=60,  # 60 req/min (需根据实际调整)
    ),
)
class QwenLLMService(LLMService):
    """通义千问 LLM 服务实现（DashScope API）

    官方文档：https://help.aliyun.com/zh/dashscope/developer-reference/api-details
    """

    _circuit_breaker = CircuitBreaker.get_or_create(
        "qwen_llm",
        CircuitBreakerConfig(
            failure_threshold=5,
            success_threshold=2,
            timeout=60.0,
            expected_exception=(BusinessError, httpx.HTTPError),
        ),
    )

    def __init__(self) -> None:
        api_key = settings.QWEN_API_KEY
        model = settings.QWEN_MODEL or "qwen2.5-72b-instruct"

        if not api_key:
            raise RuntimeError("Qwen settings are not set")

        self._api_key = api_key
        self._model = model
        self._base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "qwen"

    @retry(
        RetryConfig(max_attempts=3, initial_delay=0.5, max_delay=5.0),
        exceptions=(httpx.TimeoutException, httpx.NetworkError),
    )
    @monitor("llm", "qwen")
    async def _call_llm_api(self, payload: dict, headers: dict) -> str:
        """调用通义千问 API（非流式）"""
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=120.0) as client:
                response = await client.post("/chat/completions", json=payload, headers=headers)
                response.raise_for_status()

                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")

                if not content:
                    raise BusinessError(
                        ErrorCode.AI_SUMMARY_GENERATION_FAILED,
                        reason="Qwen returned empty content",
                    )

                return content

        except httpx.TimeoutException as exc:
            raise BusinessError(
                ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                reason=f"Qwen request timeout: {exc}",
            ) from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code == 429:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                    reason=f"Qwen rate limit exceeded (HTTP {status_code})",
                ) from exc
            elif 500 <= status_code < 600:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                    reason=f"Qwen server error (HTTP {status_code})",
                ) from exc
            else:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_GENERATION_FAILED,
                    reason=f"Qwen request failed (HTTP {status_code}): {exc.response.text}",
                ) from exc
        except httpx.HTTPError as exc:
            raise BusinessError(
                ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                reason=f"Qwen network error: {exc}",
            ) from exc

    @monitor("llm", "qwen")
    async def summarize(self, text: str, summary_type: str, content_style: str = "meeting") -> str:
        if not text:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="text")

        prompt_manager = get_prompt_manager()
        prompt_config = prompt_manager.get_prompt(
            category="summary",
            prompt_type=summary_type,
            locale="zh-CN",
            variables={"transcript": text, "content_style": content_style},
        )

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": prompt_config["system"]},
                {"role": "user", "content": prompt_config["user_prompt"]},
            ],
            "max_tokens": prompt_config["model_params"].get("max_tokens", 4096),
            "temperature": prompt_config["model_params"].get("temperature", 0.7),
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}

        return await self._call_llm_api(payload, headers)

    @monitor("llm", "qwen")
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
            variables={"transcript": text, "content_style": content_style},
        )

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": prompt_config["system"]},
                {"role": "user", "content": prompt_config["user_prompt"]},
            ],
            "max_tokens": prompt_config["model_params"].get("max_tokens", 4096),
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
                reason=f"Qwen stream request timeout: {exc}",
            ) from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code == 429:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                    reason=f"Qwen rate limit exceeded (HTTP {status_code})",
                ) from exc
            elif 500 <= status_code < 600:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                    reason=f"Qwen server error (HTTP {status_code})",
                ) from exc
            else:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_GENERATION_FAILED,
                    reason=f"Qwen stream request failed (HTTP {status_code})",
                ) from exc
        except httpx.HTTPError as exc:
            raise BusinessError(
                ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                reason=f"Qwen network error: {exc}",
            ) from exc

    @monitor("llm", "qwen")
    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        """通用对话功能

        Args:
            messages: 对话消息列表
            **kwargs: 额外参数

        Returns:
            LLM 返回的文本内容
        """
        if not messages:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="messages")

        payload = {
            "model": self._model,
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", 4096),
            "temperature": kwargs.get("temperature", 0.7),
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}

        return await self._call_llm_api(payload, headers)

    @monitor("llm", "qwen")
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
            "max_tokens": kwargs.get("max_tokens", 4096),
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
                reason=f"Qwen stream error: {exc}",
            ) from exc

    async def health_check(self) -> bool:
        """健康检查：验证 API 配置是否正确

        Returns:
            True 如果服务健康，否则 False
        """
        try:
            if not self._api_key or not self._model:
                return False
            return True
        except Exception:
            return False

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """估算成本（人民币元）

        通义千问定价（参考 2024 年）：
        - qwen2.5-72b-instruct: 输入 ¥0.0004/1K, 输出 ¥0.0004/1K
        - qwen-turbo: 输入 ¥0.0008/1K, 输出 ¥0.002/1K

        Args:
            input_tokens: 输入 token 数
            output_tokens: 输出 token 数

        Returns:
            估算成本（人民币元）
        """
        # 使用 qwen2.5-72b-instruct 的价格作为基准
        input_price_per_1k = 0.0004
        output_price_per_1k = 0.0004

        input_cost = (input_tokens / 1000) * input_price_per_1k
        output_cost = (output_tokens / 1000) * output_price_per_1k

        return input_cost + output_cost
