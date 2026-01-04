from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.user import User
from app.services.avatar_service import AvatarService

logger = logging.getLogger("app.auth_service")


class AuthService:
    @staticmethod
    async def sync_account(
        db: AsyncSession,
        provider: str,
        provider_account_id: str,
        email: str,
        name: str | None,
        avatar_url: str | None,
    ) -> str:
        try:
            logger.info(f"Syncing account: provider={provider}, email={email}")
            account_result = await db.execute(
                select(Account).where(
                    Account.provider == provider,
                    Account.provider_account_id == provider_account_id,
                )
            )
            account = account_result.scalar_one_or_none()
            if account is not None:
                logger.info(f"Found existing account: user_id={account.user_id}")
                user_result = await db.execute(
                    select(User).where(User.id == account.user_id, User.deleted_at.is_(None))
                )
                user = user_result.scalar_one_or_none()
                if user is not None:
                    if name and user.name != name:
                        user.name = name
                        await db.commit()
                    await AvatarService.sync_avatar(db, user, avatar_url)
                return account.user_id

            logger.info("No existing account found, creating new user/account")
            user_result = await db.execute(
                select(User).where(User.email == email, User.deleted_at.is_(None))
            )
            user = user_result.scalar_one_or_none()
            if user is None:
                logger.info("Creating new user")
                user = User(email=email, name=name, avatar_url=None)
                db.add(user)
                await db.flush()
                logger.info(f"New user created: id={user.id}")
            else:
                logger.info(f"Found existing user by email: id={user.id}")
                if name and user.name != name:
                    user.name = name

            account = Account(
                user_id=user.id,
                provider=provider,
                provider_account_id=provider_account_id,
            )
            db.add(account)
            try:
                await db.commit()
                logger.info("Account committed successfully")
            except IntegrityError as e:
                logger.warning(f"IntegrityError during commit: {e}")
                await db.rollback()
                account_result = await db.execute(
                    select(Account).where(
                        Account.provider == provider,
                        Account.provider_account_id == provider_account_id,
                    )
                )
                account = account_result.scalar_one_or_none()
                if account is not None:
                    return account.user_id
                raise

            await AvatarService.sync_avatar(db, user, avatar_url)
            logger.info(f"Account sync completed: user_id={user.id}")
            return user.id
        except Exception as e:
            logger.exception(f"Error in sync_account: {e}")
            raise
