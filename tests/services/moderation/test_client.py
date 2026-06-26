from __future__ import annotations

import httpx
import pytest

from app.core.fault_tolerance import CircuitBreaker, CircuitBreakerOpenError
from app.services.moderation.client import ModerationClient, ModerationResult


@pytest.fixture(autouse=True)
def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.moderation.client.settings.MODERATION_SERVICE_URL", "http://cms.test")
    monkeypatch.setattr("app.services.moderation.client.settings.MODERATION_API_KEY", "test-key")
    monkeypatch.setattr("app.services.moderation.client.settings.MODERATION_TIMEOUT", 3.0)
    # 每个测试前重置共享熔断器,避免上个用例的失败计数泄漏到本用例
    CircuitBreaker.get_or_create("moderation").reset()


def _patch_transport(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient.__init__
    monkeypatch.setattr(
        httpx.AsyncClient,
        "__init__",
        lambda self, *a, **k: original(self, *a, **{**k, "transport": transport}),
    )


def _envelope(action: str, *, cloud: str | None = "Normal", degraded: bool = False) -> dict:
    return {
        "code": 0,
        "message": "ok",
        "data": {"action": action, "cloud": cloud, "local_shadow": None, "degraded": degraded},
        "traceId": "cms-trace-123",
    }


@pytest.mark.asyncio
async def test_pass_parsed_and_headers_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_envelope("pass"))

    _patch_transport(monkeypatch, handler)
    result = await ModerationClient().moderate("hello", scene="search_query", request_id="req-9")

    assert isinstance(result, ModerationResult)
    assert result.action == "pass"
    assert result.cms_trace_id == "cms-trace-123"
    # 契约头:X-API-Key + 透传 X-Request-Id
    assert captured[0].headers["X-API-Key"] == "test-key"
    assert captured[0].headers["X-Request-Id"] == "req-9"
    assert captured[0].url.path == "/v1/moderate"
    body = captured[0].read().decode()
    assert '"scene":"search_query"' in body
    assert '"text":"hello"' in body


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["review", "block"])
async def test_review_and_block_parsed(monkeypatch: pytest.MonkeyPatch, action: str) -> None:
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, json=_envelope(action)))
    result = await ModerationClient().moderate("x", scene="ugc_publish", request_id=None)
    assert result.action == action


@pytest.mark.asyncio
async def test_response_degraded_flag_maps_to_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    # CMS 自身降级:200 但 data.degraded=true → 归一化 degraded
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, json=_envelope("pass", degraded=True)))
    result = await ModerationClient().moderate("x", scene="search_query", request_id=None)
    assert result.action == "degraded"


@pytest.mark.asyncio
async def test_non_200_maps_to_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_transport(monkeypatch, lambda req: httpx.Response(503, json={"code": 50000, "message": "down"}))
    result = await ModerationClient().moderate("x", scene="search_query", request_id=None)
    assert result.action == "degraded"


@pytest.mark.asyncio
async def test_timeout_maps_to_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    _patch_transport(monkeypatch, handler)
    result = await ModerationClient().moderate("x", scene="search_query", request_id=None)
    assert result.action == "degraded"


@pytest.mark.asyncio
async def test_unknown_action_maps_to_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    # 200 但 action 不在 pass/review/block 白名单 → 防御式 degraded,不崩
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, json=_envelope("weird")))
    result = await ModerationClient().moderate("x", scene="search_query", request_id=None)
    assert result.action == "degraded"


@pytest.mark.asyncio
async def test_circuit_open_maps_to_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    # 熔断器打开路径:直接让受保护调用抛 CircuitBreakerOpenError,确认被归一化为 degraded
    async def _raise_open(*_a: object, **_k: object) -> ModerationResult:
        raise CircuitBreakerOpenError("open")

    monkeypatch.setattr(ModerationClient, "_guarded_moderate", _raise_open)
    result = await ModerationClient().moderate("x", scene="search_query", request_id=None)
    assert result.action == "degraded"
