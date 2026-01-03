from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.user import User
from app.services.avatar_service import AvatarService


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
        result = await db.execute(
            select(Account).where(
                Account.provider == provider,
                Account.provider_account_id == provider_account_id,
            )
        )
        account = result.scalar_one_or_none()
        if account is not None:
            result = await db.execute(
                select(User).where(User.id == account.user_id, User.deleted_at.is_(None))
            )
            user = result.scalar_one_or_none()
            if user is not None:
                if name and user.name != name:
                    user.name = name
                    await db.commit()
                await AvatarService.sync_avatar(db, user, avatar_url)
            return account.user_id

        result = await db.execute(
            select(User).where(User.email == email, User.deleted_at.is_(None))
        )
        user = result.scalar_one_or_none()
        if user is None:
            user = User(email=email, name=name, avatar_url=None)
            db.add(user)
            await db.flush()
        else:
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
        except IntegrityError:
            await db.rollback()
            result = await db.execute(
                select(Account).where(
                    Account.provider == provider,
                    Account.provider_account_id == provider_account_id,
                )
            )
            account = result.scalar_one_or_none()
            if account is not None:
                return account.user_id
            raise

        await AvatarService.sync_avatar(db, user, avatar_url)
        return user.id
