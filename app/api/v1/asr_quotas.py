from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, is_admin_user
from app.config import settings
from app.core.exceptions import BusinessError
from app.core.response import success
from app.i18n.codes import ErrorCode
from app.models.user import User
from app.schemas.asr_quota import (
    AsrAdminOverviewResponse,
    AsrFreeQuotaStatus,
    AsrProviderUsage,
    AsrQuotaItem,
    AsrQuotaUpsertRequest,
    AsrQuotaUpsertResponse,
    AsrUsageSummary,
    AsrUserFreeQuotaResponse,
)
from app.services.asr_quota_service import (
    get_admin_asr_overview,
    get_user_total_usage,
    upsert_quota,
)

router = APIRouter(prefix="/asr/quotas")


def _ensure_admin(user: User) -> None:
    if not is_admin_user(user):
        raise BusinessError(ErrorCode.PERMISSION_DENIED)


def _resolve_quota_seconds(payload: AsrQuotaUpsertRequest) -> float:
    if payload.quota_seconds is not None:
        return payload.quota_seconds
    hours = payload.quota_hours or 0
    return hours * 3600


@router.get("")
async def get_asr_quotas(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """获取用户免费 ASR 额度信息

    管理员用户不受配额限制，返回 is_unlimited=True
    """
    used_seconds = await get_user_total_usage(db, user_id=str(user.id))

    # 管理员不受配额限制
    if is_admin_user(user):
        response = AsrUserFreeQuotaResponse(
            free_quota_seconds=-1,  # -1 表示无限制
            free_quota_hours=-1,
            used_seconds=round(used_seconds, 2),
            used_hours=round(used_seconds / 3600, 2),
            remaining_seconds=-1,
            remaining_hours=-1,
            is_unlimited=True,
        )
        return success(data=jsonable_encoder(response))

    free_quota = float(settings.DEFAULT_USER_FREE_QUOTA_SECONDS)
    remaining = max(0, free_quota - used_seconds)

    response = AsrUserFreeQuotaResponse(
        free_quota_seconds=free_quota,
        free_quota_hours=round(free_quota / 3600, 2),
        used_seconds=round(used_seconds, 2),
        used_hours=round(used_seconds / 3600, 2),
        remaining_seconds=round(remaining, 2),
        remaining_hours=round(remaining / 3600, 2),
        is_unlimited=False,
    )
    return success(data=jsonable_encoder(response))


@router.post("/refresh")
async def refresh_asr_quota(
    payload: AsrQuotaUpsertRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    _ensure_admin(user)
    quota_seconds = _resolve_quota_seconds(payload)
    row = await upsert_quota(
        db,
        provider=payload.provider,
        variant=payload.variant,
        window_type=payload.window_type,
        quota_seconds=quota_seconds,
        reset=payload.reset,
        owner_user_id=str(user.id),
        window_start=payload.window_start,
        window_end=payload.window_end,
        used_seconds=payload.used_seconds,
    )
    item = AsrQuotaItem(
        provider=row.provider,
        variant=row.variant,
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
        variant=payload.variant,
        window_type=payload.window_type,
        quota_seconds=quota_seconds,
        reset=payload.reset,
        owner_user_id=None,
        window_start=payload.window_start,
        window_end=payload.window_end,
        used_seconds=payload.used_seconds,
    )
    item = AsrQuotaItem(
        provider=row.provider,
        variant=row.variant,
        window_type=row.window_type,
        window_start=row.window_start,
        window_end=row.window_end,
        quota_seconds=row.quota_seconds,
        used_seconds=row.used_seconds,
        status=row.status,
    )
    response = AsrQuotaUpsertResponse(item=item)
    return success(data=jsonable_encoder(response))


@router.get("/admin/overview")
async def get_admin_overview(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """获取管理员 ASR 概览

    分离两个业务关注点：
    - free_quota_status: 免费额度状态（只关心免费额度的使用情况）
    - providers_usage: 所有提供商的付费使用统计
    """
    _ensure_admin(user)

    result = await get_admin_asr_overview(db)

    # 免费额度状态
    free_quota_status = [
        AsrFreeQuotaStatus(
            provider=p.provider,
            variant=p.variant,
            display_name=p.display_name,
            free_quota_hours=round(p.free_quota_seconds / 3600, 2),
            used_hours=round(p.used_seconds / 3600, 2),
            remaining_hours=round(max(0, p.free_quota_seconds - p.used_seconds) / 3600, 2),
            usage_percent=round(
                (
                    min(100, p.used_seconds / p.free_quota_seconds * 100)
                    if p.free_quota_seconds > 0
                    else 0
                ),
                1,
            ),
            reset_period=p.reset_period,
            period_start=p.period_start,
            period_end=p.period_end,
        )
        for p in result.free_quota_status
    ]

    # 所有提供商的付费使用统计
    providers_usage = [
        AsrProviderUsage(
            provider=p.provider,
            variant=p.variant,
            display_name=p.display_name,
            cost_per_hour=p.cost_per_hour,
            paid_hours=round(p.paid_seconds / 3600, 2),
            paid_cost=round(p.paid_cost, 4),
            is_enabled=p.is_enabled,
        )
        for p in result.providers_usage
    ]

    summary = AsrUsageSummary(
        total_used_hours=result.summary["total_used_hours"],
        total_free_hours=result.summary["total_free_hours"],
        total_paid_hours=result.summary["total_paid_hours"],
        total_cost=result.summary["total_cost"],
    )

    response = AsrAdminOverviewResponse(
        summary=summary,
        free_quota_status=free_quota_status,
        providers_usage=providers_usage,
    )
    return success(data=jsonable_encoder(response))
