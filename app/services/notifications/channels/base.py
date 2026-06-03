"""通知渠道抽象 + 注册表。

复刻 app/core/registry.py 的 @register_service/ServiceRegistry 装饰器模式，但范围收窄到通知渠道：
- register_channel(name): 类装饰器，注册即实例化并缓存为单例。
- get_channel(name): 取渠道单例；未注册抛 ValueError。
每个渠道自带错误隔离由 NotificationService 逐个 try/except 保证（见 service.py）。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.notifications.events import NotificationEvent

logger = logging.getLogger(__name__)


class BaseNotificationChannel(ABC):
    """通知渠道基类。子类设 name 并实现同步 deliver。"""

    name: str

    @abstractmethod
    def deliver(self, session: object, event: NotificationEvent) -> None:
        """投递一条通知。worker 同步上下文 -> 同步签名（sync session）。"""
        ...


_CHANNEL_REGISTRY: dict[str, BaseNotificationChannel] = {}


def register_channel(name: str) -> Callable[[type[BaseNotificationChannel]], type[BaseNotificationChannel]]:
    """类装饰器：注册渠道并立即实例化为单例存入 _CHANNEL_REGISTRY。

    name 同时是注册表键；渠道类须设同值的 name 类属性（单一事实源，约定二者一致）。
    """

    def decorator(cls: type[BaseNotificationChannel]) -> type[BaseNotificationChannel]:
        _CHANNEL_REGISTRY[name] = cls()
        logger.info("Registered notification channel: %s (class=%s)", name, cls.__name__)
        return cls

    return decorator


def get_channel(name: str) -> BaseNotificationChannel:
    """取已注册渠道单例；未注册抛 ValueError。"""
    if name not in _CHANNEL_REGISTRY:
        raise ValueError(f"Notification channel '{name}' not registered. Available: {list(_CHANNEL_REGISTRY)}")
    return _CHANNEL_REGISTRY[name]
