"""/ws/user 经 EventBus.subscribe 取流 + 心跳；legacy /ws/tasks/{id} 已下线。

不连真实 Redis：注入假 pubsub。路由下线用 router 路径集合断言（无需起 WS 连接）。
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from app.api.v1 import ws as ws_module


def _ws_paths() -> set[str]:
    return {getattr(r, "path", "") for r in ws_module.router.routes}


def test_legacy_tasks_endpoint_removed() -> None:
    paths = _ws_paths()
    assert "/ws/user" in paths
    assert "/ws/tasks/{task_id}" not in paths


def test_task_progress_handler_symbol_gone() -> None:
    # legacy 处理函数本体已删除
    assert not hasattr(ws_module, "task_progress")


class _FakeSyncPubSub:
    """同步 redis pubsub 替身：吐一条 message 后持续返回 None。"""

    def __init__(self, payloads: list[str]) -> None:
        self._payloads = list(payloads)
        self.unsubscribed = False
        self.closed = False

    def get_message(self, ignore_subscribe_messages: bool = True, timeout: float = 1.0) -> Any:
        if self._payloads:
            return {"type": "message", "data": self._payloads.pop(0)}
        return None

    def unsubscribe(self, *_a: Any) -> None:
        self.unsubscribed = True

    def close(self) -> None:
        self.closed = True


class _FakeBus:
    def __init__(self, pubsub: _FakeSyncPubSub) -> None:
        self._pubsub = pubsub
        self.subscribed_user: str | None = None

    def subscribe(self, user_id: str) -> _FakeSyncPubSub:
        self.subscribed_user = user_id
        return self._pubsub


class _FakeWebSocket:
    """记录 send_text 的最小 WS 替身。"""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)


async def test_forward_pubsub_subscribes_via_bus_and_forwards(monkeypatch) -> None:
    pubsub = _FakeSyncPubSub(['{"kind":"notification","data":{"id":"n1"}}'])
    bus = _FakeBus(pubsub)
    monkeypatch.setattr(ws_module, "get_event_bus", lambda: bus)

    ws = _FakeWebSocket()
    task = asyncio.create_task(ws_module._forward_pubsub(ws, "u1"))
    # 给 forwarder 把那条 message 取出并转发的机会
    await asyncio.sleep(0.2)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert bus.subscribed_user == "u1"
    assert '{"kind":"notification","data":{"id":"n1"}}' in ws.sent
    assert pubsub.unsubscribed is True
    assert pubsub.closed is True


class _DisconnectingWebSocket:
    """鉴权握手阶段就断开的 WS 替身:receive_text 抛 WebSocketDisconnect。"""

    headers: dict[str, str] = {}

    async def receive_text(self) -> str:
        from fastapi import WebSocketDisconnect

        raise WebSocketDisconnect(code=1006)


async def test_authenticate_in_band_returns_none_on_disconnect() -> None:
    """客户端在带内鉴权期间断开(刷新/关页)应被吞掉,返回 (None, None),

    而不是把 WebSocketDisconnect 抛成未捕获的 ASGI 异常刷错误日志。
    """
    user, error_code = await ws_module._authenticate_in_band(_DisconnectingWebSocket(), None, "zh", "trace-id")
    assert user is None
    assert error_code is None
