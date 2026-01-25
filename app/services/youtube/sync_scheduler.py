"""YouTube sync scheduling based on channel publishing patterns.

Provides intelligent sync scheduling by analyzing channel publishing frequency
and calculating optimal sync intervals.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from app.models.youtube_subscription import YouTubeSubscription

# Default sync interval in hours
DEFAULT_SYNC_HOURS = 6
# Minimum sync interval (even for very active channels)
MIN_SYNC_HOURS = 2
# Maximum sync interval (for very slow channels)
MAX_SYNC_HOURS = 48


def calculate_next_sync_time(
    avg_interval_hours: Optional[float],
    last_publish_at: Optional[datetime],
    last_sync_at: Optional[datetime],
    now: Optional[datetime] = None,
) -> datetime:
    """Calculate optimal next sync time based on channel publishing patterns.

    Strategy:
    1. If no publish history, use default interval (6 hours)
    2. If avg_interval < 12 hours: sync every 2-4 hours (active channel)
    3. If avg_interval 12-48 hours: sync at 0.5x interval
    4. If avg_interval > 48 hours: sync daily

    Also considers time since last publish to catch new videos sooner.

    Args:
        avg_interval_hours: Average publish interval in hours
        last_publish_at: Most recent video publish time
        last_sync_at: Last sync time
        now: Current time (defaults to UTC now)

    Returns:
        Next optimal sync time
    """
    now = now or datetime.now(timezone.utc)

    # No history, use default
    if not avg_interval_hours:
        return now + timedelta(hours=DEFAULT_SYNC_HOURS)

    # Calculate base interval based on publishing frequency
    if avg_interval_hours < 12:
        # Very active channel - sync frequently
        sync_interval = max(MIN_SYNC_HOURS, avg_interval_hours * 0.5)
    elif avg_interval_hours < 48:
        # Moderately active - sync at half the publish interval
        sync_interval = min(avg_interval_hours * 0.5, 12)
    else:
        # Slow channel - sync daily
        sync_interval = 24

    # Clamp to bounds
    sync_interval = max(MIN_SYNC_HOURS, min(sync_interval, MAX_SYNC_HOURS))

    # If we know when they last published, adjust
    if last_publish_at:
        # Ensure timezone aware
        if last_publish_at.tzinfo is None:
            last_publish_at = last_publish_at.replace(tzinfo=timezone.utc)

        hours_since_publish = (now - last_publish_at).total_seconds() / 3600

        # If they're "overdue" for a publish, check more frequently
        if hours_since_publish > avg_interval_hours * 0.8:
            sync_interval = min(sync_interval, MIN_SYNC_HOURS)

    return now + timedelta(hours=sync_interval)


def update_publish_stats(
    subscription: "YouTubeSubscription",
    session: Session,
) -> None:
    """Update subscription's publishing statistics after syncing videos.

    Calculates average publish interval from recent videos.

    Args:
        subscription: YouTubeSubscription to update
        session: Database session (sync)
    """
    from app.models.youtube_video import YouTubeVideo

    # Get recent video publish times (up to 20)
    result = session.execute(
        select(YouTubeVideo.published_at)
        .where(
            YouTubeVideo.subscription_id == str(subscription.id),
            YouTubeVideo.published_at.is_not(None),
        )
        .order_by(YouTubeVideo.published_at.desc())
        .limit(20)
    )
    publish_times = [row[0] for row in result.all() if row[0]]

    if len(publish_times) < 2:
        # Not enough data to calculate interval
        return

    # Calculate average interval between videos
    intervals = []
    for i in range(len(publish_times) - 1):
        time1 = publish_times[i]
        time2 = publish_times[i + 1]

        # Ensure timezone aware
        if time1.tzinfo is None:
            time1 = time1.replace(tzinfo=timezone.utc)
        if time2.tzinfo is None:
            time2 = time2.replace(tzinfo=timezone.utc)

        interval_hours = (time1 - time2).total_seconds() / 3600
        if interval_hours > 0:
            intervals.append(interval_hours)

    if not intervals:
        return

    # Update subscription stats
    now = datetime.now(timezone.utc)
    subscription.avg_publish_interval_hours = sum(intervals) / len(intervals)
    subscription.last_publish_at = publish_times[0]
    subscription.next_sync_at = calculate_next_sync_time(
        subscription.avg_publish_interval_hours,
        subscription.last_publish_at,
        subscription.videos_synced_at,
        now,
    )
