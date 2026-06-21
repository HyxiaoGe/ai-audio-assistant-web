"""rate_limit_by_ip(匿名公开端点按 IP 固定窗口限流)单元测试。

P3-4:不再信任 X-Forwarded-For 最左 token(客户端可伪造、轮换即绕过预算)。优先 CF-Connecting-IP
(cloudflared 是唯一公网入口、客户端无法经 CF 伪造);XFF 默认关(RATE_LIMIT_TRUSTED_PROXY_HOPS=0,
跳数取决于不在仓里的 nginx 配置,猜错=全塌进一桶=自我 DoS),仅显式配置可信跳数才从右数。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest
from fastapi import Depends, FastAPI, Request
from httpx import ASGITransport

import app.core.rate_limit as rate_limit_module
from app.core.exceptions import BusinessError
from app.core.response import error
from app.i18n.codes import ErrorCode


class _FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expirations: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, ttl: int) -> None:
        self.expirations[key] = ttl


def _make_app(limit: int) -> FastAPI:
    app = FastAPI()
    dep = rate_limit_module.rate_limit_by_ip(limit=limit, scope="probe")

    @app.get("/probe")
    async def probe(_rl: None = Depends(dep)) -> dict[str, int]:
        return {"ok": 1}

    @app.exception_handler(BusinessError)
    async def _handle(_req: Request, exc: BusinessError) -> Any:
        return error(int(exc.code), exc.code.name)

    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_blocks_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit_module, "get_redis_client", lambda: fake)
    async with _client(_make_app(limit=2)) as client:
        assert (await client.get("/probe")).json()["ok"] == 1
        assert (await client.get("/probe")).json()["ok"] == 1
        third = (await client.get("/probe")).json()
        assert third["code"] == int(ErrorCode.RATE_LIMIT_EXCEEDED)


async def test_uses_cf_connecting_ip_over_xff(monkeypatch: pytest.MonkeyPatch) -> None:
    """CF-Connecting-IP 优先于(可伪造的)X-Forwarded-For。"""
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit_module, "get_redis_client", lambda: fake)
    async with _client(_make_app(limit=1)) as client:
        resp = await client.get(
            "/probe",
            headers={"cf-connecting-ip": "9.9.9.9", "x-forwarded-for": "1.1.1.1"},
        )
        assert resp.json()["ok"] == 1
    assert any(":ip:9.9.9.9:" in k for k in fake.counts)
    assert not any(":ip:1.1.1.1:" in k for k in fake.counts)  # 不信任 XFF


async def test_buckets_by_cf_connecting_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit_module, "get_redis_client", lambda: fake)
    async with _client(_make_app(limit=1)) as client:
        ok_a = (await client.get("/probe", headers={"cf-connecting-ip": "1.1.1.1"})).json()
        ok_b = (await client.get("/probe", headers={"cf-connecting-ip": "2.2.2.2"})).json()
        assert ok_a["ok"] == 1 and ok_b["ok"] == 1  # 不同 IP 各自独立窗口
        blocked = (await client.get("/probe", headers={"cf-connecting-ip": "1.1.1.1"})).json()
        assert blocked["code"] == int(ErrorCode.RATE_LIMIT_EXCEEDED)
    assert any(":ip:1.1.1.1:" in k for k in fake.counts)
    assert any(":ip:2.2.2.2:" in k for k in fake.counts)


async def test_spoofed_leftmost_xff_does_not_evade(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认(hops=0)无 CF 头时忽略 XFF → 轮换最左 token 不能绕过预算,全塌进 client.host 一桶。"""
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit_module, "get_redis_client", lambda: fake)
    async with _client(_make_app(limit=1)) as client:
        first = (await client.get("/probe", headers={"x-forwarded-for": "6.6.6.6"})).json()
        second = (await client.get("/probe", headers={"x-forwarded-for": "7.7.7.7"})).json()
        assert first["ok"] == 1
        assert second["code"] == int(ErrorCode.RATE_LIMIT_EXCEEDED)
    # 没有任何伪造的 XFF token 进了 key
    assert not any(":ip:6.6.6.6:" in k or ":ip:7.7.7.7:" in k for k in fake.counts)


async def test_trusted_xff_offset_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """显式 RATE_LIMIT_TRUSTED_PROXY_HOPS=1 时从右数第 1 跳取(ProxyFix x_for 语义)。"""
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit_module, "get_redis_client", lambda: fake)
    monkeypatch.setattr(rate_limit_module.settings, "RATE_LIMIT_TRUSTED_PROXY_HOPS", 1)
    async with _client(_make_app(limit=1)) as client:
        resp = await client.get("/probe", headers={"x-forwarded-for": "5.5.5.5, 10.0.0.9"})
        assert resp.json()["ok"] == 1
    assert any(":ip:10.0.0.9:" in k for k in fake.counts)  # 最右(第 1 跳)


async def test_trusted_xff_insufficient_parts_falls_back_to_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """配置 hops=2 但 XFF 不足 2 跳时回落 socket 地址,不误信可伪造的最左 token。

    这正是 hops 配置高于真实代理深度时的边界:`len(parts) >= hops` 守卫不成立 → 走 socket
    回落,而不是把不足跳数里那个客户端可控的 token 当成 IP。
    """
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit_module, "get_redis_client", lambda: fake)
    monkeypatch.setattr(rate_limit_module.settings, "RATE_LIMIT_TRUSTED_PROXY_HOPS", 2)
    async with _client(_make_app(limit=1)) as client:
        resp = await client.get("/probe", headers={"x-forwarded-for": "9.9.9.9"})  # 只有 1 跳
        assert resp.json()["ok"] == 1
    assert not any(":ip:9.9.9.9:" in k for k in fake.counts)  # 不足跳数的 XFF token 未被当作 IP


async def test_fail_open_on_redis_error_logs_once(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    def _boom() -> Any:
        raise ConnectionError("redis down")

    monkeypatch.setattr(rate_limit_module, "get_redis_client", _boom)
    rate_limit_module._failopen_logged_scopes.clear()
    with caplog.at_level(logging.ERROR, logger="app.core.rate_limit"):
        async with _client(_make_app(limit=1)) as client:
            for _ in range(3):
                assert (await client.get("/probe")).json()["ok"] == 1  # fail-open
    # 同一 scope 的 fail-open 只记一次 ERROR(不刷屏)
    failopen_errors = [r for r in caplog.records if "fail-open" in r.getMessage()]
    assert len(failopen_errors) == 1
