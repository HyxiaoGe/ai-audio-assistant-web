"""YouTube OAuth and subscription API endpoints."""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.config import settings
from app.core.exceptions import BusinessError
from app.core.response import success
from app.i18n.codes import ErrorCode
from app.models.task import Task
from app.models.user import User
from app.models.youtube_subscription import YouTubeSubscription
from app.schemas.youtube import (
    BatchAutoTranscribeRequest,
    BatchStarRequest,
    BatchUpdateResponse,
    StarredVideosResponse,
    SubscriptionSettingsResponse,
    SubscriptionSettingsUpdate,
    YouTubeAuthUrlResponse,
    YouTubeChannelSyncStatus,
    YouTubeConnectionStatus,
    YouTubeDisconnectResponse,
    YouTubeSubscriptionItem,
    YouTubeSubscriptionListResponse,
    YouTubeSyncOverview,
    YouTubeSyncResponse,
    YouTubeTaskStatusResponse,
    YouTubeTranscribeRequest,
    YouTubeVideoItem,
    YouTubeVideoListResponse,
)
from app.services.youtube import (
    YouTubeDataService,
    YouTubeOAuthService,
    YouTubeSubscriptionService,
    YouTubeVideoService,
)

logger = logging.getLogger("app.api.youtube")

# Auto-sync threshold: trigger sync if last sync was more than this many hours ago
VIDEO_SYNC_THRESHOLD_HOURS = 6

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


async def _trigger_video_sync_if_needed(
    db: AsyncSession,
    user_id: str,
    force: bool = False,
    threshold_hours: int = VIDEO_SYNC_THRESHOLD_HOURS,
) -> bool:
    """Trigger video sync for subscriptions that need it.

    Args:
        db: Database session
        user_id: User ID
        force: If True, sync all subscriptions regardless of last sync time
        threshold_hours: Sync if last sync was more than this many hours ago

    Returns:
        True if sync was triggered, False otherwise
    """
    from worker.tasks.sync_youtube_videos import sync_channel_videos

    # Get subscriptions that need sync
    if force:
        # Sync all subscriptions
        result = await db.execute(
            select(YouTubeSubscription.channel_id).where(
                YouTubeSubscription.user_id == user_id,
            )
        )
    else:
        # Only sync subscriptions that haven't been synced or are stale
        threshold_time = datetime.now(timezone.utc) - timedelta(hours=threshold_hours)

        result = await db.execute(
            select(YouTubeSubscription.channel_id).where(
                YouTubeSubscription.user_id == user_id,
                (YouTubeSubscription.videos_synced_at.is_(None))
                | (YouTubeSubscription.videos_synced_at < threshold_time),
            )
        )

    channel_ids = result.scalars().all()

    if not channel_ids:
        return False

    # Trigger sync for each channel
    for channel_id in channel_ids:
        sync_channel_videos.delay(
            user_id=user_id,
            channel_id=channel_id,
            max_videos=50,
            incremental=True,
        )

    logger.info(f"Triggered video sync for {len(channel_ids)} channels for user {user_id}")
    return True


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
    show_hidden: bool = Query(False, description="Include hidden channels"),
    starred_only: bool = Query(False, description="Only show starred channels"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Get user's YouTube subscriptions (cached).

    Returns cached subscriptions from the database.
    Use POST /subscriptions/sync to refresh from YouTube.
    """
    subscription_service = YouTubeSubscriptionService()
    video_service = YouTubeVideoService()

    # Check if connected
    if not await subscription_service.is_connected(db, user.id):
        raise BusinessError(ErrorCode.YOUTUBE_NOT_CONNECTED)

    subscriptions, total = await subscription_service.get_cached_subscriptions(
        db=db,
        user_id=user.id,
        page=page,
        page_size=page_size,
        show_hidden=show_hidden,
        starred_only=starred_only,
    )

    # Get video counts for each channel
    channel_ids = [sub.channel_id for sub in subscriptions]
    video_counts = await video_service.get_video_counts_by_channels(db, user.id, channel_ids)

    items = [
        YouTubeSubscriptionItem(
            channel_id=sub.channel_id,
            channel_title=sub.channel_title,
            channel_thumbnail=sub.channel_thumbnail,
            channel_description=sub.channel_description,
            subscribed_at=sub.subscribed_at,
            is_hidden=sub.is_hidden,
            sync_enabled=sub.sync_enabled,
            is_starred=sub.is_starred,
            auto_transcribe=sub.auto_transcribe,
            video_count=video_counts.get(sub.channel_id, 0),
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


# ============================================================
# Subscription settings endpoints
# ============================================================


@router.get("/subscriptions/{channel_id}/settings")
async def get_subscription_settings(
    channel_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Get settings for a specific subscription."""
    subscription_service = YouTubeSubscriptionService()

    # Check if connected
    if not await subscription_service.is_connected(db, user.id):
        raise BusinessError(ErrorCode.YOUTUBE_NOT_CONNECTED)

    # Get subscription
    subscription = await subscription_service.get_subscription_by_channel(db, user.id, channel_id)
    if not subscription:
        raise BusinessError(
            ErrorCode.YOUTUBE_SUBSCRIPTION_NOT_FOUND,
            reason=f"Not subscribed to channel {channel_id}",
        )

    response = SubscriptionSettingsResponse(
        channel_id=subscription.channel_id,
        channel_title=subscription.channel_title,
        is_hidden=subscription.is_hidden,
        sync_enabled=subscription.sync_enabled,
        is_starred=subscription.is_starred,
        auto_transcribe=subscription.auto_transcribe,
        auto_transcribe_max_duration=subscription.auto_transcribe_max_duration,
        auto_transcribe_language=subscription.auto_transcribe_language,
        avg_publish_interval_hours=subscription.avg_publish_interval_hours,
        next_sync_at=subscription.next_sync_at,
    )

    return success(data=jsonable_encoder(response))


@router.patch("/subscriptions/{channel_id}/settings")
async def update_subscription_settings(
    channel_id: str,
    settings_update: SubscriptionSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Update settings for a specific subscription."""
    subscription_service = YouTubeSubscriptionService()

    # Check if connected
    if not await subscription_service.is_connected(db, user.id):
        raise BusinessError(ErrorCode.YOUTUBE_NOT_CONNECTED)

    # Get subscription
    subscription = await subscription_service.get_subscription_by_channel(db, user.id, channel_id)
    if not subscription:
        raise BusinessError(
            ErrorCode.YOUTUBE_SUBSCRIPTION_NOT_FOUND,
            reason=f"Not subscribed to channel {channel_id}",
        )

    # Update settings
    update_data = settings_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(subscription, key, value)

    await db.commit()
    await db.refresh(subscription)

    logger.info(f"Updated settings for channel {channel_id}: {update_data}")

    response = SubscriptionSettingsResponse(
        channel_id=subscription.channel_id,
        channel_title=subscription.channel_title,
        is_hidden=subscription.is_hidden,
        sync_enabled=subscription.sync_enabled,
        is_starred=subscription.is_starred,
        auto_transcribe=subscription.auto_transcribe,
        auto_transcribe_max_duration=subscription.auto_transcribe_max_duration,
        auto_transcribe_language=subscription.auto_transcribe_language,
        avg_publish_interval_hours=subscription.avg_publish_interval_hours,
        next_sync_at=subscription.next_sync_at,
    )

    return success(data=jsonable_encoder(response))


@router.post("/subscriptions/batch/star")
async def batch_star_subscriptions(
    request: BatchStarRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Batch update starred status for multiple channels."""
    subscription_service = YouTubeSubscriptionService()

    # Check if connected
    if not await subscription_service.is_connected(db, user.id):
        raise BusinessError(ErrorCode.YOUTUBE_NOT_CONNECTED)

    updated_count = await subscription_service.batch_update_starred(
        db=db,
        user_id=user.id,
        channel_ids=request.channel_ids,
        is_starred=request.is_starred,
    )

    logger.info(f"Batch starred update: {updated_count} channels, starred={request.is_starred}")

    return success(
        data=jsonable_encoder(
            BatchUpdateResponse(
                updated_count=updated_count,
                message=f"Updated {updated_count} channels",
            )
        )
    )


@router.post("/subscriptions/batch/auto-transcribe")
async def batch_auto_transcribe_settings(
    request: BatchAutoTranscribeRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Batch update auto-transcribe settings for multiple channels."""
    subscription_service = YouTubeSubscriptionService()

    # Check if connected
    if not await subscription_service.is_connected(db, user.id):
        raise BusinessError(ErrorCode.YOUTUBE_NOT_CONNECTED)

    updated_count = await subscription_service.batch_update_auto_transcribe(
        db=db,
        user_id=user.id,
        channel_ids=request.channel_ids,
        auto_transcribe=request.auto_transcribe,
        max_duration=request.max_duration,
        language=request.language,
    )

    logger.info(
        f"Batch auto-transcribe update: {updated_count} channels, "
        f"enabled={request.auto_transcribe}"
    )

    return success(
        data=jsonable_encoder(
            BatchUpdateResponse(
                updated_count=updated_count,
                message=f"Updated {updated_count} channels",
            )
        )
    )


# ============================================================
# Video endpoints
# ============================================================


def _generate_content_hash(content: str) -> str:
    """Generate SHA256 hash for content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def _get_transcribed_status(
    db: AsyncSession,
    user_id: str,
    video_ids: list[str],
) -> dict[str, tuple[bool, Optional[str]]]:
    """Get transcription status for a list of video IDs.

    Returns:
        Dict mapping video_id to (transcribed, task_id)
    """
    if not video_ids:
        return {}

    # Compute content hashes for all video IDs
    hash_to_video_id = {_generate_content_hash(f"youtube:{vid}"): vid for vid in video_ids}

    # Query tasks with these content hashes
    result = await db.execute(
        select(Task.content_hash, Task.id, Task.status).where(
            Task.user_id == user_id,
            Task.content_hash.in_(hash_to_video_id.keys()),
            Task.deleted_at.is_(None),
        )
    )

    status_map: dict[str, tuple[bool, Optional[str]]] = {vid: (False, None) for vid in video_ids}

    for content_hash, task_id, task_status in result.all():
        video_id = hash_to_video_id.get(content_hash)
        if video_id:
            is_transcribed = task_status == "completed"
            status_map[video_id] = (is_transcribed, str(task_id))

    return status_map


@router.get("/channels/{channel_id}/videos")
async def get_channel_videos(
    channel_id: str,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=50, description="Items per page"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Get cached videos for a channel.

    Returns cached videos from the database.
    Automatically triggers background sync if videos haven't been synced
    or last sync was too long ago.
    """
    subscription_service = YouTubeSubscriptionService()
    video_service = YouTubeVideoService()

    # Check if connected
    if not await subscription_service.is_connected(db, user.id):
        raise BusinessError(ErrorCode.YOUTUBE_NOT_CONNECTED)

    # Check if this channel needs sync
    sync_status = await video_service.get_channel_sync_status(db, user.id, channel_id)
    syncing = False

    if sync_status.get("subscribed"):
        last_synced = sync_status.get("last_synced_at")
        needs_sync = False

        if last_synced is None:
            # Never synced
            needs_sync = True
        else:
            # Check if stale
            threshold = datetime.now(timezone.utc) - timedelta(hours=VIDEO_SYNC_THRESHOLD_HOURS)
            if last_synced < threshold:
                needs_sync = True

        if needs_sync:
            from worker.tasks.sync_youtube_videos import sync_channel_videos

            sync_channel_videos.delay(
                user_id=user.id,
                channel_id=channel_id,
                max_videos=50,
                incremental=True,
            )
            syncing = True
            logger.info(f"Triggered video sync for channel {channel_id}")

    # Get cached videos
    videos, total = await video_service.get_cached_videos(
        db=db,
        user_id=user.id,
        channel_id=channel_id,
        page=page,
        page_size=page_size,
    )

    # Get transcription status for all videos
    video_ids = [v.video_id for v in videos]
    transcribed_status = await _get_transcribed_status(db, user.id, video_ids)

    items = [
        YouTubeVideoItem(
            video_id=v.video_id,
            channel_id=v.channel_id,
            title=v.title,
            description=v.description,
            thumbnail_url=v.thumbnail_url,
            published_at=v.published_at,
            duration_seconds=v.duration_seconds,
            view_count=v.view_count,
            like_count=v.like_count,
            comment_count=v.comment_count,
            transcribed=transcribed_status.get(v.video_id, (False, None))[0],
            task_id=transcribed_status.get(v.video_id, (False, None))[1],
        )
        for v in videos
    ]

    response = YouTubeVideoListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        last_synced_at=sync_status.get("last_synced_at"),
        syncing=syncing,
    )

    return success(data=jsonable_encoder(response))


@router.get("/videos/latest")
async def get_latest_videos(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=50, description="Items per page"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Get latest videos across all subscriptions.

    Returns the most recent videos from all subscribed channels,
    ordered by publish date descending.

    Automatically triggers background sync if:
    - No videos are cached yet (first access)
    - Last sync was more than VIDEO_SYNC_THRESHOLD_HOURS ago
    """
    subscription_service = YouTubeSubscriptionService()
    video_service = YouTubeVideoService()

    # Check if connected
    if not await subscription_service.is_connected(db, user.id):
        raise BusinessError(ErrorCode.YOUTUBE_NOT_CONNECTED)

    # Check if sync is needed and trigger if so
    syncing = await _trigger_video_sync_if_needed(db, user.id)

    # Get latest videos (excluding hidden channels)
    videos, total = await video_service.get_latest_videos(
        db=db,
        user_id=user.id,
        page=page,
        page_size=page_size,
        exclude_hidden=True,
    )

    # Get transcription status for all videos
    video_ids = [v.video_id for v in videos]
    transcribed_status = await _get_transcribed_status(db, user.id, video_ids)

    items = [
        YouTubeVideoItem(
            video_id=v.video_id,
            channel_id=v.channel_id,
            title=v.title,
            description=v.description,
            thumbnail_url=v.thumbnail_url,
            published_at=v.published_at,
            duration_seconds=v.duration_seconds,
            view_count=v.view_count,
            like_count=v.like_count,
            comment_count=v.comment_count,
            transcribed=transcribed_status.get(v.video_id, (False, None))[0],
            task_id=transcribed_status.get(v.video_id, (False, None))[1],
        )
        for v in videos
    ]

    response = YouTubeVideoListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        syncing=syncing,
    )

    return success(data=jsonable_encoder(response))


@router.get("/videos/starred")
async def get_starred_videos(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=50, description="Items per page"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Get latest videos from starred channels only.

    Returns videos only from channels marked as is_starred=True,
    ordered by publish date descending.
    """
    subscription_service = YouTubeSubscriptionService()
    video_service = YouTubeVideoService()

    # Check if connected
    if not await subscription_service.is_connected(db, user.id):
        raise BusinessError(ErrorCode.YOUTUBE_NOT_CONNECTED)

    # Get starred channels count
    starred_count = await subscription_service.get_starred_count(db, user.id)

    # Get videos from starred channels
    videos, total = await video_service.get_starred_videos(
        db=db,
        user_id=user.id,
        page=page,
        page_size=page_size,
    )

    # Get transcription status for all videos
    video_ids = [v.video_id for v in videos]
    transcribed_status = await _get_transcribed_status(db, user.id, video_ids)

    items = [
        YouTubeVideoItem(
            video_id=v.video_id,
            channel_id=v.channel_id,
            title=v.title,
            description=v.description,
            thumbnail_url=v.thumbnail_url,
            published_at=v.published_at,
            duration_seconds=v.duration_seconds,
            view_count=v.view_count,
            like_count=v.like_count,
            comment_count=v.comment_count,
            transcribed=transcribed_status.get(v.video_id, (False, None))[0],
            task_id=transcribed_status.get(v.video_id, (False, None))[1],
        )
        for v in videos
    ]

    response = StarredVideosResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        starred_channels_count=starred_count,
    )

    return success(data=jsonable_encoder(response))


@router.get("/sync-overview")
async def get_sync_overview(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Get overall video sync status across all subscriptions.

    Returns:
        - total_subscriptions: Total subscribed channels
        - synced_subscriptions: Channels that have been synced at least once
        - pending_subscriptions: Channels waiting to be synced
        - total_videos: Total videos in cache
        - channels_with_videos: Number of channels with cached videos
        - fully_synced: True if all subscriptions have been synced
        - last_sync_at: Most recent sync timestamp
    """
    subscription_service = YouTubeSubscriptionService()
    video_service = YouTubeVideoService()

    # Check if connected
    if not await subscription_service.is_connected(db, user.id):
        raise BusinessError(ErrorCode.YOUTUBE_NOT_CONNECTED)

    overview = await video_service.get_sync_overview(db, user.id)

    return success(data=jsonable_encoder(YouTubeSyncOverview(**overview)))


@router.get("/channels/{channel_id}/sync-status")
async def get_channel_sync_status(
    channel_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Get video sync status for a channel."""
    subscription_service = YouTubeSubscriptionService()
    video_service = YouTubeVideoService()

    # Check if connected
    if not await subscription_service.is_connected(db, user.id):
        raise BusinessError(ErrorCode.YOUTUBE_NOT_CONNECTED)

    status = await video_service.get_channel_sync_status(db, user.id, channel_id)

    return success(data=jsonable_encoder(YouTubeChannelSyncStatus(**status)))


@router.post("/channels/{channel_id}/videos/sync")
async def sync_channel_videos(
    channel_id: str,
    max_videos: int = Query(50, ge=1, le=200, description="Max videos to fetch"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Trigger video sync for a specific channel.

    This starts a background task to fetch the latest videos
    from the specified channel.
    """
    subscription_service = YouTubeSubscriptionService()
    video_service = YouTubeVideoService()

    # Check if connected
    if not await subscription_service.is_connected(db, user.id):
        raise BusinessError(ErrorCode.YOUTUBE_NOT_CONNECTED)

    # Check if subscription exists
    subscription = await video_service.get_subscription(db, user.id, channel_id)
    if not subscription:
        raise BusinessError(
            ErrorCode.YOUTUBE_SUBSCRIPTION_NOT_FOUND,
            reason=f"Not subscribed to channel {channel_id}",
        )

    # Trigger background sync
    from worker.tasks.sync_youtube_videos import sync_channel_videos as sync_task

    task = sync_task.delay(user_id=user.id, channel_id=channel_id, max_videos=max_videos)

    logger.info(f"Started video sync for user {user.id}, channel {channel_id}, task_id={task.id}")

    return success(
        data=jsonable_encoder(
            YouTubeSyncResponse(
                task_id=task.id,
                message=f"Video sync started for channel {subscription.channel_title}",
            )
        )
    )


@router.post("/videos/{video_id}/transcribe")
async def transcribe_video(
    video_id: str,
    request: YouTubeTranscribeRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Create a transcription task from a cached YouTube video.

    This creates a new task to transcribe the specified YouTube video.
    The video must be in the user's cache (from a subscribed channel).
    """
    subscription_service = YouTubeSubscriptionService()
    video_service = YouTubeVideoService()

    # Check if connected
    if not await subscription_service.is_connected(db, user.id):
        raise BusinessError(ErrorCode.YOUTUBE_NOT_CONNECTED)

    # Get the cached video
    video = await video_service.get_video_by_id(db, user.id, video_id)
    if not video:
        raise BusinessError(
            ErrorCode.YOUTUBE_VIDEO_NOT_FOUND,
            reason=f"Video {video_id} not found in cache",
        )

    # Check if already transcribed
    content_hash = _generate_content_hash(f"youtube:{video_id}")
    existing_result = await db.execute(
        select(Task).where(
            Task.user_id == user.id,
            Task.content_hash == content_hash,
            Task.deleted_at.is_(None),
        )
    )
    existing_task = existing_result.scalar_one_or_none()

    if existing_task:
        if existing_task.status == "completed":
            raise BusinessError(
                ErrorCode.TASK_ALREADY_EXISTS,
                reason="Video already transcribed",
            )
        if existing_task.status not in ("failed", "cancelled"):
            raise BusinessError(
                ErrorCode.TASK_PROCESSING,
                reason="Video is already being processed",
            )

    # Create task using TaskService
    from app.schemas.task import TaskCreateRequest, TaskOptions
    from app.services.task_service import TaskService

    # Build options with requested language if provided
    options = TaskOptions()
    if request.language:
        options.language = request.language

    task_data = TaskCreateRequest(
        source_type="youtube",
        source_url=f"https://www.youtube.com/watch?v={video_id}",
        title=video.title,
        options=options,
    )

    task = await TaskService.create_task(db, user, task_data, trace_id=None)

    logger.info(f"Created transcription task {task.id} for video {video_id}")

    return success(
        data={
            "task_id": str(task.id),
            "video_id": video_id,
            "title": video.title,
            "message": "Transcription task created",
        }
    )


@router.get("/tasks/{task_id}/status")
async def get_task_status(
    task_id: str,
    _: User = Depends(get_current_user),
) -> JSONResponse:
    """Get the status of a YouTube sync task.

    This endpoint queries the Celery task status for subscription
    or video sync tasks.
    """
    from worker.celery_app import celery_app

    result = celery_app.AsyncResult(task_id)

    status = result.status  # PENDING, STARTED, SUCCESS, FAILURE, REVOKED
    task_result = None
    error = None

    if result.ready():
        if result.successful():
            task_result = result.result
        else:
            # Task failed
            error = str(result.result) if result.result else "Unknown error"

    response = YouTubeTaskStatusResponse(
        task_id=task_id,
        status=status.lower(),
        result=task_result if isinstance(task_result, dict) else None,
        error=error,
    )

    return success(data=jsonable_encoder(response))
