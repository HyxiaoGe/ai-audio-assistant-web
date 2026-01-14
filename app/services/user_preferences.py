from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models.user import User
from app.schemas.user import UserPreferencesUpdateRequest

DEFAULT_TASK_DEFAULTS: dict[str, object] = {
    "language": "auto",
    "summary_style": "meeting",
    "enable_speaker_diarization": True,
    "asr_provider": None,
    "asr_variant": None,
    "llm_provider": None,
    "llm_model_id": None,
}

DEFAULT_NOTIFICATIONS: dict[str, object] = {
    "task_completed": True,
    "task_failed": True,
}


def _normalize_settings(settings: object) -> dict[str, object]:
    if isinstance(settings, dict):
        return dict(settings)
    return {}


def get_user_preferences(user: User) -> dict[str, object]:
    settings = _normalize_settings(user.settings)
    prefs = settings.get("preferences")
    if not isinstance(prefs, dict):
        prefs = {}

    task_defaults = dict(DEFAULT_TASK_DEFAULTS)
    stored_task_defaults = prefs.get("task_defaults")
    if isinstance(stored_task_defaults, dict):
        task_defaults.update(stored_task_defaults)

    notifications = dict(DEFAULT_NOTIFICATIONS)
    stored_notifications = prefs.get("notifications")
    if isinstance(stored_notifications, dict):
        notifications.update(stored_notifications)

    return {
        "task_defaults": task_defaults,
        "ui": {"locale": user.locale, "timezone": user.timezone},
        "notifications": notifications,
    }


async def update_user_preferences(
    db: AsyncSession, user: User, payload: UserPreferencesUpdateRequest
) -> dict[str, object]:
    settings = _normalize_settings(user.settings)
    prefs = settings.get("preferences")
    if not isinstance(prefs, dict):
        prefs = {}

    if payload.task_defaults is not None:
        updates = payload.task_defaults.model_dump(exclude_unset=True)
        current = prefs.get("task_defaults")
        if not isinstance(current, dict):
            current = {}
        current.update(updates)
        prefs["task_defaults"] = current

    if payload.notifications is not None:
        updates = payload.notifications.model_dump(exclude_unset=True)
        current = prefs.get("notifications")
        if not isinstance(current, dict):
            current = {}
        current.update(updates)
        prefs["notifications"] = current

    if payload.ui is not None:
        updates = payload.ui.model_dump(exclude_unset=True)
        if "locale" in updates:
            user.locale = updates["locale"]
        if "timezone" in updates:
            user.timezone = updates["timezone"]

    settings["preferences"] = prefs
    user.settings = settings
    flag_modified(user, "settings")

    await db.commit()
    await db.refresh(user)

    return get_user_preferences(user)
