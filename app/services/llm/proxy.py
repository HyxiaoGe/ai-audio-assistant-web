"""LiteLLM Proxy LLM 服务实现

通过 LiteLLM Proxy 统一路由所有 LLM 请求，支持多模型切换。
Proxy 提供 OpenAI 兼容的 /v1/chat/completions 端点。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import settings
from app.core.exceptions import BusinessError
from app.core.fault_tolerance import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerOpenError,
    RetryConfig,
    retry,
)
from app.core.monitoring import monitor
from app.core.registry import ServiceMetadata, register_service
from app.i18n.codes import ErrorCode
from app.prompts import get_prompt_manager
from app.services.llm.base import LLMService

logger = logging.getLogger(__name__)


def _normalize_usage(raw: object) -> dict[str, int | None] | None:
    """把 OpenAI 兼容响应里的 usage 块归一为 {input_tokens, output_tokens, total_tokens}。

    上游字段名是 prompt_tokens / completion_tokens / total_tokens；两者皆缺则视为无用量
    （返回 None），调用方据此把 Summary 的 token 列留 NULL，而不是落入伪造值。
    """
    if not isinstance(raw, dict):
        return None
    prompt_tokens = raw.get("prompt_tokens")
    completion_tokens = raw.get("completion_tokens")
    if prompt_tokens is None and completion_tokens is None:
        return None
    return {
        "input_tokens": prompt_tokens,
        "output_tokens": completion_tokens,
        "total_tokens": raw.get("total_tokens"),
    }


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

    def __init__(
        self,
        config: object | None = None,
        model_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
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
        # 成本归因:LiteLLM 按请求体 user 字段累计 end-user spend(GET /customer/info 据此)。
        # 带 user_id 的实例在 SmartFactory force_new=True、不进缓存、不跨用户复用,存实例状态安全。
        self._end_user_id = user_id

    def _apply_attribution(
        self,
        payload: dict,
        *,
        task_id: str | None = None,
        summary_type: str | None = None,
    ) -> dict:
        """给 payload 注入成本归因标签(单一来源,避免各 payload 漂移)。

        - ``user``:LiteLLM 据此把 spend 归到该 end-user/customer(仅在有 user_id 时写,
          不污染匿名/系统调用的旧契约);
        - ``metadata``:task_id/summary_type 供 LiteLLM 日志下钻,随调用带出、不存实例状态。
        """
        if self._end_user_id:
            payload["user"] = self._end_user_id
        metadata: dict[str, str] = {}
        if task_id:
            metadata["task_id"] = task_id
        if summary_type:
            metadata["summary_type"] = summary_type
        if metadata:
            metadata["app"] = "audio"
            payload["metadata"] = metadata
        return payload

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
    async def _request_chat_completion(self, payload: dict) -> tuple[str, dict[str, int | None] | None]:
        """实际的 HTTP 调用与解析，返回 (content, usage)。

        故意放行底层 httpx 异常（不在此层包成 BusinessError），以便外层 @retry 能识别
        TimeoutException / NetworkError 并真正重试瞬时故障；HTTPStatusError(4xx/5xx)
        不在重试白名单内，会直接上抛交由调用方映射。

        usage 从上游响应解析并随返回值带出（不写实例状态）——代理实例被 SmartFactory 跨任务
        缓存复用，写实例可变状态会在并发摘要间串号。
        """
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
            return content, _normalize_usage(result.get("usage"))

    @_circuit_breaker.protected
    async def _guarded_call(self, payload: dict) -> tuple[str, dict[str, int | None] | None]:
        """在熔断器保护下完成调用，并把底层 httpx 异常映射为对外的 BusinessError。

        重试由 _request_chat_completion 内部完成；重试耗尽后上抛的原始 httpx 异常
        在此映射为 BusinessError，并由熔断器按 expected_exception 计入失败计数。
        透传 (content, usage)。
        """
        try:
            return await self._request_chat_completion(payload)
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
                # 上游响应体可能含敏感信息（密钥片段、内部地址等），仅记入服务端日志，
                # 不回传给客户端；与上面 429/5xx 分支保持一致，对外只暴露状态码。
                logger.warning(
                    "LiteLLM Proxy request failed (HTTP %s): %s",
                    status_code,
                    exc.response.text,
                )
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_GENERATION_FAILED,
                    reason=f"LiteLLM Proxy request failed (HTTP {status_code})",
                ) from exc
        except httpx.HTTPError as exc:
            raise BusinessError(
                ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                reason=f"LiteLLM Proxy network error: {exc}",
            ) from exc

    async def _call_api(self, payload: dict) -> tuple[str, dict[str, int | None] | None]:
        """非流式调用入口：熔断器打开时快速失败，并把熔断异常映射为 BusinessError。返回 (content, usage)。"""
        try:
            return await self._guarded_call(payload)
        except CircuitBreakerOpenError as exc:
            raise BusinessError(
                ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                reason="LiteLLM Proxy 熔断器已打开，快速失败",
            ) from exc

    async def _stream_api(self, payload: dict) -> AsyncIterator[str]:
        """流式调用入口：在熔断器保护下转发到内部 SSE 实现（D3-002）。

        与非流式 _call_api 对齐——熔断器 OPEN 时快速失败（不发起 HTTP、不进入内部重试循环），
        避免对已知故障的后端形成重试风暴；流完成记成功、expected_exception 记失败。装饰器
        ``@protected`` 会把异步生成器变成「返回协程」破坏 async for，故改用 guard() 上下文管理器。
        """
        try:
            async with self._circuit_breaker.guard():
                async for chunk in self._stream_api_inner(payload):
                    yield chunk
        except CircuitBreakerOpenError as exc:
            raise BusinessError(
                ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                reason="LiteLLM Proxy 熔断器已打开，快速失败",
            ) from exc

    async def _stream_api_inner(self, payload: dict) -> AsyncIterator[str]:
        """流式调用 LiteLLM Proxy，解析 SSE。

        瞬时故障（连接超时 / 网络错误）在「尚未产出任何 token」时按指数退避重试，对齐非流式
        _request_chat_completion 的 @retry 语义（D7）：流式入口不经 _guarded_call / @retry，
        原先把 Timeout/NetworkError 当场包成 BusinessError 即 0 重试。一旦已 yield 过内容则不能
        重试（会重复输出），直接映射上抛。HTTPStatusError(4xx/5xx) 不在重试范围，立即映射。
        """
        payload["stream"] = True
        max_attempts = 3
        base_delay = 0.5
        yielded_any = False
        for attempt in range(1, max_attempts + 1):
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
                                yielded_any = True
                                yield content
                        except json.JSONDecodeError:
                            continue
                return
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                # 已产出内容 / 重试用尽则无法安全重试（会重复 token），映射上抛；否则退避后重试。
                if yielded_any or attempt >= max_attempts:
                    raise BusinessError(
                        ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                        reason=f"LiteLLM Proxy stream failed after {attempt} attempt(s): {exc}",
                    ) from exc
                await asyncio.sleep(base_delay * (2 ** (attempt - 1)))
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
        self._apply_attribution(payload, summary_type=summary_type)
        content, _usage = await self._call_api(payload)
        return content

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
        self._apply_attribution(payload, summary_type=summary_type)
        async for chunk in self._stream_api(payload):
            yield chunk

    def _build_generate_payload(
        self,
        prompt: str,
        system_message: str | None,
        temperature: float | None,
        max_tokens: int | None,
        *,
        task_id: str | None = None,
        summary_type: str | None = None,
    ) -> dict:
        """构造 generate / generate_with_usage 共用的 chat 请求体（单一来源，避免漂移）。"""
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
        return self._apply_attribution(payload, task_id=task_id, summary_type=summary_type)

    @monitor("llm", "proxy")
    async def generate(
        self,
        prompt: str,
        system_message: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> str:
        payload = self._build_generate_payload(
            prompt,
            system_message,
            temperature,
            max_tokens,
            task_id=kwargs.get("task_id"),
            summary_type=kwargs.get("summary_type"),
        )
        content, _usage = await self._call_api(payload)
        return content

    @monitor("llm", "proxy")
    async def generate_with_usage(
        self,
        prompt: str,
        system_message: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, int | None] | None]:
        """同 generate，但额外返回上游真实 token 用量 {input_tokens, output_tokens, total_tokens}。

        响应无 usage 块时返回 (content, None)。供摘要/章节路径落库真实 token，取代 token_count
        旧的字符数近似。
        """
        payload = self._build_generate_payload(
            prompt,
            system_message,
            temperature,
            max_tokens,
            task_id=kwargs.get("task_id"),
            summary_type=kwargs.get("summary_type"),
        )
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
        self._apply_attribution(payload, task_id=kwargs.get("task_id"), summary_type=kwargs.get("summary_type"))
        content, _usage = await self._call_api(payload)
        return content

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
        self._apply_attribution(payload, task_id=kwargs.get("task_id"), summary_type=kwargs.get("summary_type"))
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
