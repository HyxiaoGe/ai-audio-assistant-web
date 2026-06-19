"""溯源 PR5:LLM 调用透出真实 token 用量(input/output)。

此前 LiteLLM 响应里的 usage(prompt_tokens/completion_tokens)在 _request_chat_completion
解析完 content 后被直接丢弃,Summary.token_count 退化成 len(content) 字符数。本组测试钉住:
- proxy 新增 generate_with_usage() 返回 (content, usage),usage 为归一后的 input/output_tokens;
- 响应无 usage 块时优雅返回 None(不崩);
- 既有 generate() 仍返回纯字符串(向后兼容,链路改造不破坏 str 契约);
- 基类默认 generate_with_usage 对无用量 provider 返回 (text, None)。

代理实例被 SmartFactory 跨任务缓存复用,故用量必须随调用返回值带出,严禁写实例可变状态(会串号)。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from app.services.llm.base import LLMService
from app.services.llm.proxy import ProxyLLMService


def _make_service() -> ProxyLLMService:
    return ProxyLLMService(
        config={
            "base_url": "http://litellm.test",
            "api_key": "test-key",
            "model": "m",
            "max_tokens": 16,
        }
    )


def _install_transport(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient.__init__
    monkeypatch.setattr(
        httpx.AsyncClient,
        "__init__",
        lambda self, *a, **k: original(self, *a, **{**k, "transport": transport}),
    )


async def test_generate_with_usage_returns_token_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "摘要正文"}}],
                "usage": {"prompt_tokens": 1200, "completion_tokens": 300, "total_tokens": 1500},
            },
        )

    _install_transport(monkeypatch, handler)
    svc = _make_service()
    content, usage = await svc.generate_with_usage("prompt", system_message="sys")
    assert content == "摘要正文"
    assert usage is not None
    assert usage["input_tokens"] == 1200
    assert usage["output_tokens"] == 300
    assert usage["total_tokens"] == 1500


async def test_generate_with_usage_none_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    _install_transport(monkeypatch, handler)
    svc = _make_service()
    content, usage = await svc.generate_with_usage("prompt")
    assert content == "x"
    assert usage is None


async def test_generate_still_returns_plain_string(monkeypatch: pytest.MonkeyPatch) -> None:
    # 链路改造后 generate() 必须仍返回 str(不是 tuple),保持既有调用方契约不破。
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "纯文本结果"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
            },
        )

    _install_transport(monkeypatch, handler)
    svc = _make_service()
    result = await svc.generate("prompt")
    assert result == "纯文本结果"
    assert isinstance(result, str)


class _NoUsageLLM(LLMService):
    """不暴露用量的最小 provider:验证基类默认 generate_with_usage 返回 (text, None)。"""

    @property
    def model_name(self) -> str:
        return "fake"

    @property
    def provider(self) -> str:
        return "fake"

    async def summarize(self, text: str, summary_type: str, content_style: str = "meeting") -> str:
        return "s"

    async def summarize_stream(
        self, text: str, summary_type: str, content_style: str = "meeting"
    ) -> AsyncIterator[str]:
        yield "s"

    async def generate(
        self,
        prompt: str,
        system_message: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> str:
        return "generated-text"

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        return "c"

    async def chat_stream(self, messages: list[dict[str, str]], **kwargs: Any) -> AsyncIterator[str]:
        yield "c"

    async def health_check(self) -> bool:
        return True

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return 0.0


async def test_base_generate_with_usage_defaults_to_none_usage() -> None:
    content, usage = await _NoUsageLLM().generate_with_usage("prompt")
    assert content == "generated-text"
    assert usage is None
