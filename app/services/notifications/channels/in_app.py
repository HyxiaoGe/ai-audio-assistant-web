"""In-app 渠道：持久化 notifications 行 + 经 EventBus 实时推送。

- 写库带 dedup_key；撞部分唯一索引 -> IntegrityError -> rollback 并吞为「已通知」（原子去重，无竞态）。
- 顺带用后端默认语言(zh)渲染 title/message 写入，作为过渡期老前端/REST 兜底 + 前端缺某 type key 的安全网。
- 落库成功后用统一 notification 信封 publish 到用户频道。
"""

from __future__ import annotations

import logging

from sqlalchemy.exc import IntegrityError

from app.i18n.notifications import render_notification
from app.models.notification import Notification
from app.schemas.notification import NotificationResponse
from app.services.notifications.bus import get_event_bus
from app.services.notifications.channels.base import BaseNotificationChannel, register_channel
from app.services.notifications.events import NotificationEvent, notification_envelope
from app.services.notifications.types import NOTIFICATION_TEMPLATES

logger = logging.getLogger(__name__)

_FALLBACK_LOCALE = "zh"


@register_channel("in_app")
class InAppChannel(BaseNotificationChannel):
    name = "in_app"

    def deliver(self, session: object, event: NotificationEvent) -> None:
        tmpl = NOTIFICATION_TEMPLATES[event.type]
        title, message = render_notification(tmpl.i18n_key, event.params, _FALLBACK_LOCALE)

        notif = Notification(
            user_id=event.user_id,
            task_id=event.task_id,
            type=str(event.type),
            category=str(event.category),
            priority=str(event.priority),
            title=title,
            message=message,
            action_url=event.action_url,
            extra_data=dict(event.params),
            dedup_key=event.dedup_key,
        )
        session.add(notif)
        try:
            session.commit()
        except IntegrityError:
            # dedup_key 撞部分唯一索引：已通知过，吞掉、不推送。
            session.rollback()
            logger.debug("Notification dedup hit, skipping: type=%s dedup_key=%s", event.type, event.dedup_key)
            return

        response = NotificationResponse.model_validate(notif)
        envelope = notification_envelope(response.model_dump(mode="json"), trace_id="")
        try:
            get_event_bus().publish(event.user_id, envelope)
        except Exception as exc:  # noqa: BLE001  行已落库，推送失败靠前端兜底重取，优雅降级
            logger.warning("EventBus publish failed (row persisted), relying on client refetch: %s", exc)
