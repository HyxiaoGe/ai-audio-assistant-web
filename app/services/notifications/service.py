"""通知服务唯一收口：NotificationService.notify。

producer（worker/服务）只调这一个入口，不再手搓 ORM 行 / WS payload。
流程：
  ① 取 type 模板元数据（category/priority/i18n_key/允许渠道）
  ② action_url = action_url or (/tasks/{task_id} if task_id else None)
  ③ 命中渠道 = resolve_enabled_channels(用户偏好, type, 模板允许渠道)
  ④ 逐个 get_channel(ch).deliver(...)，每个单独 try/except 错误隔离
  ⑤ best-effort：整体 try/except，永不向 producer 抛（延续 _mark_failed 铁律）
worker 同步上下文 -> 同步签名（sync session）。
"""

from __future__ import annotations

import logging

from app.models.user import UserProfile
from app.schemas.user import NotificationPreferences
from app.services.notifications.channels.base import get_channel
from app.services.notifications.events import NotificationEvent
from app.services.notifications.types import NOTIFICATION_TEMPLATES, NotificationType
from app.services.user_preferences import get_app_preferences, resolve_enabled_channels

logger = logging.getLogger(__name__)

# 确保 in_app / feishu 渠道在 import 时完成注册（@register_channel 副作用）。
from app.services.notifications.channels import feishu as _feishu  # noqa: E402,F401
from app.services.notifications.channels import in_app as _in_app  # noqa: E402,F401


def _load_preferences(session: object, user_id: str) -> NotificationPreferences:
    """从用户 profile 读通知偏好矩阵；缺失/异常给安全默认（in_app 全开、feishu 全关）。"""
    profile = session.get(UserProfile, user_id)
    if profile is None:
        return NotificationPreferences()
    raw = get_app_preferences(profile).get("notifications")
    if not isinstance(raw, dict):
        return NotificationPreferences()
    return NotificationPreferences.model_validate(raw)


class NotificationService:
    @staticmethod
    def notify(
        session: object,
        *,
        type: NotificationType,
        user_id: str,
        params: dict,
        task_id: str | None = None,
        action_url: str | None = None,
        dedup_key: str | None = None,
    ) -> None:
        """收口入口。best-effort：整体吞错，永不向 producer 抛。"""
        try:
            tmpl = NOTIFICATION_TEMPLATES[type]
            resolved_action_url = action_url or (f"/tasks/{task_id}" if task_id else None)

            prefs = _load_preferences(session, user_id)
            enabled = resolve_enabled_channels(prefs, str(type), tmpl.channels)

            event = NotificationEvent(
                type=type,
                user_id=user_id,
                params=params,
                category=tmpl.category,
                priority=tmpl.priority,
                action_url=resolved_action_url,
                task_id=task_id,
                dedup_key=dedup_key,
            )

            for ch in enabled:
                try:
                    get_channel(ch).deliver(session, event)
                except Exception as exc:  # noqa: BLE001  逐渠道隔离：一个渠道挂不影响其他
                    logger.warning("Notification channel '%s' deliver failed: %s", ch, exc, exc_info=True)
        except Exception as exc:  # noqa: BLE001  best-effort 整体兜底：通知绝不拖垮 producer
            logger.error("NotificationService.notify failed (swallowed): type=%s user=%s: %s", type, user_id, exc)
