"""P0-5:阻塞式 YouTube/Google SDK 调用必须 offload 出事件循环(asyncio.to_thread)。

单 worker uvicorn 下,async 路由里内联的同步跨境往返会卡住所有并发请求。本测试用
线程 id 捕获验证阻塞调用跑在**非事件循环线程**上——RED 时(内联调用)与协程同线程,
GREEN 时(to_thread)是线程池的另一个线程。

覆盖两处:①热路径 task_service._get_youtube_video_info 的 get_video_full_info;
②oauth_callback 的 exchange_code + get_my_channel。
"""

from __future__ import annotations

import sys
import threading
import types
from typing import Any

import pytest

from app.api.v1 import youtube as youtube_module
from app.services.task_service import TaskService
from app.services.youtube import data_service as data_service_module

_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


class _Result:
    def __init__(self, val: Any) -> None:
        self._val = val

    def scalar_one_or_none(self) -> Any:
        return self._val


class _FakeDB:
    """按调用顺序返回预置结果(_get_youtube_video_info:先查 video 缓存,后查 account)。"""

    def __init__(self, results: list[Any]) -> None:
        self._results = results
        self.calls = 0

    async def execute(self, _stmt: Any) -> Any:
        r = self._results[self.calls]
        self.calls += 1
        return r


async def test_get_video_info_offloads_blocking_api_call(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, int] = {}

    class _StubDataService:
        def __init__(self, _credentials: Any) -> None:
            pass

        def get_video_full_info(self, video_id: str) -> dict[str, Any]:
            captured["thread"] = threading.get_ident()
            return {"video_id": video_id, "title": "t"}

    # _get_youtube_video_info 内部 `from app.services.youtube.data_service import YouTubeDataService`
    monkeypatch.setattr(data_service_module, "YouTubeDataService", _StubDataService)

    account = types.SimpleNamespace(access_token="tok", refresh_token="ref")
    db = _FakeDB([_Result(None), _Result(account)])  # 缓存未命中 → 走 API 分支

    loop_tid = threading.get_ident()
    info = await TaskService._get_youtube_video_info(db, "user-1", _URL)  # type: ignore[arg-type]

    assert info is not None and info.video_id == "dQw4w9WgXcQ"
    assert "thread" in captured, "get_video_full_info 未被调用(路径没走到 API 分支)"
    assert captured["thread"] != loop_tid, "阻塞 API 调用仍跑在事件循环线程上(未 offload)"


async def test_oauth_callback_offloads_blocking_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, int] = {}

    class _StubOAuth:
        def exchange_code(self, _code: str) -> tuple[str, str, None]:
            captured["exchange"] = threading.get_ident()
            return ("at", "rt", None)

        def build_credentials(self, **_kw: Any) -> object:
            return object()

    class _StubData:
        def __init__(self, _credentials: Any) -> None:
            pass

        def get_my_channel(self) -> dict[str, str]:
            captured["channel"] = threading.get_ident()
            return {"id": "chan-1"}

    class _StubSub:
        async def save_youtube_account(self, **_kw: Any) -> None:
            return None

    async def _ok_state(_s: str) -> str:  # _verify_state 现为 async(Redis 化,P3-6)
        return "user-1"

    monkeypatch.setattr(youtube_module, "_verify_state", _ok_state)
    monkeypatch.setattr(youtube_module, "YouTubeOAuthService", _StubOAuth)
    monkeypatch.setattr(youtube_module, "YouTubeDataService", _StubData)
    monkeypatch.setattr(youtube_module, "YouTubeSubscriptionService", _StubSub)
    # 拦掉函数内 `from worker.tasks.sync_youtube_subscriptions import sync_youtube_subscriptions`
    fake_mod = types.SimpleNamespace(sync_youtube_subscriptions=types.SimpleNamespace(delay=lambda **_k: None))
    monkeypatch.setitem(sys.modules, "worker.tasks.sync_youtube_subscriptions", fake_mod)

    loop_tid = threading.get_ident()
    await youtube_module.oauth_callback(code="c", state="s", db=object())  # type: ignore[arg-type]

    assert captured.get("exchange") and captured["exchange"] != loop_tid, "exchange_code 未 offload"
    assert captured.get("channel") and captured["channel"] != loop_tid, "get_my_channel 未 offload"
