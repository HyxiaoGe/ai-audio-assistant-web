"""worker.redis_client 单测：任务进度发布收口到单一用户频道（不再双发 tasks:{id}）。

设计已下线 legacy /ws/tasks/{id}，task-progress 只走 user:{user_id}:updates 一条频道。
不依赖 live Redis：手写同步 fake 替换 get_sync_redis_client。
"""

from __future__ import annotations

from worker import redis_client


class _FakeSyncRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))


def test_publish_task_update_sync_only_user_channel(monkeypatch) -> None:
    fake = _FakeSyncRedis()
    monkeypatch.setattr(redis_client, "get_sync_redis_client", lambda: fake)

    redis_client.publish_task_update_sync("t1", "u1", "payload")

    assert fake.published == [("user:u1:updates", "payload")]
    # 收口后不再有 task-specific 频道
    assert all(ch != "tasks:t1" for ch, _ in fake.published)


def test_publish_message_sync_unchanged(monkeypatch) -> None:
    fake = _FakeSyncRedis()
    monkeypatch.setattr(redis_client, "get_sync_redis_client", lambda: fake)

    redis_client.publish_message_sync("user:u1:updates", "m")

    assert fake.published == [("user:u1:updates", "m")]
