from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, RedirectResponse, Response

from app.api.deps import get_current_user
from app.core.response import success
from app.config import settings
from app.core.smart_factory import SmartFactory
from app.schemas.user import UserProfileResponse

router = APIRouter(prefix="/users")


@router.get("/me")
async def get_me(user=Depends(get_current_user)) -> JSONResponse:
    response = UserProfileResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        avatar_url="/api/v1/users/me/avatar",
    )
    return success(data=response.model_dump())


@router.get("/me/avatar")
async def get_my_avatar(
    user=Depends(get_current_user),
) -> Response:
    if user.avatar_url and not user.avatar_url.startswith("http"):
        # 使用 SmartFactory 获取 storage 服务（默认使用 COS）
        storage = await SmartFactory.get_service("storage", provider="cos")
        expires_in = settings.UPLOAD_PRESIGN_EXPIRES or 300
        url = storage.generate_presigned_url(user.avatar_url, expires_in)
        return RedirectResponse(url, status_code=307)

    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='128' height='128' viewBox='0 0 128 128'>"
        "<rect width='128' height='128' rx='64' fill='#E5E7EB'/>"
        "<circle cx='64' cy='50' r='22' fill='#9CA3AF'/>"
        "<path d='M24 112c8-24 24-36 40-36s32 12 40 36' fill='#9CA3AF'/>"
        "</svg>"
    )
    return Response(content=svg, media_type="image/svg+xml")
