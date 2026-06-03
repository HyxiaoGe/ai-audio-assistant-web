"""飞书渠道占位：接口/注册就位，实现留待飞书 PR（本期不实做）。

偏好矩阵默认 feishu 关，故未实现的 deliver 永不被 NotificationService 调用。
"""

from __future__ import annotations

from app.services.notifications.channels.base import BaseNotificationChannel, register_channel
from app.services.notifications.events import NotificationEvent


@register_channel("feishu")
class FeishuChannel(BaseNotificationChannel):
    name = "feishu"

    def deliver(self, session: object, event: NotificationEvent) -> None:
        raise NotImplementedError("feishu channel not implemented yet")
