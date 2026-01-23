"""ASR 用量查询 API

提供 ASR 调用用量的明细查询和汇总统计功能。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.response import success
from app.models.asr_usage import ASRUsage
from app.models.user import User
from app.schemas.asr_usage import (
    ASRUsageItem,
    ASRUsageListResponse,
    ASRUsageSummaryItem,
    ASRUsageSummaryResponse,
)

router = APIRouter(prefix="/asr/usage", tags=["asr-usage"])


@router.get("")
async def get_asr_usage_list(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    provider: Optional[str] = Query(default=None, description="按提供商筛选"),
    variant: Optional[str] = Query(default=None, description="按变体筛选 (file, file_fast)"),
    status: Optional[str] = Query(default=None, description="按状态筛选 (success, failed)"),
    start_date: Optional[datetime] = Query(default=None, description="开始日期"),
    end_date: Optional[datetime] = Query(default=None, description="结束日期"),
    task_id: Optional[str] = Query(default=None, description="按任务 ID 筛选"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """获取 ASR 用量明细列表（分页）

    支持按提供商、变体、状态、时间范围筛选。
    """
    query = select(ASRUsage).where(ASRUsage.user_id == str(user.id))

    if provider:
        query = query.where(ASRUsage.provider == provider)
    if variant:
        query = query.where(ASRUsage.variant == variant)
    if status:
        query = query.where(ASRUsage.status == status)
    if start_date:
        query = query.where(ASRUsage.created_at >= start_date)
    if end_date:
        query = query.where(ASRUsage.created_at <= end_date)
    if task_id:
        query = query.where(ASRUsage.task_id == task_id)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Paginate
    query = query.order_by(ASRUsage.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    rows = result.scalars().all()

    items = [
        ASRUsageItem(
            id=str(row.id),
            user_id=str(row.user_id),
            task_id=str(row.task_id) if row.task_id else None,
            provider=row.provider,
            variant=row.variant,
            external_task_id=row.external_task_id,
            duration_seconds=row.duration_seconds,
            estimated_cost=row.estimated_cost,
            actual_cost=row.actual_cost,
            audio_url=row.audio_url,
            audio_format=row.audio_format,
            status=row.status,
            error_code=row.error_code,
            error_message=row.error_message,
            processing_time_ms=row.processing_time_ms,
            created_at=row.created_at,
            # 免费额度分拆字段
            free_quota_consumed=row.free_quota_consumed,
            paid_duration_seconds=row.paid_duration_seconds,
            actual_paid_cost=row.actual_paid_cost,
        )
        for row in rows
    ]

    response = ASRUsageListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )
    return success(data=jsonable_encoder(response))


@router.get("/summary")
async def get_asr_usage_summary(
    start_date: Optional[datetime] = Query(default=None, description="开始日期"),
    end_date: Optional[datetime] = Query(default=None, description="结束日期"),
    provider: Optional[str] = Query(default=None, description="按提供商筛选"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """获取 ASR 用量汇总统计（按提供商分组）

    返回指定时间范围内各提供商的用量汇总，包括：
    - 调用次数（成功/失败）
    - 总时长
    - 总成本
    - 平均处理时间
    """
    # Build base query with filters
    base_conditions = [ASRUsage.user_id == str(user.id)]
    if start_date:
        base_conditions.append(ASRUsage.created_at >= start_date)
    if end_date:
        base_conditions.append(ASRUsage.created_at <= end_date)
    if provider:
        base_conditions.append(ASRUsage.provider == provider)

    # Aggregate by provider and variant
    query = (
        select(
            ASRUsage.provider,
            ASRUsage.variant,
            func.count().label("total_count"),
            func.sum(func.cast(ASRUsage.status == "success", Integer)).label("success_count"),
            func.sum(func.cast(ASRUsage.status == "failed", Integer)).label("failed_count"),
            func.sum(ASRUsage.duration_seconds).label("total_duration_seconds"),
            func.sum(ASRUsage.estimated_cost).label("total_estimated_cost"),
            func.sum(ASRUsage.actual_cost).label("total_actual_cost"),
            func.avg(ASRUsage.processing_time_ms).label("avg_processing_time_ms"),
            # 免费额度分拆汇总
            func.sum(ASRUsage.free_quota_consumed).label("total_free_quota_consumed"),
            func.sum(ASRUsage.paid_duration_seconds).label("total_paid_duration_seconds"),
            func.sum(ASRUsage.actual_paid_cost).label("total_actual_paid_cost"),
        )
        .where(*base_conditions)
        .group_by(ASRUsage.provider, ASRUsage.variant)
        .order_by(ASRUsage.provider, ASRUsage.variant)
    )

    result = await db.execute(query)
    rows = result.all()

    items = []
    total_duration = 0.0
    total_cost = 0.0
    total_count = 0
    total_free_consumed = 0.0
    total_paid_duration = 0.0
    total_paid_cost = 0.0

    for row in rows:
        item = ASRUsageSummaryItem(
            provider=row.provider,
            variant=row.variant,
            total_count=row.total_count or 0,
            success_count=row.success_count or 0,
            failed_count=row.failed_count or 0,
            total_duration_seconds=row.total_duration_seconds or 0.0,
            total_estimated_cost=row.total_estimated_cost or 0.0,
            total_actual_cost=row.total_actual_cost,
            avg_processing_time_ms=(
                float(row.avg_processing_time_ms) if row.avg_processing_time_ms else None
            ),
            # 免费额度分拆汇总
            total_free_quota_consumed=row.total_free_quota_consumed or 0.0,
            total_paid_duration_seconds=row.total_paid_duration_seconds or 0.0,
            total_actual_paid_cost=row.total_actual_paid_cost or 0.0,
        )
        items.append(item)
        total_duration += item.total_duration_seconds
        total_cost += item.total_estimated_cost
        total_count += item.total_count
        total_free_consumed += item.total_free_quota_consumed
        total_paid_duration += item.total_paid_duration_seconds
        total_paid_cost += item.total_actual_paid_cost

    response = ASRUsageSummaryResponse(
        items=items,
        period_start=start_date,
        period_end=end_date,
        total_duration_seconds=total_duration,
        total_estimated_cost=total_cost,
        total_count=total_count,
        total_free_quota_consumed=total_free_consumed,
        total_paid_duration_seconds=total_paid_duration,
        total_actual_paid_cost=total_paid_cost,
    )
    return success(data=jsonable_encoder(response))
