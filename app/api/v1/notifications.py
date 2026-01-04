"""Notification API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.response import success
from app.models.notification import Notification
from app.models.user import User
from app.schemas.common import PageResponse
from app.schemas.notification import NotificationResponse, NotificationStatsResponse

router = APIRouter(prefix="/notifications")


@router.get("")
async def list_notifications(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    unread_only: bool = Query(default=False),
    category: str = Query(default=None),
) -> JSONResponse:
    """Get user's notifications with pagination."""

    # Build query
    query = select(Notification).where(Notification.user_id == user.id)

    if unread_only:
        query = query.where(Notification.read_at.is_(None))

    if category:
        query = query.where(Notification.category == category)

    # Exclude dismissed notifications
    query = query.where(Notification.dismissed_at.is_(None))

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0

    # Get paginated results
    query = query.order_by(Notification.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    notifications = result.scalars().all()

    # Convert to response
    items = [NotificationResponse.model_validate(notif) for notif in notifications]

    response = PageResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )

    return success(data=jsonable_encoder(response))


@router.get("/stats")
async def get_notification_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Get notification statistics (total, unread, and dismissed count)."""

    # Get total count (exclude dismissed)
    total_query = select(func.count()).where(
        and_(
            Notification.user_id == user.id,
            Notification.dismissed_at.is_(None),
        )
    )
    total = await db.scalar(total_query) or 0

    # Get unread count
    unread_query = select(func.count()).where(
        and_(
            Notification.user_id == user.id,
            Notification.read_at.is_(None),
            Notification.dismissed_at.is_(None),
        )
    )
    unread = await db.scalar(unread_query) or 0

    # Get dismissed count
    dismissed_query = select(func.count()).where(
        and_(
            Notification.user_id == user.id,
            Notification.dismissed_at.isnot(None),
        )
    )
    dismissed = await db.scalar(dismissed_query) or 0

    response = NotificationStatsResponse(
        total=total,
        unread=unread,
        dismissed=dismissed,
    )

    return success(data=jsonable_encoder(response))


@router.patch("/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Mark a notification as read."""

    from datetime import datetime, timezone

    query = select(Notification).where(
        and_(
            Notification.id == notification_id,
            Notification.user_id == user.id,
        )
    )
    result = await db.execute(query)
    notification = result.scalar_one_or_none()

    if not notification:
        from app.core.exceptions import BusinessError
        from app.i18n.codes import ErrorCode

        raise BusinessError(ErrorCode.NOTIFICATION_NOT_FOUND)

    # Set read_at to current time
    notification.read_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(notification)

    response = NotificationResponse.model_validate(notification)
    return success(data=jsonable_encoder(response))


@router.patch("/read-all")
async def mark_all_read(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Mark all notifications as read."""

    from datetime import datetime, timezone

    from sqlalchemy import update

    stmt = (
        update(Notification)
        .where(
            and_(
                Notification.user_id == user.id,
                Notification.read_at.is_(None),
                Notification.dismissed_at.is_(None),
            )
        )
        .values(read_at=datetime.now(timezone.utc))
    )

    await db.execute(stmt)
    await db.commit()

    return success(data={"message": "All notifications marked as read"})


@router.delete("/{notification_id}")
async def delete_notification(
    notification_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Dismiss a notification (soft delete)."""

    from datetime import datetime, timezone

    query = select(Notification).where(
        and_(
            Notification.id == notification_id,
            Notification.user_id == user.id,
        )
    )
    result = await db.execute(query)
    notification = result.scalar_one_or_none()

    if not notification:
        from app.core.exceptions import BusinessError
        from app.i18n.codes import ErrorCode

        raise BusinessError(ErrorCode.NOTIFICATION_NOT_FOUND)

    # Soft delete: set dismissed_at
    notification.dismissed_at = datetime.now(timezone.utc)
    await db.commit()

    return success(data={"message": "Notification dismissed"})


@router.delete("/clear")
async def clear_all_notifications(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Dismiss all notifications for the current user (soft delete)."""

    from datetime import datetime, timezone

    from sqlalchemy import update

    stmt = (
        update(Notification)
        .where(
            and_(
                Notification.user_id == user.id,
                Notification.dismissed_at.is_(None),
            )
        )
        .values(dismissed_at=datetime.now(timezone.utc))
    )

    await db.execute(stmt)
    await db.commit()

    return success(data={"message": "All notifications cleared"})
