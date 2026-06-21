"""POST /api/v1/client-errors:匿名接收前端未捕获错误,仅落结构化日志(P3-3)。

log-only sink:不发飞书、不存库、不引新密钥。匿名(错误常发生在登录前/登录页),
按 IP 限流挡日志刷屏,字段截断 + body 大小守卫防超长灌爆日志/内存。
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.i18n.codes import ErrorCode


def _client() -> TestClient:
    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


def _records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [rec for rec in caplog.records if rec.name == "app.api.client_errors"]


def test_accepts_and_logs_report(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="app.api.client_errors"):
        r = _client().post(
            "/api/v1/client-errors",
            json={
                "message": "Boom: undefined is not a function",
                "source": "error_boundary",
                "stack": "at Foo (app.js:1:2)",
                "url": "https://x/explore",
                "digest": "abc123",
                "release": "build-2026-06-21",
            },
        )
    assert r.status_code == 200
    assert r.json()["code"] == 0
    logged = _records(caplog)
    assert logged, "client error should be logged"
    blob = logged[-1].getMessage()
    assert "Boom: undefined is not a function" in blob
    assert "error_boundary" in blob


def test_oversized_fields_are_truncated_not_rejected(caplog: pytest.LogCaptureFixture) -> None:
    # 字段超长应截断而非拒绝——尽力留下错误,不因超长丢掉整份报告。
    # 字段各自超过 cap(message 2000 / stack 8000),但整体 body ~35KB < 64KB body 守卫,
    # 以确保测的是字段截断而非 body 守卫。
    with caplog.at_level(logging.WARNING, logger="app.api.client_errors"):
        r = _client().post(
            "/api/v1/client-errors",
            json={"message": "x" * 5_000, "stack": "x" * 30_000},
        )
    assert r.status_code == 200
    assert r.json()["code"] == 0
    blob = _records(caplog)[-1].getMessage()
    assert "x" * 100 in blob  # 报告确实落了
    # 截断后 message≤2000 + stack≤8000 ≈ 10k;未截断会是 ~35k。<20k 能区分两者。
    assert len(blob) < 20_000


def test_oversized_raw_body_is_rejected() -> None:
    # 单请求 body 过大(>64KB)在解析前被 content-length 守卫挡掉(防灌爆内存)。
    big = "y" * (70 * 1024)
    r = _client().post("/api/v1/client-errors", json={"message": big})
    assert r.status_code == 200  # 统一错误信封是 HTTP 200
    assert r.json()["code"] == int(ErrorCode.INVALID_PARAMETER)


def test_rate_limited_returns_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.core.rate_limit as rate_limit_module

    class _Saturated:
        async def incr(self, _key: str) -> int:
            return 10**9  # 直接越限

        async def expire(self, _key: str, _ttl: int) -> None:
            return None

    monkeypatch.setattr(rate_limit_module, "get_redis_client", lambda: _Saturated())
    r = _client().post("/api/v1/client-errors", json={"message": "x"})
    assert r.status_code == 200
    assert r.json()["code"] == int(ErrorCode.RATE_LIMIT_EXCEEDED)
