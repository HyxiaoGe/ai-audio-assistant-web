from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.response import success
from app.models.user import User
from app.services.stats_service import StatsService

router = APIRouter(prefix="/stats")


@router.get("/services/overview")
async def get_service_usage_overview(
    time_range: Optional[str] = Query(default=None),
    start_date: Optional[datetime] = Query(default=None),
    end_date: Optional[datetime] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    service = StatsService(db, user)
    data = await service.get_service_usage_overview(time_range, start_date, end_date)
    return success(data=jsonable_encoder(data))


@router.get("/tasks/overview")
async def get_task_overview(
    time_range: Optional[str] = Query(default=None),
    start_date: Optional[datetime] = Query(default=None),
    end_date: Optional[datetime] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    service = StatsService(db, user)
    data = await service.get_task_overview(time_range, start_date, end_date)
    return success(data=jsonable_encoder(data))

