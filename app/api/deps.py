from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncGenerator, Optional
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessError
from app.core.security import extract_bearer_token, verify_access_token
from app.db import get_db_session
from app.i18n.codes import ErrorCode
from app.models.user import UserProfile


@dataclass
class CurrentUser:
    """Lightweight user identity resolved from JWT."""

    id: str  # auth-service user_id (from JWT sub)
    email: str  # from JWT claims
    scopes: list[str] = field(default_factory=list)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_db_session():
        yield session


async def _resolve_user(db: AsyncSession, token: str) -> CurrentUser:
    """Verify JWT and ensure local profile exists."""
    auth_user = await verify_access_token(token)
    user_id = auth_user.sub
    if not user_id:
        raise BusinessError(ErrorCode.AUTH_TOKEN_INVALID)

    # Ensure local profile exists (auto-create on first login)
    profile = await db.get(UserProfile, UUID(user_id))
    if profile is None:
        profile = UserProfile(id=UUID(user_id))
        db.add(profile)
        await db.flush()

    return CurrentUser(id=user_id, email=auth_user.email, scopes=auth_user.scopes)


async def get_current_user(
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
) -> CurrentUser:
    token = extract_bearer_token(authorization)
    return await _resolve_user(db, token)


def is_admin_user(user: CurrentUser) -> bool:
    """Check if user has admin scope (from JWT)."""
    return "admin" in user.scopes


async def get_admin_user(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not is_admin_user(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def get_current_user_optional(
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
) -> Optional[CurrentUser]:
    if not authorization:
        return None
    return await get_current_user(db, authorization)


async def get_current_user_from_query(
    db: AsyncSession = Depends(get_db),
    token: Optional[str] = Query(default=None, description="JWT token"),
    authorization: Optional[str] = Header(default=None),
) -> CurrentUser:
    """Support token from query or header (for SSE)."""
    if authorization:
        return await get_current_user(db, authorization)

    if not token:
        raise BusinessError(ErrorCode.AUTH_TOKEN_NOT_PROVIDED)

    return await _resolve_user(db, token)
