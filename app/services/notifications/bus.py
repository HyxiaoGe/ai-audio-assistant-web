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
