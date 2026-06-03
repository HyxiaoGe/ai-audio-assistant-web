"""Notification API endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db
from app.core.exceptions import BusinessError
from app.core.response import success
from app.i18n.codes import ErrorCode
from app.models.notification import Notification
from app.schemas.common import PageResponse
from app.schemas.notification import NotificationResponse, NotificationStatsResponse

router = APIRouter(prefix="/notifications")


@router.get("")
async def list_notifications(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    unread_only: bool = Query(default=False),
    category: str | None = Query(default=None),
) -> JSONResponse:
    """List the current user's notifications (paginated, newest first)."""

    query = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    if category:
        query = query.where(Notification.category == category)

    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0

    query = query.order_by(Notification.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    notifications = result.scalars().all()

    items = [NotificationResponse.model_validate(notif) for notif in notifications]
    response = PageResponse(items=items, total=total, page=page, page_size=page_size)
    return success(data=jsonable_encoder(response))


@router.get("/stats")
async def get_notification_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """Notification counters: total + unread (no dismissed concept)."""

    total = await db.scalar(select(func.count()).where(Notification.user_id == user.id)) or 0
    unread = (
        await db.scalar(
            select(func.count()).where(
                and_(
                    Notification.user_id == user.id,
                    Notification.read_at.is_(None),
                )
            )
        )
        or 0
    )

    response = NotificationStatsResponse(total=total, unread=unread)
    return success(data=jsonable_encoder(response))


@router.patch("/read-all")
async def mark_all_read(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """Mark every unread notification of the user as read."""

    stmt = (
        update(Notification)
        .where(
            and_(
                Notification.user_id == user.id,
                Notification.read_at.is_(None),
            )
        )
        .values(read_at=datetime.now(UTC))
    )
    result = await db.execute(stmt)
    await db.commit()
    affected = getattr(result, "rowcount", None)
    if affected is None or affected < 0:
        affected = getattr(db, "_last_affected", 0)
    return success(data={"affected": int(affected), "unread": 0})


@router.patch("/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """Idempotently mark one notification as read; return the live unread count."""

    result = await db.execute(
        select(Notification).where(
            and_(
                Notification.id == notification_id,
                Notification.user_id == user.id,
            )
        )
    )
    notification = result.scalar_one_or_none()
    if not notification:
        raise BusinessError(ErrorCode.NOTIFICATION_NOT_FOUND)

    # 幂等：仅当前未读才写 read_at
    if notification.read_at is None:
        notification.read_at = datetime.now(UTC)
        await db.commit()

    unread = (
        await db.scalar(
            select(func.count()).where(
                and_(
                    Notification.user_id == user.id,
                    Notification.read_at.is_(None),
                )
            )
        )
        or 0
    )
    return success(data={"unread": int(unread)})
