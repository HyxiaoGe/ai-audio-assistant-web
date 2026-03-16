from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db, is_admin_user
from app.core.response import success
from app.core.security import extract_bearer_token
from app.models.user import UserProfile
from app.schemas.user import (
    NotificationPreferences,
    TaskDefaultsPreferences,
    UiPreferences,
    UserPreferencesResponse,
    UserPreferencesUpdateRequest,
    UserProfileResponse,
)
from app.services.user_preferences import get_user_preferences, update_user_preferences

router = APIRouter(prefix="/users")


@router.get("/me")
async def get_me(user: CurrentUser = Depends(get_current_user)) -> JSONResponse:
    response = UserProfileResponse(
        id=user.id,
        email=user.email,
        name=None,  # name is now managed by auth-service
        avatar_url=None,  # avatar is now managed by auth-service
        is_admin=is_admin_user(user),
    )
    return success(data=response.model_dump())


@router.get("/me/preferences")
async def get_preferences(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    token = extract_bearer_token(authorization)
    profile = await db.get(UserProfile, UUID(user.id))
    preferences = await get_user_preferences(profile, token)
    task_defaults = TaskDefaultsPreferences.model_validate(preferences["task_defaults"])
    ui = UiPreferences.model_validate(preferences["ui"])
    notifications = NotificationPreferences.model_validate(preferences["notifications"])
    response = UserPreferencesResponse(
        task_defaults=task_defaults,
        ui=ui,
        notifications=notifications,
    )
    return success(data=response.model_dump())


@router.patch("/me/preferences")
async def update_preferences(
    payload: UserPreferencesUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    token = extract_bearer_token(authorization)
    profile = await db.get(UserProfile, UUID(user.id))
    preferences = await update_user_preferences(db, profile, payload, token)
    task_defaults = TaskDefaultsPreferences.model_validate(preferences["task_defaults"])
    ui = UiPreferences.model_validate(preferences["ui"])
    notifications = NotificationPreferences.model_validate(preferences["notifications"])
    response = UserPreferencesResponse(
        task_defaults=task_defaults,
        ui=ui,
        notifications=notifications,
    )
    return success(data=response.model_dump())
