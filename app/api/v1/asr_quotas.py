from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.response import success
from app.models.user import User
from app.schemas.asr_quota import (
    AsrQuotaItem,
    AsrQuotaListResponse,
    AsrQuotaUpsertRequest,
    AsrQuotaUpsertResponse,
)
from app.services.asr_quota_service import list_active_quotas, upsert_quota

router = APIRouter(prefix="/asr/quotas")


@router.get("")
async def get_asr_quotas(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    _ = user
    rows = await list_active_quotas(db)
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
    _ = user
    row = await upsert_quota(
        db,
        provider=payload.provider,
        window_type=payload.window_type,
        quota_seconds=payload.quota_seconds,
        reset=payload.reset,
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
