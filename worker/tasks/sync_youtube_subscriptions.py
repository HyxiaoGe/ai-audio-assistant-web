"""YouTube subscription sync Celery task.

Syncs user's YouTube subscriptions from YouTube Data API to local database.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from celery import shared_task
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.config import settings
from app.models.account import Account
from app.models.youtube_subscription import YouTubeSubscription
from worker.db import get_sync_db_session

logger = logging.getLogger(__name__)

# Provider name for YouTube OAuth account
YOUTUBE_PROVIDER = "youtube"


@shared_task(
    name="worker.tasks.sync_youtube_subscriptions.sync_youtube_subscriptions",
    bind=True,
    max_retries=3,
    soft_time_limit=300,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
)
def sync_youtube_subscriptions(
    self,
    user_id: str,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Sync YouTube subscriptions for a user.

    This task:
    1. Gets the user's YouTube OAuth account
    2. Refreshes the token if needed
    3. Fetches all subscriptions from YouTube API
    4. Updates the local database cache

    Args:
        user_id: The user ID to sync subscriptions for
        request_id: Optional request ID for tracing

    Returns:
        Dict with sync results
    """
    logger.info(f"Starting YouTube subscription sync for user {user_id}")

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

            # Check if token needs refresh
            credentials = _build_credentials(account)

            if _is_token_expired(account.token_expires_at):
                logger.info(f"Refreshing token for user {user_id}")
                try:
                    from google.auth.transport.requests import Request

                    credentials.refresh(Request())

                    # Update account with new token
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

            # Fetch all subscriptions from YouTube
            try:
                subscriptions = _fetch_all_subscriptions(credentials)
            except HttpError as e:
                logger.exception(f"YouTube API error: {e}")
                return {
                    "status": "error",
                    "error": f"YouTube API error: {e}",
                    "synced_count": 0,
                }

            if not subscriptions:
                logger.info(f"No subscriptions found for user {user_id}")
                return {
                    "status": "success",
                    "synced_count": 0,
                    "message": "No subscriptions found",
                }

            # Sync to database
            now = datetime.now(timezone.utc)
            synced_count = _sync_subscriptions_to_db(session, user_id, subscriptions, now)

            logger.info(f"Synced {synced_count} subscriptions for user {user_id}")

            return {
                "status": "success",
                "synced_count": synced_count,
                "message": f"Successfully synced {synced_count} subscriptions",
            }

    except Exception as e:
        logger.exception(f"Unexpected error in sync task: {e}")
        raise  # Let Celery handle retry


def _build_credentials(account: Account) -> Credentials:
    """Build Google Credentials from account."""
    return Credentials(
        token=account.access_token,
        refresh_token=account.refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        expiry=account.token_expires_at,
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


def _fetch_all_subscriptions(credentials: Credentials) -> List[Dict[str, Any]]:
    """Fetch all subscriptions from YouTube API."""
    youtube = build("youtube", "v3", credentials=credentials)

    all_subscriptions = []
    page_token = None

    while True:
        request = youtube.subscriptions().list(
            part="snippet,contentDetails",
            mine=True,
            maxResults=50,
            pageToken=page_token,
            order="alphabetical",
        )
        response = request.execute()

        for item in response.get("items", []):
            snippet = item.get("snippet", {})
            resource_id = snippet.get("resourceId", {})

            subscribed_at = None
            published_at = snippet.get("publishedAt")
            if published_at:
                try:
                    subscribed_at = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    pass

            all_subscriptions.append(
                {
                    "channel_id": resource_id.get("channelId"),
                    "channel_title": snippet.get("title"),
                    "channel_description": snippet.get("description"),
                    "channel_thumbnail": snippet.get("thumbnails", {})
                    .get("default", {})
                    .get("url"),
                    "subscribed_at": subscribed_at,
                }
            )

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return all_subscriptions


def _sync_subscriptions_to_db(
    session,
    user_id: str,
    subscriptions: List[Dict[str, Any]],
    sync_time: datetime,
) -> int:
    """Sync subscriptions to database using upsert."""
    synced_channel_ids = []

    for sub in subscriptions:
        channel_id = sub["channel_id"]
        if not channel_id:
            continue

        synced_channel_ids.append(channel_id)

        # Upsert subscription
        stmt = insert(YouTubeSubscription).values(
            user_id=user_id,
            channel_id=channel_id,
            channel_title=sub["channel_title"] or "Unknown",
            channel_thumbnail=sub["channel_thumbnail"],
            channel_description=sub["channel_description"],
            subscribed_at=sub["subscribed_at"],
            last_synced_at=sync_time,
        )

        stmt = stmt.on_conflict_do_update(
            constraint="uk_youtube_subscriptions_user_channel",
            set_={
                "channel_title": stmt.excluded.channel_title,
                "channel_thumbnail": stmt.excluded.channel_thumbnail,
                "channel_description": stmt.excluded.channel_description,
                "subscribed_at": stmt.excluded.subscribed_at,
                "last_synced_at": stmt.excluded.last_synced_at,
                "updated_at": sync_time,
            },
        )

        session.execute(stmt)

    # Remove subscriptions that no longer exist
    if synced_channel_ids:
        session.execute(
            delete(YouTubeSubscription).where(
                YouTubeSubscription.user_id == user_id,
                YouTubeSubscription.channel_id.not_in(synced_channel_ids),
            )
        )

    session.commit()

    return len(synced_channel_ids)
