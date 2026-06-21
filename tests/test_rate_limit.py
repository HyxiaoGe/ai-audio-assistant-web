"""app.core.rate_limit 单测：固定窗口、按用户限流、Redis 故障 fail-open。

无需 live Redis/DB；用手写的异步 fake 替换 get_redis_client。
"""

from __future__ import annotations

import logging
import types

import pytest

from app.api.deps import CurrentUser
from app.core import rate_limit
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode


class _FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expire_calls: list[tuple[str, int]] = []

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, ttl: int) -> bool:
        self.expire_calls.append((key, ttl))
        return True


class _BrokenRedis:
    async def incr(self, key: str) -> int:
        raise RuntimeError("redis down")

    async def expire(self, key: str, ttl: int) -> bool:  # pragma: no cover - never reached
        raise RuntimeError("redis down")


def _patch_redis(monkeypatch: pytest.MonkeyPatch, fake: object) -> None:
    monkeypatch.setattr(rate_limit, "get_redis_client", lambda: fake)


def _pin_time(monkeypatch: pytest.MonkeyPatch, value: float) -> None:
    monkeypatch.setattr(rate_limit, "time", types.SimpleNamespace(time=lambda: value))


async def test_under_limit_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_redis(monkeypatch, _FakeRedis())
    for _ in range(3):
        await rate_limit._check("k", limit=3, window_seconds=60)


async def test_over_limit_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_redis(monkeypatch, _FakeRedis())
    for _ in range(3):
        await rate_limit._check("k", limit=3, window_seconds=60)
    with pytest.raises(BusinessError) as ei:
        await rate_limit._check("k", limit=3, window_seconds=60)
    assert ei.value.code == ErrorCode.RATE_LIMIT_EXCEEDED
    assert ei.value.kwargs.get("retry_after") == "60"


async def test_expire_set_only_on_first_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRedis()
    _patch_redis(monkeypatch, fake)
    await rate_limit._check("k2", limit=5, window_seconds=60)
    assert fake.expire_calls == [("k2", 60)]
    await rate_limit._check("k2", limit=5, window_seconds=60)
    assert len(fake.expire_calls) == 1


async def test_distinct_keys_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_redis(monkeypatch, _FakeRedis())
    # limit=1 trips on the SECOND hit of a key; distinct keys must not interfere.
    await rate_limit._check("rl:a:u1:0", limit=1, window_seconds=60)
    await rate_limit._check("rl:b:u2:0", limit=1, window_seconds=60)


async def test_redis_failure_fails_open(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _patch_redis(monkeypatch, _BrokenRedis())
    rate_limit._failopen_logged_scopes.clear()
    with caplog.at_level(logging.ERROR, logger="app.core.rate_limit"):
        await rate_limit._check("k", limit=1, window_seconds=60)  # no raise
    assert "fail-open" in caplog.text


async def test_dependency_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_redis(monkeypatch, _FakeRedis())
    _pin_time(monkeypatch, 1000.0)  # stable bucket across both calls
    dep = rate_limit.rate_limit(limit=1, window_seconds=60, scope="t")
    user = CurrentUser(id="u", email="e")
    await dep(user=user)
    with pytest.raises(BusinessError):
        await dep(user=user)


async def test_dependency_query_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_redis(monkeypatch, _FakeRedis())
    _pin_time(monkeypatch, 1000.0)
    dep = rate_limit.rate_limit_query(limit=1, window_seconds=60, scope="t")
    user = CurrentUser(id="u", email="e")
    await dep(user=user)
    with pytest.raises(BusinessError):
        await dep(user=user)
