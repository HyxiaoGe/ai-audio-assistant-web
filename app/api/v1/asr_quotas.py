from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.exceptions import BusinessError
from app.core.response import success
from app.i18n.codes import ErrorCode
from app.models.user import User
from app.config import settings
from app.schemas.asr_quota import (
    AsrQuotaItem,
    AsrQuotaListResponse,
    AsrQuotaUpsertRequest,
    AsrQuotaUpsertResponse,
)
from app.services.asr_quota_service import list_effective_quotas, list_global_quotas, upsert_quota

router = APIRouter(prefix="/asr/quotas")


def _ensure_admin(user: User) -> None:
    admin_emails = [email.strip() for email in (settings.ADMIN_EMAILS or "").split(",") if email]
    if user.email not in admin_emails:
        raise BusinessError(ErrorCode.PERMISSION_DENIED)


def _resolve_quota_seconds(payload: AsrQuotaUpsertRequest) -> int:
    if payload.quota_seconds is not None:
        return payload.quota_seconds
    hours = payload.quota_hours or 0
    return int(hours * 3600)


@router.get("")
async def get_asr_quotas(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    rows = await list_effective_quotas(db, owner_user_id=str(user.id))
    items = [
        AsrQuotaItem(
            provider=row.provider,
            window_type=row.window_type,
            window_start=row.window_start,
            window_end=row.window_end,
            quota_seconds=row.quota_seconds,
            used_seconds=row.used_seconds,
            status=row.status,
        )
        for row in rows
    ]
    response = AsrQuotaListResponse(items=items)
    return success(data=jsonable_encoder(response))


@router.get("/global")
async def get_global_asr_quotas(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    _ensure_admin(user)
    rows = await list_global_quotas(db)
    items = [
        AsrQuotaItem(
            provider=row.provider,
            window_type=row.window_type,
            window_start=row.window_start,
            window_end=row.window_end,
            quota_seconds=row.quota_seconds,
            used_seconds=row.used_seconds,
            status=row.status,
        )
        for row in rows
    ]
    response = AsrQuotaListResponse(items=items)
    return success(data=jsonable_encoder(response))


@router.post("/refresh")
async def refresh_asr_quota(
    payload: AsrQuotaUpsertRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    quota_seconds = _resolve_quota_seconds(payload)
    row = await upsert_quota(
        db,
        provider=payload.provider,
        window_type=payload.window_type,
        quota_seconds=quota_seconds,
        reset=payload.reset,
        owner_user_id=str(user.id),
    )
    item = AsrQuotaItem(
        provider=row.provider,
        window_type=row.window_type,
        window_start=row.window_start,
        window_end=row.window_end,
        quota_seconds=row.quota_seconds,
        used_seconds=row.used_seconds,
        status=row.status,
    )
    response = AsrQuotaUpsertResponse(item=item)
    return success(data=jsonable_encoder(response))


@router.post("/refresh-global")
async def refresh_global_asr_quota(
    payload: AsrQuotaUpsertRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    _ensure_admin(user)
    quota_seconds = _resolve_quota_seconds(payload)
    row = await upsert_quota(
        db,
        provider=payload.provider,
        window_type=payload.window_type,
        quota_seconds=quota_seconds,
        reset=payload.reset,
        owner_user_id=None,
    )
    item = AsrQuotaItem(
        provider=row.provider,
        window_type=row.window_type,
        window_start=row.window_start,
        window_end=row.window_end,
        quota_seconds=row.quota_seconds,
        used_seconds=row.used_seconds,
        status=row.status,
    )
    response = AsrQuotaUpsertResponse(item=item)
    return success(data=jsonable_encoder(response))
