"""LiteLLM Proxy 流式调用的熔断保护测试。

非流式入口 ``_call_api`` 在熔断器 OPEN 时快速失败（D3-002）；流式入口 ``_stream_api``
必须对齐——否则熔断打开后流式仍会真发 HTTP 并自带 3 次重试，对已知故障的后端形成
重试风暴。本测试用 httpx.MockTransport 探针验证 OPEN 态下「不发起任何 HTTP」。
"""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest

from app.core.exceptions import BusinessError
from app.core.fault_tolerance import CircuitState
from app.i18n.codes import ErrorCode
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


async def test_stream_fast_fails_when_circuit_open(monkeypatch: pytest.MonkeyPatch) -> None:
    http_called = {"value": False}

    def handler(request: httpx.Request) -> httpx.Response:
        http_called["value"] = True
        return httpx.Response(200, text="data: [DONE]\n\n")

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient.__init__
    monkeypatch.setattr(
        httpx.AsyncClient,
        "__init__",
        lambda self, *a, **k: original(self, *a, **{**k, "transport": transport}),
    )

    svc = _make_service()
    breaker = svc._circuit_breaker
    # 强制 OPEN 且刚失败（elapsed < timeout，不进入 HALF_OPEN 探测）。
    breaker.state = CircuitState.OPEN
    breaker.last_failure_time = datetime.now()
    try:
        with pytest.raises(BusinessError) as ei:
            async for _ in svc._stream_api({"model": "m", "messages": []}):
                pass
        assert ei.value.code == ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE
        assert http_called["value"] is False  # 快速失败，未发起任何 HTTP
    finally:
        breaker.reset()


from app.services.llm.proxy import ProxyLLMService as _Svc


def _svc() -> _Svc:
    return _Svc(config={"base_url": "http://litellm.test", "api_key": "k", "model": "m", "max_tokens": 16})


def test_default_service_uses_shared_proxy_breaker():
    svc = _svc()
    assert svc._breaker is _Svc._circuit_breaker
    assert svc._breaker.name == "proxy_llm"


def test_use_dedicated_circuit_breaker_isolates_from_shared():
    svc = _svc()
    svc.use_dedicated_circuit_breaker("polish_llm")
    assert svc._breaker.name == "polish_llm"
    # 与共享的 proxy_llm 是不同对象:polish 打开不牵连 summarize
    assert svc._breaker is not _Svc._circuit_breaker
    # 同配置(失败阈值一致)
    assert svc._breaker.config.failure_threshold == _Svc._circuit_breaker.config.failure_threshold
