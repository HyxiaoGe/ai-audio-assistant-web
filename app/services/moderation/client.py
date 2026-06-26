"""CMS(content-moderation-service)审核客户端。

只做两件事:把文本同步发给 CMS /v1/moderate,把任何失败(超时/网络/非200/熔断打开/
CMS 自降级/解析异常)归一化为 action="degraded"。策略(三态映射)不在这里,在 gate。

镜像 app/services/llm/image_service.py 的容错范式:内层 @retry 仅瞬时重试 → @_circuit_breaker
受保护调用 → 顶层 moderate() 捕获 httpx/熔断异常落到 degraded,故对调用方"永不抛"。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal

import httpx

from app.config import settings
from app.core.fault_tolerance import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerOpenError,
    RetryConfig,
    retry,
)
from app.core.monitoring import monitor

logger = logging.getLogger(__name__)

ModerationAction = Literal["pass", "review", "block", "degraded"]
_KNOWN_ACTIONS: frozenset[str] = frozenset({"pass", "review", "block"})


@dataclass(frozen=True)
class ModerationResult:
    """CMS 一次判定的归一化结果。action=degraded 表示调用失败/不可信,交 gate 按 flag 处理。"""

    action: ModerationAction
    cloud_label: str | None = None
    cms_trace_id: str | None = None
    latency_ms: int | None = None


_DEGRADED = ModerationResult(action="degraded")


class ModerationClient:
    """调 CMS /v1/moderate。失败归一化为 degraded,对调用方永不抛。"""

    _circuit_breaker = CircuitBreaker.get_or_create(
        "moderation",
        CircuitBreakerConfig(
            failure_threshold=5,
            success_threshold=2,
            timeout=60.0,
            expected_exception=(httpx.HTTPError,),
        ),
    )

    def __init__(self) -> None:
        self._base_url = settings.MODERATION_SERVICE_URL.rstrip("/")
        self._api_key = settings.MODERATION_API_KEY
        self._timeout = settings.MODERATION_TIMEOUT

    def _headers(self, request_id: str | None) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        if request_id:
            headers["X-Request-Id"] = request_id
        return headers

    @retry(
        RetryConfig(max_attempts=2, initial_delay=0.5, max_delay=2.0),
        exceptions=(httpx.TimeoutException, httpx.NetworkError),
    )
    @monitor("moderation", "moderation_client")
    async def _post_moderate(self, text: str, scene: str, request_id: str | None) -> ModerationResult:
        """实际 POST /v1/moderate 并解析信封。

        故意放行底层 httpx 异常(超时/网络/HTTPStatusError)以便 @retry 重试瞬时故障、
        熔断器计入失败;顶层 moderate() 再统一归一化为 degraded。
        """
        payload = {"text": text, "scene": scene}
        async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as client:
            resp = await client.post("/v1/moderate", json=payload, headers=self._headers(request_id))
            resp.raise_for_status()
            envelope = resp.json()

        data = envelope.get("data") or {}
        cms_trace_id = envelope.get("traceId")
        if data.get("degraded"):
            return ModerationResult(action="degraded", cms_trace_id=cms_trace_id)
        action = data.get("action")
        if action not in _KNOWN_ACTIONS:
            logger.warning("moderation: unexpected action=%r scene=%s", action, scene)
            return ModerationResult(action="degraded", cms_trace_id=cms_trace_id)
        return ModerationResult(
            action=action,  # type: ignore[arg-type]  # 已用 _KNOWN_ACTIONS 收窄
            cloud_label=data.get("cloud"),
            cms_trace_id=cms_trace_id,
        )

    @_circuit_breaker.protected
    async def _guarded_moderate(self, text: str, scene: str, request_id: str | None) -> ModerationResult:
        return await self._post_moderate(text, scene, request_id)

    async def moderate(self, text: str, *, scene: str, request_id: str | None) -> ModerationResult:
        """对外唯一入口。任何失败(熔断打开/超时/网络/非200/解析)→ degraded,永不抛。"""
        start = time.monotonic()
        try:
            result = await self._guarded_moderate(text, scene, request_id)
        except (CircuitBreakerOpenError, httpx.HTTPError) as exc:
            logger.warning("moderation: degraded scene=%s err=%s", scene, type(exc).__name__)
            result = _DEGRADED
        except Exception as exc:  # noqa: BLE001
            # 守住"永不抛"契约:任何意外(JSONDecodeError / 非 dict 信封 AttributeError / 其它)也归 degraded。
            logger.warning("moderation: degraded(unexpected) scene=%s err=%s", scene, type(exc).__name__)
            result = _DEGRADED
        latency_ms = int((time.monotonic() - start) * 1000)
        return ModerationResult(
            action=result.action,
            cloud_label=result.cloud_label,
            cms_trace_id=result.cms_trace_id,
            latency_ms=latency_ms,
        )
