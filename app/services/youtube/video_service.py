"""YouTube video service for managing cached channel videos."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.models.youtube_subscription import YouTubeSubscription
from app.models.youtube_video import YouTubeVideo
from app.services.youtube.data_service import YouTubeDataService
from app.services.youtube.subscription_service import YouTubeSubscriptionService

logger = logging.getLogger("app.youtube.video")


class YouTubeVideoService:
    """Manages YouTube video caching and retrieval."""

    def __init__(self) -> None:
        self._subscription_service = YouTubeSubscriptionService()

    async def get_cached_videos(
        self,
        db: AsyncSession,
        user_id: str,
        channel_id: str,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[YouTubeVideo], int]:
        """Get cached videos for a channel.

        Args:
            db: Database session
            user_id: User ID
            channel_id: YouTube channel ID
            page: Page number (1-indexed)
            page_size: Items per page

        Returns:
            Tuple of (videos list, total count)
        """
        # Get total count
        count_result = await db.execute(
            select(func.count(YouTubeVideo.id)).where(
                YouTubeVideo.user_id == user_id,
                YouTubeVideo.channel_id == channel_id,
            )
        )
        total = count_result.scalar() or 0

        # Get paginated results ordered by publish date descending
        offset = (page - 1) * page_size
        result = await db.execute(
            select(YouTubeVideo)
            .where(
                YouTubeVideo.user_id == user_id,
                YouTubeVideo.channel_id == channel_id,
            )
            .order_by(YouTubeVideo.published_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        videos = list(result.scalars().all())

        return videos, total

    async def get_latest_videos(
        self,
        db: AsyncSession,
        user_id: str,
        page: int = 1,
        page_size: int = 20,
        exclude_hidden: bool = False,
    ) -> Tuple[List[YouTubeVideo], int]:
        """Get latest videos across all subscriptions.

        Args:
            db: Database session
            user_id: User ID
            page: Page number (1-indexed)
            page_size: Items per page
            exclude_hidden: Exclude videos from hidden channels

        Returns:
            Tuple of (videos list, total count)
        """
        # Build base query
        base_query = select(YouTubeVideo).where(YouTubeVideo.user_id == user_id)
        count_query = select(func.count(YouTubeVideo.id)).where(YouTubeVideo.user_id == user_id)

        # Exclude hidden channels if requested
        if exclude_hidden:
            # Subquery to get non-hidden channel IDs
            hidden_channels_subq = (
                select(YouTubeSubscription.channel_id)
                .where(
                    YouTubeSubscription.user_id == user_id,
                    YouTubeSubscription.is_hidden == True,  # noqa: E712
                )
                .scalar_subquery()
            )
            base_query = base_query.where(YouTubeVideo.channel_id.not_in(hidden_channels_subq))
            count_query = count_query.where(YouTubeVideo.channel_id.not_in(hidden_channels_subq))

        # Get total count
        count_result = await db.execute(count_query)
        total = count_result.scalar() or 0

        # Get paginated results ordered by publish date descending
        offset = (page - 1) * page_size
        result = await db.execute(
            base_query.order_by(YouTubeVideo.published_at.desc()).offset(offset).limit(page_size)
        )
        videos = list(result.scalars().all())

        return videos, total

    async def get_starred_videos(
        self,
        db: AsyncSession,
        user_id: str,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[YouTubeVideo], int]:
        """Get latest videos from starred channels only.

        Args:
            db: Database session
            user_id: User ID
            page: Page number (1-indexed)
            page_size: Items per page

        Returns:
            Tuple of (videos list, total count)
        """
        # Subquery to get starred channel IDs
        starred_channels_subq = (
            select(YouTubeSubscription.channel_id)
            .where(
                YouTubeSubscription.user_id == user_id,
                YouTubeSubscription.is_starred == True,  # noqa: E712
            )
            .scalar_subquery()
        )

        # Get total count
        count_result = await db.execute(
            select(func.count(YouTubeVideo.id)).where(
                YouTubeVideo.user_id == user_id,
                YouTubeVideo.channel_id.in_(starred_channels_subq),
            )
        )
        total = count_result.scalar() or 0

        # Get paginated results ordered by publish date descending
        offset = (page - 1) * page_size
        result = await db.execute(
            select(YouTubeVideo)
            .where(
                YouTubeVideo.user_id == user_id,
                YouTubeVideo.channel_id.in_(starred_channels_subq),
            )
            .order_by(YouTubeVideo.published_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        videos = list(result.scalars().all())

        return videos, total

    async def get_video_counts_by_channels(
        self,
        db: AsyncSession,
        user_id: str,
        channel_ids: List[str],
    ) -> Dict[str, int]:
        """Get video counts for multiple channels.

        Args:
            db: Database session
            user_id: User ID
            channel_ids: List of channel IDs

        Returns:
            Dict mapping channel_id to video count
        """
        if not channel_ids:
            return {}

        result = await db.execute(
            select(
                YouTubeVideo.channel_id,
                func.count(YouTubeVideo.id),
            )
            .where(
                YouTubeVideo.user_id == user_id,
                YouTubeVideo.channel_id.in_(channel_ids),
            )
            .group_by(YouTubeVideo.channel_id)
        )

        return {row[0]: row[1] for row in result.all()}

    async def get_video_by_id(
        self,
        db: AsyncSession,
        user_id: str,
        video_id: str,
    ) -> Optional[YouTubeVideo]:
        """Get a single video by YouTube video ID.

        Args:
            db: Database session
            user_id: User ID
            video_id: YouTube video ID

        Returns:
            YouTubeVideo or None
        """
        result = await db.execute(
            select(YouTubeVideo).where(
                YouTubeVideo.user_id == user_id,
                YouTubeVideo.video_id == video_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_subscription(
        self,
        db: AsyncSession,
        user_id: str,
        channel_id: str,
    ) -> Optional[YouTubeSubscription]:
        """Get subscription for a channel.

        Args:
            db: Database session
            user_id: User ID
            channel_id: YouTube channel ID

        Returns:
            YouTubeSubscription or None
        """
        result = await db.execute(
            select(YouTubeSubscription).where(
                YouTubeSubscription.user_id == user_id,
                YouTubeSubscription.channel_id == channel_id,
            )
        )
        return result.scalar_one_or_none()

    async def sync_channel_videos(
        self,
        db: AsyncSession,
        user_id: str,
        channel_id: str,
        max_videos: int = 50,
        incremental: bool = True,
    ) -> int:
        """Sync videos for a channel from YouTube API.

        Args:
            db: Database session
            user_id: User ID
            channel_id: YouTube channel ID
            max_videos: Maximum videos to fetch
            incremental: If True, stop when we hit already-cached videos

        Returns:
            Number of videos synced

        Raises:
            BusinessError: If YouTube not connected or subscription not found
        """
        # Get valid credentials
        account, credentials = await self._subscription_service.get_valid_credentials(db, user_id)

        # Get subscription
        subscription = await self.get_subscription(db, user_id, channel_id)
        if not subscription:
            raise BusinessError(
                ErrorCode.YOUTUBE_SUBSCRIPTION_NOT_FOUND,
                reason=f"Subscription not found for channel {channel_id}",
            )

        data_service = YouTubeDataService(credentials)

        # Get or fetch uploads playlist ID
        uploads_playlist_id = subscription.uploads_playlist_id
        if not uploads_playlist_id:
            uploads_playlist_id = data_service.get_channel_uploads_playlist_id(channel_id)
            if not uploads_playlist_id:
                logger.warning(f"No uploads playlist found for channel {channel_id}")
                return 0

            # Cache the playlist ID
            subscription.uploads_playlist_id = uploads_playlist_id
            await db.commit()

        # Get existing video IDs for incremental sync
        existing_video_ids: set[str] = set()
        if incremental:
            result = await db.execute(
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
            videos, next_page_token = data_service.get_playlist_videos(
                playlist_id=uploads_playlist_id,
                page_token=page_token,
                max_results=min(50, max_videos - len(all_videos)),
            )

            for video in videos:
                video_id = video.get("video_id")
                if not video_id:
                    continue

                # Incremental sync: stop if we hit an existing video
                if incremental and video_id in existing_video_ids:
                    stop_fetching = True
                    break

                all_videos.append(video)

            if not next_page_token:
                break

            page_token = next_page_token

        if not all_videos:
            logger.info(f"No new videos to sync for channel {channel_id}")
            subscription.videos_synced_at = now
            await db.commit()
            return 0

        # Batch fetch video details (duration, stats)
        video_ids = [v["video_id"] for v in all_videos]
        video_details = data_service.get_videos_details(video_ids)

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
                channel_id=channel_id,
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

            await db.execute(stmt)
            synced_count += 1

        # Update subscription sync time
        subscription.videos_synced_at = now
        await db.commit()

        logger.info(f"Synced {synced_count} videos for channel {channel_id}")
        return synced_count

    async def get_channel_sync_status(
        self,
        db: AsyncSession,
        user_id: str,
        channel_id: str,
    ) -> Dict[str, Any]:
        """Get sync status for a channel.

        Args:
            db: Database session
            user_id: User ID
            channel_id: YouTube channel ID

        Returns:
            Sync status dict
        """
        subscription = await self.get_subscription(db, user_id, channel_id)
        if not subscription:
            return {
                "subscribed": False,
                "video_count": 0,
                "last_synced_at": None,
            }

        # Get video count
        count_result = await db.execute(
            select(func.count(YouTubeVideo.id)).where(
                YouTubeVideo.user_id == user_id,
                YouTubeVideo.channel_id == channel_id,
            )
        )
        video_count = count_result.scalar() or 0

        return {
            "subscribed": True,
            "channel_title": subscription.channel_title,
            "video_count": video_count,
            "last_synced_at": subscription.videos_synced_at,
            "uploads_playlist_id": subscription.uploads_playlist_id,
        }

    async def delete_channel_videos(
        self,
        db: AsyncSession,
        user_id: str,
        channel_id: str,
    ) -> int:
        """Delete all cached videos for a channel.

        Args:
            db: Database session
            user_id: User ID
            channel_id: YouTube channel ID

        Returns:
            Number of videos deleted
        """
        result = await db.execute(
            delete(YouTubeVideo)
            .where(
                YouTubeVideo.user_id == user_id,
                YouTubeVideo.channel_id == channel_id,
            )
            .returning(YouTubeVideo.id)
        )
        deleted_ids = result.scalars().all()
        await db.commit()

        logger.info(f"Deleted {len(deleted_ids)} videos for channel {channel_id}")
        return len(deleted_ids)

    async def get_sync_overview(
        self,
        db: AsyncSession,
        user_id: str,
    ) -> Dict[str, Any]:
        """Get overall video sync status across all subscriptions.

        Args:
            db: Database session
            user_id: User ID

        Returns:
            Sync overview dict with:
            - total_subscriptions: Total subscribed channels
            - synced_subscriptions: Channels with videos_synced_at set
            - pending_subscriptions: Channels never synced
            - total_videos: Total videos cached
            - channels_with_videos: Unique channels with videos
            - fully_synced: Whether all subscriptions have been synced
            - last_sync_at: Most recent sync time
        """
        # Get subscription counts
        total_sub_result = await db.execute(
            select(func.count(YouTubeSubscription.id)).where(
                YouTubeSubscription.user_id == user_id,
            )
        )
        total_subscriptions = total_sub_result.scalar() or 0

        synced_sub_result = await db.execute(
            select(func.count(YouTubeSubscription.id)).where(
                YouTubeSubscription.user_id == user_id,
                YouTubeSubscription.videos_synced_at.is_not(None),
            )
        )
        synced_subscriptions = synced_sub_result.scalar() or 0

        pending_subscriptions = total_subscriptions - synced_subscriptions

        # Get video counts
        total_videos_result = await db.execute(
            select(func.count(YouTubeVideo.id)).where(
                YouTubeVideo.user_id == user_id,
            )
        )
        total_videos = total_videos_result.scalar() or 0

        channels_with_videos_result = await db.execute(
            select(func.count(func.distinct(YouTubeVideo.channel_id))).where(
                YouTubeVideo.user_id == user_id,
            )
        )
        channels_with_videos = channels_with_videos_result.scalar() or 0

        # Get most recent sync time
        last_sync_result = await db.execute(
            select(func.max(YouTubeSubscription.videos_synced_at)).where(
                YouTubeSubscription.user_id == user_id,
            )
        )
        last_sync_at = last_sync_result.scalar()

        return {
            "total_subscriptions": total_subscriptions,
            "synced_subscriptions": synced_subscriptions,
            "pending_subscriptions": pending_subscriptions,
            "total_videos": total_videos,
            "channels_with_videos": channels_with_videos,
            "fully_synced": pending_subscriptions == 0 and total_subscriptions > 0,
            "last_sync_at": last_sync_at,
        }
