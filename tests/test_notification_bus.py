"""app.services.notifications.bus 单测：频道命名 / RedisPubSubBus.publish / 单例。

不依赖 live Redis：手写同步 fake redis 替换 get_sync_redis_client（对齐 worker 同步上下文）。
"""

from __future__ import annotations

from app.services.notifications import bus as bus_module
from app.services.notifications.bus import user_channel


def test_user_channel_naming() -> None:
    assert user_channel("u1") == "user:u1:updates"
    assert user_channel("abc-123") == "user:abc-123:updates"


class _FakeSyncRedis:
    """最小同步 redis 替身：记录 publish 的 (channel, message)。"""

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))


def test_redis_pubsub_bus_publish_writes_user_channel_and_json(
    monkeypatch,
) -> None:
    fake = _FakeSyncRedis()
    monkeypatch.setattr(bus_module, "get_sync_redis_client", lambda: fake)

    bus = bus_module.RedisPubSubBus()
    envelope = {"kind": "notification", "data": {"id": "n1"}, "traceId": "t"}
    bus.publish("u1", envelope)

    assert len(fake.published) == 1
    channel, message = fake.published[0]
    assert channel == "user:u1:updates"
    # payload 是 envelope 的 JSON 序列化，且能原样回读
    import json

    assert json.loads(message) == envelope


def test_redis_pubsub_bus_publish_uses_ensure_ascii_false(monkeypatch) -> None:
    fake = _FakeSyncRedis()
    monkeypatch.setattr(bus_module, "get_sync_redis_client", lambda: fake)

    bus_module.RedisPubSubBus().publish("u1", {"kind": "notification", "data": {"t": "中文"}})

    _, message = fake.published[0]
    assert "中文" in message  # 未被转义成 \uXXXX


def test_get_event_bus_is_singleton() -> None:
    first = bus_module.get_event_bus()
    second = bus_module.get_event_bus()
    assert first is second
    assert isinstance(first, bus_module.RedisPubSubBus)
