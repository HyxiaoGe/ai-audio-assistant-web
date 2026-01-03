from __future__ import annotations

from typing import AsyncGenerator, Optional

from fastapi import Depends, Header, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessError
from app.core.security import decode_access_token, extract_bearer_token
from app.db import get_db_session
from app.i18n.codes import ErrorCode
from app.models.user import User


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_db_session():
        yield session


async def get_current_user(
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
) -> User:
    token = extract_bearer_token(authorization)
    payload = decode_access_token(token)
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise BusinessError(ErrorCode.AUTH_TOKEN_INVALID)
    result = await db.execute(
        select(User).where(User.id == subject, User.deleted_at.is_(None))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise BusinessError(ErrorCode.USER_NOT_FOUND)
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
    # 优先从 header 获取
    if authorization:
        return await get_current_user(db, authorization)

    # 从 query 获取
    if not token:
        raise BusinessError(ErrorCode.AUTH_TOKEN_NOT_PROVIDED)

    payload = decode_access_token(token)
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise BusinessError(ErrorCode.AUTH_TOKEN_INVALID)

    result = await db.execute(
        select(User).where(User.id == subject, User.deleted_at.is_(None))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise BusinessError(ErrorCode.USER_NOT_FOUND)
    return user
