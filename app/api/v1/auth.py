from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.response import success
from app.schemas.auth import AuthSyncRequest, AuthSyncResponse
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth")


@router.post("/sync")
async def sync_auth_account(
    data: AuthSyncRequest,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    user_id = await AuthService.sync_account(
        db,
        provider=data.provider,
        provider_account_id=data.provider_account_id,
        email=data.email,
        name=data.name,
        avatar_url=data.avatar_url,
    )
    response = AuthSyncResponse(user_id=user_id)
    return success(data=jsonable_encoder(response))
