"""YouTube video sync Celery tasks.

Syncs videos from subscribed channels to local database.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from celery import shared_task
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.config import settings
from app.models.account import Account
from app.models.youtube_subscription import YouTubeSubscription
from app.models.youtube_video import YouTubeVideo
from worker.db import get_sync_db_session
from worker.redis_client import publish_user_notification_sync

logger = logging.getLogger(__name__)

# Provider name for YouTube OAuth account
YOUTUBE_PROVIDER = "youtube"


def _build_credentials(account: Account) -> Credentials:
    """Build Google Credentials from account."""
    # Google auth library expects naive datetime (no timezone)
    expiry = account.token_expires_at
    if expiry and expiry.tzinfo is not None:
        expiry = expiry.astimezone(timezone.utc).replace(tzinfo=None)

    return Credentials(  # nosec B106 - not a password
        token=account.access_token,
        refresh_token=account.refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        expiry=expiry,
    )


def _is_token_expired(expires_at: Optional[datetime], buffer_minutes: int = 5) -> bool:
    """Check if token is expired or will expire soon."""
    if not expires_at:
        return True

    # Ensure timezone aware
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    from datetime import timedelta

    buffer = timedelta(minutes=buffer_minutes)
    return datetime.now(timezone.utc) >= (expires_at - buffer)


def _parse_duration(duration_str: Optional[str]) -> Optional[int]:
    """Parse ISO 8601 duration string to seconds."""
    if not duration_str:
        return None

    try:
        import re

        match = re.match(
            r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?",
            duration_str,
        )
        if not match:
            return None

        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)

        return hours * 3600 + minutes * 60 + seconds
    except (ValueError, AttributeError):
        return None


def _parse_int(value: Optional[str]) -> Optional[int]:
    """Parse string to int, returning None on failure."""
    if value is None:
        return None

    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _parse_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO datetime string from YouTube API."""
    if not dt_str:
        return None

    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _get_best_thumbnail(thumbnails: Dict[str, Any]) -> Optional[str]:
    """Get the best available thumbnail URL."""
    for quality in ["medium", "high", "default", "maxres", "standard"]:
        if quality in thumbnails:
            return thumbnails[quality].get("url")
    return None


@shared_task(
    name="worker.tasks.sync_youtube_videos.sync_channel_videos",
    bind=True,
    max_retries=3,
    soft_time_limit=300,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
)
def sync_channel_videos(
    self,
    user_id: str,
    channel_id: str,
    max_videos: int = 50,
    incremental: bool = True,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Sync videos for a specific channel.

    Args:
        user_id: The user ID to sync videos for
        channel_id: The YouTube channel ID to sync
        max_videos: Maximum number of videos to fetch
        incremental: If True, stop when we hit already-cached videos
        request_id: Optional request ID for tracing

    Returns:
        Dict with sync results
    """
    logger.info(f"Starting video sync for user {user_id}, channel {channel_id}")

    try:
        with get_sync_db_session() as session:
            # Get YouTube account
            account = session.execute(
                select(Account).where(
                    Account.user_id == user_id,
                    Account.provider == YOUTUBE_PROVIDER,
                )
            ).scalar_one_or_none()

            if not account:
                logger.warning(f"No YouTube account found for user {user_id}")
                return {
                    "status": "error",
                    "error": "YouTube account not connected",
                    "synced_count": 0,
                }

            # Get subscription
            subscription = session.execute(
                select(YouTubeSubscription).where(
                    YouTubeSubscription.user_id == user_id,
                    YouTubeSubscription.channel_id == channel_id,
                )
            ).scalar_one_or_none()

            if not subscription:
                logger.warning(f"Subscription not found for channel {channel_id}")
                return {
                    "status": "error",
                    "error": f"Not subscribed to channel {channel_id}",
                    "synced_count": 0,
                }

            # Check if sync is disabled for this channel
            if not subscription.sync_enabled:
                logger.info(f"Sync disabled for channel {channel_id}, skipping")
                return {
                    "status": "skipped",
                    "reason": "sync_disabled",
                    "synced_count": 0,
                }

            # Check if token needs refresh
            credentials = _build_credentials(account)

            if _is_token_expired(account.token_expires_at):
                logger.info(f"Refreshing token for user {user_id}")
                try:
                    from google.auth.transport.requests import Request

                    credentials.refresh(Request())

                    account.access_token = credentials.token
                    if credentials.expiry:
                        expires_at = credentials.expiry
                        if expires_at.tzinfo is None:
                            expires_at = expires_at.replace(tzinfo=timezone.utc)
                        account.token_expires_at = expires_at

                    session.commit()
                    logger.info(f"Token refreshed for user {user_id}")

                except Exception as e:
                    logger.exception(f"Failed to refresh token: {e}")
                    return {
                        "status": "error",
                        "error": f"Token refresh failed: {e}",
                        "synced_count": 0,
                    }

            # Build YouTube API client
            youtube = build("youtube", "v3", credentials=credentials)

            # Get or fetch uploads playlist ID
            uploads_playlist_id = subscription.uploads_playlist_id
            if not uploads_playlist_id:
                try:
                    request = youtube.channels().list(
                        part="contentDetails",
                        id=channel_id,
                    )
                    response = request.execute()

                    items = response.get("items", [])
                    if not items:
                        logger.warning(f"Channel not found: {channel_id}")
                        return {
                            "status": "error",
                            "error": "Channel not found",
                            "synced_count": 0,
                        }

                    content_details = items[0].get("contentDetails", {})
                    related_playlists = content_details.get("relatedPlaylists", {})
                    uploads_playlist_id = related_playlists.get("uploads")

                    if uploads_playlist_id:
                        subscription.uploads_playlist_id = uploads_playlist_id
                        session.commit()

                except HttpError as e:
                    logger.exception(f"YouTube API error getting uploads playlist: {e}")
                    return {
                        "status": "error",
                        "error": f"YouTube API error: {e}",
                        "synced_count": 0,
                    }

            if not uploads_playlist_id:
                logger.warning(f"No uploads playlist found for channel {channel_id}")
                return {
                    "status": "error",
                    "error": "No uploads playlist found",
                    "synced_count": 0,
                }

            # Get existing video IDs for incremental sync
            existing_video_ids: set = set()
            if incremental:
                result = session.execute(
                    select(YouTubeVideo.video_id).where(
                        YouTubeVideo.user_id == user_id,
                        YouTubeVideo.channel_id == channel_id,
                    )
                )
                existing_video_ids = set(result.scalars().all())

            # Fetch videos from playlist
            now = datetime.now(timezone.utc)
            all_videos: List[Dict[str, Any]] = []
            page_token = None
            stop_fetching = False

            while len(all_videos) < max_videos and not stop_fetching:
                try:
                    request = youtube.playlistItems().list(
                        part="snippet,contentDetails",
                        playlistId=uploads_playlist_id,
                        maxResults=min(50, max_videos - len(all_videos)),
                        pageToken=page_token,
                    )
                    response = request.execute()

                    for item in response.get("items", []):
                        snippet = item.get("snippet", {})
                        content_details = item.get("contentDetails", {})

                        # Skip deleted/private videos
                        if snippet.get("title") in ("Deleted video", "Private video"):
                            continue

                        video_id = content_details.get("videoId")
                        if not video_id:
                            continue

                        # Incremental sync: stop if we hit an existing video
                        if incremental and video_id in existing_video_ids:
                            stop_fetching = True
                            break

                        all_videos.append(
                            {
                                "video_id": video_id,
                                "channel_id": snippet.get("channelId", channel_id),
                                "title": snippet.get("title"),
                                "description": snippet.get("description"),
                                "thumbnail_url": _get_best_thumbnail(snippet.get("thumbnails", {})),
                                "published_at": _parse_datetime(
                                    content_details.get("videoPublishedAt")
                                    or snippet.get("publishedAt")
                                ),
                            }
                        )

                    page_token = response.get("nextPageToken")
                    if not page_token:
                        break

                except HttpError as e:
                    logger.exception(f"YouTube API error fetching playlist: {e}")
                    break

            if not all_videos:
                logger.info(f"No new videos to sync for channel {channel_id}")
                subscription.videos_synced_at = now
                session.commit()
                return {
                    "status": "success",
                    "synced_count": 0,
                    "message": "No new videos found",
                }

            # Batch fetch video details (duration, stats)
            video_ids = [v["video_id"] for v in all_videos]
            video_details: Dict[str, Dict[str, Any]] = {}

            # Process in batches of 50
            for i in range(0, len(video_ids), 50):
                batch_ids = video_ids[i : i + 50]
                try:
                    request = youtube.videos().list(
                        part="contentDetails,statistics",
                        id=",".join(batch_ids),
                    )
                    response = request.execute()

                    for item in response.get("items", []):
                        vid = item.get("id")
                        content_details = item.get("contentDetails", {})
                        statistics = item.get("statistics", {})

                        video_details[vid] = {
                            "duration_seconds": _parse_duration(content_details.get("duration")),
                            "view_count": _parse_int(statistics.get("viewCount")),
                            "like_count": _parse_int(statistics.get("likeCount")),
                            "comment_count": _parse_int(statistics.get("commentCount")),
                        }

                except HttpError as e:
                    logger.exception(f"YouTube API error fetching video details: {e}")

            # Upsert videos to database
            synced_count = 0
            for video in all_videos:
                video_id = video.get("video_id")
                if not video_id:
                    continue

                details = video_details.get(video_id, {})

                stmt = insert(YouTubeVideo).values(
                    subscription_id=str(subscription.id),
                    user_id=user_id,
                    video_id=video_id,
                    channel_id=video.get("channel_id", channel_id),
                    title=video.get("title") or "Untitled",
                    description=video.get("description"),
                    thumbnail_url=video.get("thumbnail_url"),
                    published_at=video.get("published_at") or now,
                    duration_seconds=details.get("duration_seconds"),
                    view_count=details.get("view_count"),
                    like_count=details.get("like_count"),
                    comment_count=details.get("comment_count"),
                    last_synced_at=now,
                )

                stmt = stmt.on_conflict_do_update(
                    constraint="uk_youtube_videos_user_video",
                    set_={
                        "title": stmt.excluded.title,
                        "description": stmt.excluded.description,
                        "thumbnail_url": stmt.excluded.thumbnail_url,
                        "duration_seconds": stmt.excluded.duration_seconds,
                        "view_count": stmt.excluded.view_count,
                        "like_count": stmt.excluded.like_count,
                        "comment_count": stmt.excluded.comment_count,
                        "last_synced_at": stmt.excluded.last_synced_at,
                        "updated_at": now,
                    },
                )

                session.execute(stmt)
                synced_count += 1

            # Update subscription sync time and publish stats
            subscription.videos_synced_at = now

            # Update publish frequency statistics for intelligent scheduling
            from app.services.youtube.sync_scheduler import update_publish_stats

            update_publish_stats(subscription, session)

            session.commit()

            logger.info(f"Synced {synced_count} videos for channel {channel_id}")

            # Send WebSocket notification
            import json

            notification = json.dumps(
                {
                    "code": 0,
                    "message": "success",
                    "data": {
                        "type": "youtube_videos_synced",
                        "channel_id": channel_id,
                        "channel_title": subscription.channel_title,
                        "synced_count": synced_count,
                    },
                    "traceId": request_id or "",
                },
                ensure_ascii=False,
            )
            publish_user_notification_sync(user_id, notification)

            # Trigger auto-transcription for new videos if enabled
            if synced_count > 0 and subscription.auto_transcribe:
                new_video_ids = [v["video_id"] for v in all_videos]
                if new_video_ids:
                    from worker.tasks.youtube_auto_transcribe import (
                        process_auto_transcriptions,
                    )

                    process_auto_transcriptions.delay(
                        user_id=user_id,
                        channel_id=channel_id,
                        video_ids=new_video_ids,
                        request_id=request_id,
                    )
                    logger.info(
                        f"Triggered auto-transcription for {len(new_video_ids)} "
                        f"videos from channel {channel_id}"
                    )

            return {
                "status": "success",
                "synced_count": synced_count,
                "message": f"Successfully synced {synced_count} videos",
            }

    except Exception as e:
        logger.exception(f"Unexpected error in sync task: {e}")
        raise  # Let Celery handle retry


@shared_task(
    name="worker.tasks.sync_youtube_videos.sync_all_subscriptions_videos",
    bind=True,
    soft_time_limit=3600,
)
def sync_all_subscriptions_videos(
    self,
    max_videos_per_channel: int = 20,
) -> Dict[str, Any]:
    """Sync videos for all users' subscriptions.

    This is a daily scheduled task that syncs the latest videos
    for all subscribed channels.

    Args:
        max_videos_per_channel: Max videos to sync per channel

    Returns:
        Dict with sync results
    """
    logger.info("Starting daily subscription video sync")

    channels_synced = 0
    errors = 0

    try:
        with get_sync_db_session() as session:
            # Get all subscriptions with sync enabled
            result = session.execute(
                select(
                    YouTubeSubscription.user_id,
                    YouTubeSubscription.channel_id,
                )
                .where(YouTubeSubscription.sync_enabled == True)  # noqa: E712
                .distinct()
            )
            subscriptions = result.all()

            logger.info(f"Found {len(subscriptions)} subscriptions to sync (sync_enabled)")

            for user_id, channel_id in subscriptions:
                try:
                    # Trigger individual sync task
                    sync_channel_videos.delay(
                        user_id=str(user_id),
                        channel_id=channel_id,
                        max_videos=max_videos_per_channel,
                        incremental=True,
                    )
                    channels_synced += 1
                except Exception as e:
                    logger.exception(f"Failed to queue sync for channel {channel_id}: {e}")
                    errors += 1

    except Exception as e:
        logger.exception(f"Unexpected error in daily sync: {e}")
        return {
            "status": "error",
            "error": str(e),
            "channels_queued": channels_synced,
            "errors": errors,
        }

    logger.info(f"Queued video sync for {channels_synced} channels, {errors} errors")

    return {
        "status": "success",
        "channels_queued": channels_synced,
        "errors": errors,
        "message": f"Queued sync for {channels_synced} channels",
    }


@shared_task(
    name="worker.tasks.sync_youtube_videos.check_scheduled_syncs",
    bind=True,
    soft_time_limit=300,
)
def check_scheduled_syncs(
    self,
    batch_size: int = 100,
) -> Dict[str, Any]:
    """Check which channels need syncing based on their next_sync_at time.

    Runs periodically (e.g., every hour) to trigger syncs for channels
    whose next_sync_at has passed.

    Args:
        batch_size: Max channels to process per run

    Returns:
        Dict with sync results
    """
    from sqlalchemy import or_

    logger.info("Checking for scheduled channel syncs")

    now = datetime.now(timezone.utc)
    syncs_triggered = 0
    errors = 0

    try:
        with get_sync_db_session() as session:
            # Find subscriptions due for sync
            # - sync_enabled must be True
            # - next_sync_at is NULL (never calculated) OR <= now
            result = session.execute(
                select(YouTubeSubscription.user_id, YouTubeSubscription.channel_id)
                .where(
                    YouTubeSubscription.sync_enabled == True,  # noqa: E712
                    or_(
                        YouTubeSubscription.next_sync_at.is_(None),
                        YouTubeSubscription.next_sync_at <= now,
                    ),
                )
                .limit(batch_size)
            )

            subscriptions = result.all()
            logger.info(f"Found {len(subscriptions)} channels due for sync")

            for user_id, channel_id in subscriptions:
                try:
                    sync_channel_videos.delay(
                        user_id=str(user_id),
                        channel_id=channel_id,
                        max_videos=20,
                        incremental=True,
                    )
                    syncs_triggered += 1
                except Exception as e:
                    logger.exception(f"Failed to trigger sync for channel {channel_id}: {e}")
                    errors += 1

    except Exception as e:
        logger.exception(f"Unexpected error in scheduled sync check: {e}")
        return {
            "status": "error",
            "error": str(e),
            "syncs_triggered": syncs_triggered,
            "errors": errors,
        }

    logger.info(f"Triggered {syncs_triggered} scheduled syncs, {errors} errors")

    return {
        "status": "success",
        "syncs_triggered": syncs_triggered,
        "errors": errors,
    }
