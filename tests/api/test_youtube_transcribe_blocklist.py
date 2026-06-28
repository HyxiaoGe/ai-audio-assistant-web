"""Task 3: transcribe_video 创建前即时拦截已屏蔽频道。

三断言:
1. 屏蔽 channel_id 的 video → code == 40018(CHANNEL_BLOCKED)
2. create_task 全程未被调用(raise stub 验证)
3. 对照组未屏蔽 → create_task 被调用,返回 task_id

Harness 照 tests/api/test_youtube_blocklist_api.py 的 _make_app/_client 模式构造。
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
from app.api.v1 import youtube as youtube_module
from app.core import rate_limit as rate_limit_module
from app.core.exceptions import BusinessError
from app.core.response import error
from app.i18n.codes import ErrorCode
from app.services.youtube import blocklist_service
from app.services.youtube.blocklist_service import Blocklist
from app.services.youtube.subscription_service import YouTubeSubscriptionService
from app.services.youtube.video_service import YouTubeVideoService

_USER_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_VIDEO_ID = "dQw4w9WgXcQ"
_CHANNEL_ID = "UCblocked000000000000000000"


class _Result:
    def __init__(self, val: Any) -> None:
        self._val = val

    def scalar_one_or_none(self) -> Any:
        return self._val


class _FakeDB:
    """existing_task 查询返回 None(无已存在任务)。"""

    async def execute(self, _stmt: Any) -> _Result:
        return _Result(None)


class _AllowRateLimit:
    async def incr(self, _key: str) -> int:
        return 1

    async def expire(self, _key: str, _ttl: int) -> bool:
        return True


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(youtube_module.router, prefix="/api/v1")

    @app.exception_handler(BusinessError)
    async def _handle(_req: Request, exc: BusinessError) -> Any:
        return error(int(exc.code), exc.code.name)

    async def _db() -> AsyncIterator[_FakeDB]:
        yield _FakeDB()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=_USER_ID, email="u@ex.com")
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _patch_infra(monkeypatch: pytest.MonkeyPatch) -> None:
    """绕过 rate_limit Redis + stub is_connected + stub get_video_by_id。"""
    monkeypatch.setattr(rate_limit_module, "get_redis_client", lambda: _AllowRateLimit())
    monkeypatch.setattr(rate_limit_module, "time", types.SimpleNamespace(time=lambda: 1000.0))

    async def _connected(_self: Any, _db: Any, _uid: str) -> bool:
        return True

    monkeypatch.setattr(YouTubeSubscriptionService, "is_connected", _connected)

    fake_video = types.SimpleNamespace(
        video_id=_VIDEO_ID,
        channel_id=_CHANNEL_ID,
        title="Test Video Title",
    )

    async def _get_video(_self: Any, _db: Any, _uid: str, _vid: str) -> Any:
        return fake_video

    monkeypatch.setattr(YouTubeVideoService, "get_video_by_id", _get_video)


def _blocked_bl() -> Blocklist:
    return Blocklist(
        terms=frozenset(),
        channel_ids=frozenset([_CHANNEL_ID]),
        channel_names=frozenset(),
        channel_handles=frozenset(),
    )


def _empty_bl() -> Blocklist:
    return Blocklist(
        terms=frozenset(),
        channel_ids=frozenset(),
        channel_names=frozenset(),
        channel_handles=frozenset(),
    )


async def test_blocked_channel_returns_40018_and_does_not_call_create_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """断言①②:屏蔽频道 → 40018;create_task 未被调用。"""
    _patch_infra(monkeypatch)

    async def _get_blocklist(_db: Any) -> Blocklist:
        return _blocked_bl()

    monkeypatch.setattr(blocklist_service, "get_blocklist", _get_blocklist)

    # 若 create_task 被调用则本测试立刻 fail(断言②)
    from app.services.task_service import TaskService

    async def _must_not_be_called(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("TaskService.create_task must NOT be called for a blocked channel")

    monkeypatch.setattr(TaskService, "create_task", _must_not_be_called)

    async with _client(_make_app()) as client:
        body = (await client.post(f"/api/v1/youtube/videos/{_VIDEO_ID}/transcribe", json={})).json()

    # 断言①
    assert body["code"] == int(ErrorCode.CHANNEL_BLOCKED), (
        f"expected CHANNEL_BLOCKED({int(ErrorCode.CHANNEL_BLOCKED)}), got {body}"
    )


async def test_unblocked_channel_calls_create_task_and_returns_task_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """断言③:未屏蔽频道 → create_task 被调用,返回 task_id。"""
    _patch_infra(monkeypatch)

    async def _get_blocklist(_db: Any) -> Blocklist:
        return _empty_bl()

    monkeypatch.setattr(blocklist_service, "get_blocklist", _get_blocklist)

    _TASK_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"

    from app.services.task_service import TaskService

    async def _create(_db: Any, _user: Any, _data: Any, **kwargs: Any) -> Any:
        return types.SimpleNamespace(id=_TASK_ID)

    monkeypatch.setattr(TaskService, "create_task", _create)

    async with _client(_make_app()) as client:
        body = (await client.post(f"/api/v1/youtube/videos/{_VIDEO_ID}/transcribe", json={})).json()

    assert body["code"] == 0, f"expected 0, got {body}"
    assert body["data"]["task_id"] == _TASK_ID
