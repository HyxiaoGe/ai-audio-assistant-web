"""regenerate 端点并发预检契约测试(裸 app + 假 session,模式同 test_summary_regenerate_rate_limit.py)。

端点在 send_task 前 best-effort 查 worker 锁是否存在:在→拒绝 SUMMARY_REGENERATING(即时提示);
不在→正常派发(status=queued)。这是 UX 快反馈;正确性由 worker 原子锁(Task 1)保证。
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
from app.core import rate_limit
from app.core.exceptions import BusinessError
from app.core.response import error
from app.i18n.codes import ErrorCode

_USER_ID = "11111111-1111-1111-1111-111111111111"
_TASK = "22222222-2222-2222-2222-222222222222"


class _Result:
    def __init__(self, val: Any) -> None:
        self._val = val

    def scalar_one_or_none(self) -> Any:
        return self._val


class _OkSession:
    """task 查询返回任务(归属本人)、transcript 查询返回存在 → 穿过端点前置校验进入并发预检。"""

    def __init__(self) -> None:
        self._calls = 0

    async def execute(self, _stmt: Any) -> _Result:
        self._calls += 1
        if self._calls == 1:
            return _Result(types.SimpleNamespace(id=_TASK, user_id=_USER_ID))
        return _Result(object())  # transcript 存在


class _AllowRateLimit:
    async def incr(self, _key: str) -> int:
        return 1

    async def expire(self, _key: str, _ttl: int) -> bool:
        return True


class _LockRedis:
    def __init__(self, exists_val: int) -> None:
        self._exists = exists_val

    async def exists(self, _key: str) -> int:
        return self._exists


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(summaries_module.router)

    @app.exception_handler(BusinessError)
    async def _handle(_req: Request, exc: BusinessError) -> Any:
        return error(int(exc.code), exc.code.name)

    async def _db() -> AsyncIterator[_OkSession]:
        yield _OkSession()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=_USER_ID, email="u@ex.com")
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _patch_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rate_limit, "get_redis_client", lambda: _AllowRateLimit())
    monkeypatch.setattr(rate_limit, "time", types.SimpleNamespace(time=lambda: 1000.0))


async def test_regenerate_rejected_when_lock_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_rate_limit(monkeypatch)
    monkeypatch.setattr(summaries_module, "get_redis_client", lambda: _LockRedis(1))
    async with _client(_make_app()) as client:
        resp = await client.post(f"/summaries/{_TASK}/regenerate", json={"summary_type": "overview"})
    assert resp.json()["code"] == int(ErrorCode.SUMMARY_REGENERATING)


async def test_regenerate_dispatches_when_no_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_rate_limit(monkeypatch)
    monkeypatch.setattr(summaries_module, "get_redis_client", lambda: _LockRedis(0))

    sent: list[dict[str, Any]] = []

    class _Spy:
        def send_task(self, name: str, **kwargs: Any) -> Any:
            sent.append({"name": name, **kwargs})
            return types.SimpleNamespace(id="task-x")

    import worker.celery_app as celery_mod

    monkeypatch.setattr(celery_mod, "celery_app", _Spy())

    async with _client(_make_app()) as client:
        resp = await client.post(f"/summaries/{_TASK}/regenerate", json={"summary_type": "overview"})
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["status"] == "queued"
    assert len(sent) == 1
    assert sent[0]["name"] == "worker.tasks.regenerate_summary"
