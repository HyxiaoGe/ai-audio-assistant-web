"""YouTube OAuth and subscription API endpoints."""

from __future__ import annotations

import logging
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.config import settings
from app.core.exceptions import BusinessError
from app.core.response import success
from app.i18n.codes import ErrorCode
from app.models.user import User
from app.schemas.youtube import (
    YouTubeAuthUrlResponse,
    YouTubeConnectionStatus,
    YouTubeDisconnectResponse,
    YouTubeSubscriptionItem,
    YouTubeSubscriptionListResponse,
    YouTubeSyncResponse,
)
from app.services.youtube import (
    YouTubeDataService,
    YouTubeOAuthService,
    YouTubeSubscriptionService,
)

logger = logging.getLogger("app.api.youtube")

router = APIRouter(prefix="/youtube", tags=["youtube"])

# In-memory state storage (in production, use Redis)
# Format: {state: user_id}
_oauth_states: dict[str, str] = {}


def _generate_state(user_id: str) -> str:
    """Generate a secure state token and store the mapping."""
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = user_id
    return state


def _verify_state(state: str) -> Optional[str]:
    """Verify state and return user_id, then remove it."""
    return _oauth_states.pop(state, None)


@router.get("/auth")
async def get_auth_url(
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Get YouTube OAuth authorization URL.

    Returns a URL that the frontend should redirect the user to.
    After authorization, Google will redirect back to /callback.
    """
    oauth_service = YouTubeOAuthService()

    if not oauth_service.is_configured():
        raise BusinessError(
            ErrorCode.YOUTUBE_OAUTH_FAILED,
            reason="YouTube OAuth not configured",
        )

    # Generate state for CSRF protection
    state = _generate_state(user.id)

    auth_url = oauth_service.generate_auth_url(state=state)

    logger.info(f"Generated auth URL for user {user.id}")

    return success(data=jsonable_encoder(YouTubeAuthUrlResponse(auth_url=auth_url)))


@router.get("/callback")
async def oauth_callback(
    code: str = Query(..., description="Authorization code from Google"),
    state: str = Query(..., description="State parameter for CSRF protection"),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Handle Google OAuth callback.

    This endpoint is called by Google after the user authorizes.
    It exchanges the code for tokens, saves them, and redirects to frontend.
    """
    # Verify state
    user_id = _verify_state(state)
    if not user_id:
        logger.warning(f"Invalid state in callback: {state[:20]}...")
        # Redirect to frontend with error
        return RedirectResponse(
            url=f"{settings.FRONTEND_URL}/settings?youtube=error&reason=invalid_state"
        )

    try:
        oauth_service = YouTubeOAuthService()
        subscription_service = YouTubeSubscriptionService()

        # Exchange code for tokens
        access_token, refresh_token, expires_at = oauth_service.exchange_code(code)

        # Build credentials and try to get channel info (optional)
        credentials = oauth_service.build_credentials(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )
        data_service = YouTubeDataService(credentials)

        # Channel is optional - user may have subscriptions without a channel
        channel_id = None
        try:
            channel_info = data_service.get_my_channel()
            channel_id = channel_info["id"]
        except BusinessError as e:
            if e.code == ErrorCode.YOUTUBE_API_ERROR:
                logger.info(f"User {user_id} has no YouTube channel, continuing anyway")
            else:
                raise

        # Save account
        await subscription_service.save_youtube_account(
            db=db,
            user_id=user_id,
            channel_id=channel_id,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )

        logger.info(f"YouTube connected for user {user_id}, channel {channel_id}")

        # Trigger background sync (import here to avoid circular import)
        from worker.tasks.sync_youtube_subscriptions import sync_youtube_subscriptions

        sync_youtube_subscriptions.delay(user_id=user_id)

        # Redirect to frontend with success
        return RedirectResponse(url=f"{settings.FRONTEND_URL}/settings?youtube=connected")

    except BusinessError as e:
        logger.exception(f"OAuth callback failed for user {user_id}: {e}")
        reason = e.kwargs.get("reason", str(e.code))
        return RedirectResponse(
            url=f"{settings.FRONTEND_URL}/settings?youtube=error&reason={reason}"
        )
    except Exception as e:
        logger.exception(f"Unexpected error in OAuth callback: {e}")
        return RedirectResponse(
            url=f"{settings.FRONTEND_URL}/settings?youtube=error&reason=unknown"
        )


@router.get("/status")
async def get_connection_status(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Get YouTube connection status."""
    subscription_service = YouTubeSubscriptionService()
    status = await subscription_service.get_connection_status(db, user.id)

    return success(data=jsonable_encoder(YouTubeConnectionStatus(**status)))


@router.delete("/disconnect")
async def disconnect_youtube(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Disconnect YouTube account."""
    subscription_service = YouTubeSubscriptionService()
    await subscription_service.disconnect(db, user.id)

    logger.info(f"YouTube disconnected for user {user.id}")

    return success(data=jsonable_encoder(YouTubeDisconnectResponse()))


@router.get("/subscriptions")
async def get_subscriptions(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Get user's YouTube subscriptions (cached).

    Returns cached subscriptions from the database.
    Use POST /subscriptions/sync to refresh from YouTube.
    """
    subscription_service = YouTubeSubscriptionService()

    # Check if connected
    if not await subscription_service.is_connected(db, user.id):
        raise BusinessError(ErrorCode.YOUTUBE_NOT_CONNECTED)

    subscriptions, total = await subscription_service.get_cached_subscriptions(
        db=db,
        user_id=user.id,
        page=page,
        page_size=page_size,
    )

    items = [
        YouTubeSubscriptionItem(
            channel_id=sub.channel_id,
            channel_title=sub.channel_title,
            channel_thumbnail=sub.channel_thumbnail,
            channel_description=sub.channel_description,
            subscribed_at=sub.subscribed_at,
        )
        for sub in subscriptions
    ]

    response = YouTubeSubscriptionListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )

    return success(data=jsonable_encoder(response))


@router.post("/subscriptions/sync")
async def sync_subscriptions(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Trigger background sync of YouTube subscriptions.

    This starts a Celery task to fetch all subscriptions from YouTube
    and update the local cache.
    """
    subscription_service = YouTubeSubscriptionService()

    # Check if connected
    if not await subscription_service.is_connected(db, user.id):
        raise BusinessError(ErrorCode.YOUTUBE_NOT_CONNECTED)

    # Trigger background sync
    from worker.tasks.sync_youtube_subscriptions import sync_youtube_subscriptions

    task = sync_youtube_subscriptions.delay(user_id=user.id)

    logger.info(f"Started subscription sync for user {user.id}, task_id={task.id}")

    return success(
        data=jsonable_encoder(
            YouTubeSyncResponse(task_id=task.id, message="Sync started in background")
        )
    )
