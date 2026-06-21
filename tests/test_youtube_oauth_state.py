"""YouTube OAuth state(CSRF 防护)Redis 化 + fail-closed 单测(P3-6)。

原实现把 state 存进进程内 dict:多 worker 进程各存各的,授权请求与回调可能落在不同
进程 → 合法回调被误判 invalid_state;且无 TTL、重启即丢、永不过期(内存泄漏)。

改为 Redis(带 TTL + 一次性 getdel)。与限流相反,state 是 CSRF 校验,必须 **fail-closed**:
- 生成时 Redis 故障 → 直接抛错(给不出能被校验的 state,不如当场失败)
- 校验时 Redis 故障 → 当作无效 state 拒绝(绝不接受无法校验的 state = 杜绝 CSRF 绕过)
"""

from __future__ import annotations

from typing import Any

import pytest

import app.api.v1.youtube as youtube
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.set_calls: list[tuple[str, str, int | None]] = []

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.store[key] = value
        self.set_calls.append((key, value, ex))
        return True

    async def getdel(self, key: str) -> str | None:
        return self.store.pop(key, None)


class _BrokenRedis:
    async def set(self, *_a: Any, **_k: Any) -> bool:
        raise ConnectionError("redis down")

    async def getdel(self, *_a: Any, **_k: Any) -> str | None:
        raise ConnectionError("redis down")


def _patch_redis(monkeypatch: pytest.MonkeyPatch, fake: object) -> None:
    monkeypatch.setattr(youtube, "get_redis_client", lambda: fake)


async def test_generate_stores_state_in_redis_with_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRedis()
    _patch_redis(monkeypatch, fake)
    state = await youtube._generate_state("user-1")
    assert state  # 非空、不可猜
    key, value, ex = fake.set_calls[0]
    assert key == f"yt_oauth_state:{state}"
    assert value == "user-1"
    assert ex == youtube._OAUTH_STATE_TTL_SECONDS  # 带 TTL,不会永久残留


async def test_verify_returns_user_id_and_is_one_time(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRedis()
    _patch_redis(monkeypatch, fake)
    state = await youtube._generate_state("user-2")
    assert await youtube._verify_state(state) == "user-2"
    # 一次性:getdel 已删除,二次校验失败(防重放)
    assert await youtube._verify_state(state) is None


async def test_verify_unknown_state_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_redis(monkeypatch, _FakeRedis())
    assert await youtube._verify_state("never-issued") is None


async def test_generate_fails_closed_when_redis_down(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_redis(monkeypatch, _BrokenRedis())
    with pytest.raises(BusinessError) as ei:
        await youtube._generate_state("user-3")
    assert ei.value.code == ErrorCode.YOUTUBE_OAUTH_FAILED


async def test_verify_fails_closed_when_redis_down(monkeypatch: pytest.MonkeyPatch) -> None:
    # 校验路径 Redis 故障 → 当作无效 state 拒绝(绝不接受无法校验的 state)
    _patch_redis(monkeypatch, _BrokenRedis())
    assert await youtube._verify_state("any-state") is None
