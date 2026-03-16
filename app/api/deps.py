from __future__ import annotations

from typing import AsyncGenerator, Optional

from fastapi import Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import BusinessError
from app.core.security import extract_bearer_token, verify_access_token
from app.db import get_db_session
from app.i18n.codes import ErrorCode
from app.models.user import User


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_db_session():
        yield session


async def _resolve_user(db: AsyncSession, token: str) -> User:
    """Verify JWT and resolve to local user (auto-create if needed)."""
    auth_user = await verify_access_token(token)
    email = auth_user.email
    if not email:
        raise BusinessError(ErrorCode.AUTH_TOKEN_INVALID)

    result = await db.execute(
        select(User)
        .where(User.email == email, User.deleted_at.is_(None))
        .order_by(User.created_at)
        .limit(1)
    )
    user = result.scalar_one_or_none()

    if user is None:
        # Auto-create local user on first login via auth-service
        name = auth_user.raw_payload.get("name", "")
        user = User(email=email, name=name or None)
        db.add(user)
        await db.flush()

    return user


async def get_current_user(
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
) -> User:
    token = extract_bearer_token(authorization)
    return await _resolve_user(db, token)


def _get_admin_emails() -> set[str]:
    raw = settings.ADMIN_EMAILS or ""
    emails = {item.strip().lower() for item in raw.split(",") if item.strip()}
    return emails


def is_admin_user(user: User) -> bool:
    """检查用户是否为管理员"""
    admins = _get_admin_emails()
    return user.email.lower() in admins if admins else False


async def get_admin_user(user: User = Depends(get_current_user)) -> User:
    admins = _get_admin_emails()
    if not admins:
        raise HTTPException(status_code=403, detail="Admin access not configured")
    if user.email.lower() not in admins:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def get_current_user_optional(
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
) -> Optional[User]:
    if not authorization:
        return None
    return await get_current_user(db, authorization)


async def get_current_user_from_query(
    db: AsyncSession = Depends(get_db),
    token: Optional[str] = Query(default=None, description="JWT token"),
    authorization: Optional[str] = Header(default=None),
) -> User:
    """支持从 query 或 header 获取 token（用于 SSE）"""
    if authorization:
        return await get_current_user(db, authorization)

    if not token:
        raise BusinessError(ErrorCode.AUTH_TOKEN_NOT_PROVIDED)

    return await _resolve_user(db, token)
