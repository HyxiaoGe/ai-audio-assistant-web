"""EventBus 传输接缝。

生产侧（Celery worker，同步上下文）经 ``EventBus.publish`` 把统一信封发到用户频道；
消费侧（FastAPI /ws）经 ``EventBus.subscribe`` 取流（订阅侧接入在 Phase 5 的 ws.py）。
当前唯一实现 ``RedisPubSubBus`` 走 Redis pub/sub；未来可换 RedisStreamBus / MqBus 而业务无感。
频道命名内化在 ``user_channel``，收口原先散落各处的裸字符串。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod

from worker.redis_client import get_sync_redis_client


def user_channel(user_id: str) -> str:
    """用户全局更新频道名（任务进度 + 通知共用）。"""
    return f"user:{user_id}:updates"


class EventBus(ABC):
    @abstractmethod
    def publish(self, user_id: str, envelope: dict) -> None: ...

    @abstractmethod
    def subscribe(self, user_id: str): ...


class RedisPubSubBus(EventBus):
    """当前唯一实现：Redis pub/sub。生产侧用同步 redis（worker 上下文无 asyncio）。"""

    def publish(self, user_id: str, envelope: dict) -> None:
        client = get_sync_redis_client()
        message = json.dumps(envelope, ensure_ascii=False)
        client.publish(user_channel(user_id), message)

    def subscribe(self, user_id: str):
        """消费侧由 FastAPI /ws 在 Phase 5 接入；当前实现订阅同频道。"""
        client = get_sync_redis_client()
        pubsub = client.pubsub()
        pubsub.subscribe(user_channel(user_id))
        return pubsub


_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """配置化的 EventBus 单例（当前固定 RedisPubSubBus）。"""
    global _event_bus
    if _event_bus is None:
        _event_bus = RedisPubSubBus()
    return _event_bus
