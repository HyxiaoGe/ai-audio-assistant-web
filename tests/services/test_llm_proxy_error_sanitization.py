"""LiteLLM Proxy 非 5xx 上游错误的脱敏测试。

上游响应体可能含敏感信息（密钥片段、内部地址等），不得透传给客户端；
仅记入服务端日志。本测试通过 httpx.MockTransport 真实触发 raise_for_status。
"""

from __future__ import annotations

import logging

import httpx
import pytest

from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.services.llm.proxy import ProxyLLMService

_SECRET_BODY = "INTERNAL secret upstream: api_key=sk-LEAK-123 model=gpt-internal"


def _make_service() -> ProxyLLMService:
    return ProxyLLMService(
        config={
            "base_url": "http://litellm.test",
            "api_key": "test-key",
            "model": "m",
            "max_tokens": 16,
        }
    )


def _patch_transport(monkeypatch: pytest.MonkeyPatch, status: int, body: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=body)

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient.__init__
    monkeypatch.setattr(
        httpx.AsyncClient,
        "__init__",
        lambda self, *a, **k: original(self, *a, **{**k, "transport": transport}),
    )


async def test_non_5xx_error_reason_excludes_raw_upstream_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_transport(monkeypatch, 400, _SECRET_BODY)
    svc = _make_service()

    with pytest.raises(BusinessError) as ei:
        await svc._call_api({"model": "m", "messages": []})

    assert ei.value.code == ErrorCode.AI_SUMMARY_GENERATION_FAILED
    reason = ei.value.kwargs.get("reason", "")
    assert "HTTP 400" in reason
    assert "sk-LEAK-123" not in reason
    assert "gpt-internal" not in reason
    assert "secret" not in reason


async def test_rendered_client_message_excludes_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.i18n import get_message

    _patch_transport(monkeypatch, 401, _SECRET_BODY)
    svc = _make_service()

    with pytest.raises(BusinessError) as ei:
        await svc._call_api({"model": "m", "messages": []})

    msg_en = get_message(ErrorCode.AI_SUMMARY_GENERATION_FAILED, "en", **ei.value.kwargs)
    msg_zh = get_message(ErrorCode.AI_SUMMARY_GENERATION_FAILED, "zh", **ei.value.kwargs)
    assert "sk-LEAK-123" not in msg_en and "sk-LEAK-123" not in msg_zh
    assert "HTTP 401" in msg_en


async def test_raw_body_is_logged_server_side(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _patch_transport(monkeypatch, 422, _SECRET_BODY)
    svc = _make_service()

    with caplog.at_level(logging.WARNING, logger="app.services.llm.proxy"):
        with pytest.raises(BusinessError):
            await svc._call_api({"model": "m", "messages": []})

    assert "sk-LEAK-123" in caplog.text
