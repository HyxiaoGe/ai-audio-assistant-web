from __future__ import annotations

import logging
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.api.deps import CurrentUser
from app.config import settings
from app.models.user import UserProfile
from app.schemas.user import UserPreferencesUpdateRequest

logger = logging.getLogger("app.user_preferences")

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


def _normalize_settings(app_settings: object) -> dict[str, object]:
    if isinstance(app_settings, dict):
        return dict(app_settings)
    return {}


async def _get_auth_preferences(token: str) -> dict[str, object]:
    """Fetch UI preferences (locale, timezone) from auth-service."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{settings.AUTH_SERVICE_URL}/auth/userinfo",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                prefs = data.get("preferences", {})
                return {
                    "locale": prefs.get("locale", "zh"),
                    "timezone": prefs.get("timezone", "Asia/Shanghai"),
                }
    except Exception as exc:
        logger.warning("Failed to fetch auth preferences: %s", exc)
    return {"locale": "zh", "timezone": "Asia/Shanghai"}


async def _update_auth_preferences(token: str, locale: str | None, timezone: str | None) -> None:
    """Update UI preferences on auth-service."""
    payload: dict[str, str] = {}
    if locale is not None:
        payload["locale"] = locale
    if timezone is not None:
        payload["timezone"] = timezone
    if not payload:
        return

    try:
        async with httpx.AsyncClient() as client:
            await client.patch(
                f"{settings.AUTH_SERVICE_URL}/auth/profile",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
                timeout=5.0,
            )
    except Exception as exc:
        logger.warning("Failed to update auth preferences: %s", exc)


def get_app_preferences(profile: UserProfile) -> dict[str, object]:
    """Get app-level preferences from local profile."""
    app_settings = _normalize_settings(profile.app_settings)
    prefs = app_settings.get("preferences")
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
        "notifications": notifications,
    }


async def get_user_preferences(profile: UserProfile, token: str) -> dict[str, object]:
    """Get combined preferences: app-level (local) + UI (auth-service)."""
    app_prefs = get_app_preferences(profile)
    ui_prefs = await _get_auth_preferences(token)
    return {
        "task_defaults": app_prefs["task_defaults"],
        "ui": ui_prefs,
        "notifications": app_prefs["notifications"],
    }


async def update_user_preferences(
    db: AsyncSession, profile: UserProfile, payload: UserPreferencesUpdateRequest, token: str
) -> dict[str, object]:
    """Update preferences: app-level locally, UI via auth-service."""
    app_settings = _normalize_settings(profile.app_settings)
    prefs = app_settings.get("preferences")
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

    app_settings["preferences"] = prefs
    profile.app_settings = app_settings
    flag_modified(profile, "app_settings")

    await db.commit()
    await db.refresh(profile)

    # Delegate UI preferences to auth-service
    if payload.ui is not None:
        ui_updates = payload.ui.model_dump(exclude_unset=True)
        await _update_auth_preferences(
            token,
            locale=ui_updates.get("locale"),
            timezone=ui_updates.get("timezone"),
        )

    return await get_user_preferences(profile, token)
