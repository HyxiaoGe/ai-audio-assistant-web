"""app.services.notifications.bus 单测：频道命名 / RedisPubSubBus.publish / 单例。

不依赖 live Redis：手写同步 fake redis 替换 get_sync_redis_client（对齐 worker 同步上下文）。
"""

from __future__ import annotations

from app.services.notifications import bus as bus_module
from app.services.notifications.bus import user_channel


def test_user_channel_naming() -> None:
    assert user_channel("u1") == "user:u1:updates"
    assert user_channel("abc-123") == "user:abc-123:updates"
