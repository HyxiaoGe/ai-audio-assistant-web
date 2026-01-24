"""YouTube subscription service for managing user subscriptions."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.models.account import Account
from app.models.youtube_subscription import YouTubeSubscription
from app.services.youtube.data_service import YouTubeDataService
from app.services.youtube.oauth_service import YouTubeOAuthService

logger = logging.getLogger("app.youtube.subscription")

# Provider name for YouTube OAuth account
YOUTUBE_PROVIDER = "youtube"


class YouTubeSubscriptionService:
    """Manages YouTube OAuth accounts and subscription syncing."""

    def __init__(self) -> None:
        self._oauth_service = YouTubeOAuthService()

    async def get_youtube_account(
        self,
        db: AsyncSession,
        user_id: str,
    ) -> Optional[Account]:
        """Get user's YouTube OAuth account.

        Args:
            db: Database session
            user_id: User ID

        Returns:
            Account or None if not connected
        """
        result = await db.execute(
            select(Account).where(
                Account.user_id == user_id,
                Account.provider == YOUTUBE_PROVIDER,
            )
        )
        return result.scalar_one_or_none()

    async def is_connected(self, db: AsyncSession, user_id: str) -> bool:
        """Check if user has connected YouTube.

        Args:
            db: Database session
            user_id: User ID

        Returns:
            True if connected
        """
        account = await self.get_youtube_account(db, user_id)
        return account is not None

    async def save_youtube_account(
        self,
        db: AsyncSession,
        user_id: str,
        channel_id: str,
        access_token: str,
        refresh_token: str,
        expires_at: datetime,
    ) -> Account:
        """Save or update YouTube OAuth account.

        Args:
            db: Database session
            user_id: User ID
            channel_id: YouTube channel ID
            access_token: OAuth access token
            refresh_token: OAuth refresh token
            expires_at: Token expiration time

        Returns:
            Created or updated Account
        """
        account = await self.get_youtube_account(db, user_id)

        if account:
            # Update existing account
            account.provider_account_id = channel_id
            account.access_token = access_token
            account.refresh_token = refresh_token
            account.token_expires_at = expires_at
            logger.info(f"Updated YouTube account for user {user_id}")
        else:
            # Create new account
            account = Account(
                user_id=user_id,
                provider=YOUTUBE_PROVIDER,
                provider_account_id=channel_id,
                access_token=access_token,
                refresh_token=refresh_token,
                token_expires_at=expires_at,
            )
            db.add(account)
            logger.info(f"Created YouTube account for user {user_id}")

        await db.commit()
        await db.refresh(account)
        return account

    async def disconnect(self, db: AsyncSession, user_id: str) -> None:
        """Disconnect YouTube account and remove cached subscriptions.

        Args:
            db: Database session
            user_id: User ID
        """
        # Delete subscriptions
        await db.execute(delete(YouTubeSubscription).where(YouTubeSubscription.user_id == user_id))

        # Delete account
        await db.execute(
            delete(Account).where(
                Account.user_id == user_id,
                Account.provider == YOUTUBE_PROVIDER,
            )
        )

        await db.commit()
        logger.info(f"Disconnected YouTube for user {user_id}")

    async def get_valid_credentials(
        self,
        db: AsyncSession,
        user_id: str,
    ) -> Tuple[Account, Any]:
        """Get valid credentials, refreshing if needed.

        Args:
            db: Database session
            user_id: User ID

        Returns:
            Tuple of (Account, Credentials)

        Raises:
            BusinessError: If not connected or token refresh fails
        """
        account = await self.get_youtube_account(db, user_id)

        if not account:
            raise BusinessError(ErrorCode.YOUTUBE_NOT_CONNECTED)

        # Check if token needs refresh
        if self._oauth_service.is_token_expired(account.token_expires_at):
            if not account.refresh_token:
                raise BusinessError(
                    ErrorCode.YOUTUBE_TOKEN_EXPIRED,
                    reason="No refresh token available",
                )

            logger.info(f"Refreshing token for user {user_id}")
            new_access_token, new_expires_at = self._oauth_service.refresh_access_token(
                account.refresh_token
            )

            # Update account with new token
            account.access_token = new_access_token
            account.token_expires_at = new_expires_at
            await db.commit()

        credentials = self._oauth_service.build_credentials(
            access_token=account.access_token,
            refresh_token=account.refresh_token,
            expires_at=account.token_expires_at,
        )

        return account, credentials

    async def sync_subscriptions(
        self,
        db: AsyncSession,
        user_id: str,
    ) -> int:
        """Sync user's YouTube subscriptions to database.

        Args:
            db: Database session
            user_id: User ID

        Returns:
            Number of subscriptions synced
        """
        account, credentials = await self.get_valid_credentials(db, user_id)

        # Fetch all subscriptions from YouTube
        data_service = YouTubeDataService(credentials)
        subscriptions = data_service.get_all_subscriptions()

        if not subscriptions:
            logger.info(f"No subscriptions found for user {user_id}")
            return 0

        now = datetime.now(timezone.utc)

        # Upsert subscriptions
        for sub in subscriptions:
            stmt = insert(YouTubeSubscription).values(
                user_id=user_id,
                channel_id=sub["channel_id"],
                channel_title=sub["channel_title"],
                channel_thumbnail=sub["channel_thumbnail"],
                channel_description=sub["channel_description"],
                subscribed_at=sub["subscribed_at"],
                last_synced_at=now,
            )

            stmt = stmt.on_conflict_do_update(
                constraint="uk_youtube_subscriptions_user_channel",
                set_={
                    "channel_title": stmt.excluded.channel_title,
                    "channel_thumbnail": stmt.excluded.channel_thumbnail,
                    "channel_description": stmt.excluded.channel_description,
                    "subscribed_at": stmt.excluded.subscribed_at,
                    "last_synced_at": stmt.excluded.last_synced_at,
                    "updated_at": now,
                },
            )

            await db.execute(stmt)

        # Remove subscriptions that no longer exist
        synced_channel_ids = [sub["channel_id"] for sub in subscriptions]
        await db.execute(
            delete(YouTubeSubscription).where(
                YouTubeSubscription.user_id == user_id,
                YouTubeSubscription.channel_id.not_in(synced_channel_ids),
            )
        )

        await db.commit()

        logger.info(f"Synced {len(subscriptions)} subscriptions for user {user_id}")
        return len(subscriptions)

    async def get_cached_subscriptions(
        self,
        db: AsyncSession,
        user_id: str,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[YouTubeSubscription], int]:
        """Get cached subscriptions from database.

        Args:
            db: Database session
            user_id: User ID
            page: Page number (1-indexed)
            page_size: Items per page

        Returns:
            Tuple of (subscriptions list, total count)
        """
        # Get total count
        count_result = await db.execute(
            select(func.count(YouTubeSubscription.id)).where(YouTubeSubscription.user_id == user_id)
        )
        total = count_result.scalar() or 0

        # Get paginated results
        offset = (page - 1) * page_size
        result = await db.execute(
            select(YouTubeSubscription)
            .where(YouTubeSubscription.user_id == user_id)
            .order_by(YouTubeSubscription.channel_title)
            .offset(offset)
            .limit(page_size)
        )
        subscriptions = list(result.scalars().all())

        return subscriptions, total

    async def get_connection_status(
        self,
        db: AsyncSession,
        user_id: str,
    ) -> Dict[str, Any]:
        """Get YouTube connection status.

        Args:
            db: Database session
            user_id: User ID

        Returns:
            Connection status dict
        """
        account = await self.get_youtube_account(db, user_id)

        if not account:
            return {
                "connected": False,
                "channel_id": None,
                "subscription_count": 0,
                "last_synced_at": None,
            }

        # Get subscription count and last sync time
        result = await db.execute(
            select(
                func.count(YouTubeSubscription.id),
                func.max(YouTubeSubscription.last_synced_at),
            ).where(YouTubeSubscription.user_id == user_id)
        )
        row = result.one()
        subscription_count = row[0] or 0
        last_synced_at = row[1]

        return {
            "connected": True,
            "channel_id": account.provider_account_id,
            "subscription_count": subscription_count,
            "last_synced_at": last_synced_at,
            "token_expires_at": account.token_expires_at,
        }
