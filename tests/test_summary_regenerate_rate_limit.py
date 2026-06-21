"""regenerate_summary 端点限流契约测试(裸 app + 假 session,patch rate_limit 的 redis/time)。

regenerate 在 worker 里同时烧 LLM + 付费 Seedream 配图,登录用户可循环触发放大花销。
按 compare 端点同款 per-user 固定窗口限流(scope="summary_regenerate")兜住 enqueue 速率。

技巧:fake session 对任务查询返回 None → body 抛 TASK_NOT_FOUND。限流器是 Depends,在 body
之前执行,故前 limit 次穿过限流器拿 TASK_NOT_FOUND、第 (limit+1) 次被限流器拦下拿
RATE_LIMIT_EXCEEDED——无需 stub celery/transcript。
"""

from __future__ import annotations

import types
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport

from app.api.deps import CurrentUser, get_current_user, get_db
from app.api.v1 import summaries as summaries_module
from app.config import settings
from app.core import rate_limit
from app.core.exceptions import BusinessError
from app.core.response import error
from app.i18n.codes import ErrorCode

_USER_ID = "11111111-1111-1111-1111-111111111111"
_TASK = "22222222-2222-2222-2222-222222222222"


class _NoneResult:
    def scalar_one_or_none(self) -> None:
        return None


class _FakeSession:
    async def execute(self, _stmt: Any) -> _NoneResult:
        return _NoneResult()


class _FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, _key: str, _ttl: int) -> bool:
        return True


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(summaries_module.router)

    @app.exception_handler(BusinessError)
    async def _handle(_req: Request, exc: BusinessError) -> Any:
        return error(int(exc.code), exc.code.name)

    async def _db() -> AsyncIterator[_FakeSession]:
        yield _FakeSession()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=_USER_ID, email="u@ex.com")
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_regenerate_rate_limited_after_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRedis()  # 单一共享实例,跨调用累加计数
    monkeypatch.setattr(rate_limit, "get_redis_client", lambda: fake)
    monkeypatch.setattr(rate_limit, "time", types.SimpleNamespace(time=lambda: 1000.0))  # 固定分桶

    limit = settings.RATE_LIMIT_SUMMARY_REGENERATE_PER_MIN
    payload = {"summary_type": "overview"}
    codes: list[int] = []
    async with _client(_make_app()) as client:
        for _ in range(limit + 1):
            resp = await client.post(f"/summaries/{_TASK}/regenerate", json=payload)
            codes.append(resp.json()["code"])

    # 前 limit 次穿过限流器到 body(任务不存在→TASK_NOT_FOUND)
    assert codes[:limit] == [int(ErrorCode.TASK_NOT_FOUND)] * limit
    # 第 (limit+1) 次被限流器在 body 之前拦下
    assert codes[limit] == int(ErrorCode.RATE_LIMIT_EXCEEDED)


async def test_regenerate_rate_limit_is_per_user_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    """限流用独立 scope="summary_regenerate",不与 compare 共享预算。"""
    seen_keys: list[str] = []

    class _SpyRedis(_FakeRedis):
        async def incr(self, key: str) -> int:
            seen_keys.append(key)
            return await super().incr(key)

    monkeypatch.setattr(rate_limit, "get_redis_client", lambda: _SpyRedis())
    monkeypatch.setattr(rate_limit, "time", types.SimpleNamespace(time=lambda: 1000.0))
    async with _client(_make_app()) as client:
        await client.post(f"/summaries/{_TASK}/regenerate", json={"summary_type": "overview"})
    assert any(k.startswith(f"rl:summary_regenerate:{_USER_ID}:") for k in seen_keys)
