from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db, is_admin_user
from app.core.avatar_proxy import AvatarProxyError, fetch_avatar
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


@router.get("/avatar")
def proxy_avatar(url: str = Query(..., min_length=1)) -> Response:
    """同源头像代理：拉取白名单图床头像并强缓存，避免国内直连 Google/GitHub 图床慢/被墙。

    无需鉴权——头像 URL 本身是公开的，且浏览器 ``<img>`` 不会携带 Bearer。安全边界在
    ``avatar_proxy`` 内（https + host 白名单 + 不跟随重定向 + 体积/类型限制）。
    """
    try:
        body, content_type = fetch_avatar(url)
    except AvatarProxyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return Response(
        content=body,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


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
