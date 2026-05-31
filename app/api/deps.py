from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from uuid import UUID

from fastapi import Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessError
from app.core.security import (
    SCOPE_MEDIA,
    SCOPE_STREAM,
    extract_bearer_token,
    verify_access_token,
    verify_scoped_token,
)
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
    authorization: str | None = Header(default=None),
) -> CurrentUser:
    token = extract_bearer_token(authorization)
    return await _resolve_user(db, token)


def is_admin_user(user: CurrentUser) -> bool:
    """Check if user has admin scope (from JWT)."""
    return "admin" in user.scopes


async def get_admin_user(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not is_admin_user(user):
        raise BusinessError(ErrorCode.PERMISSION_DENIED)
    return user


async def get_current_user_optional(
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> CurrentUser | None:
    if not authorization:
        return None
    return await get_current_user(db, authorization)


async def get_current_user_from_query(
    db: AsyncSession = Depends(get_db),
    token: str | None = Query(default=None, description="JWT token"),
    authorization: str | None = Header(default=None),
) -> CurrentUser:
    """Support token from query or header (for SSE)."""
    if authorization:
        return await get_current_user(db, authorization)

    if not token:
        raise BusinessError(ErrorCode.AUTH_TOKEN_NOT_PROVIDED)

    return await _resolve_user(db, token)


def _scoped_user_or_none(
    token: str, *, expected_scope: str, resource: dict[str, str] | None = None
) -> CurrentUser | None:
    """Authenticate a short-lived scoped ticket carried in ``?token=``.

    Returns ``None`` when the token is NOT a (valid) scoped ticket, so the caller
    can fall back to the legacy long-lived access JWT (Phase 1 dual-accept).
    Raises ``AUTH_TOKEN_INVALID`` when it IS a valid ticket but fails
    authorization (wrong scope, or bound to a different resource).
    """
    try:
        claims = verify_scoped_token(token)
    except BusinessError:
        return None  # not our ticket -> let caller try the RS256 access JWT
    if claims.get("scope") != expected_scope:
        raise BusinessError(ErrorCode.AUTH_TOKEN_INVALID)
    if resource is not None and claims.get("resource") != resource:
        raise BusinessError(ErrorCode.AUTH_TOKEN_INVALID)
    # Ticket-authenticated requests only ever need the owner id (ownership gates
    # downstream); the ticket intentionally carries no email/scopes.
    return CurrentUser(id=str(claims["sub"]), email="")


async def get_media_user(
    db: AsyncSession = Depends(get_db),
    token: str | None = Query(default=None, description="access token or media ticket"),
    authorization: str | None = Header(default=None),
) -> CurrentUser:
    """Auth for media proxy / image URLs.

    Accepts (in order): Authorization header, a short-lived ``media`` ticket in
    ``?token=``, or -- during the Phase 1 transition -- the legacy access JWT in
    ``?token=``.
    """
    if authorization:
        return await get_current_user(db, authorization)
    if not token:
        raise BusinessError(ErrorCode.AUTH_TOKEN_NOT_PROVIDED)
    user = _scoped_user_or_none(token, expected_scope=SCOPE_MEDIA)
    if user is not None:
        return user
    return await _resolve_user(db, token)


async def get_stream_user(
    task_id: str,
    summary_type: str = Query(..., description="摘要类型"),
    db: AsyncSession = Depends(get_db),
    token: str | None = Query(default=None, description="access token or stream ticket"),
    authorization: str | None = Header(default=None),
) -> CurrentUser:
    """Auth for SSE summary streams.

    A ``stream`` ticket additionally pins the request to a single
    ``(task_id, summary_type)`` pair so it cannot be replayed against another
    stream. Falls back to the legacy access JWT during Phase 1.
    """
    if authorization:
        return await get_current_user(db, authorization)
    if not token:
        raise BusinessError(ErrorCode.AUTH_TOKEN_NOT_PROVIDED)
    user = _scoped_user_or_none(
        token,
        expected_scope=SCOPE_STREAM,
        resource={"task_id": task_id, "summary_type": summary_type},
    )
    if user is not None:
        return user
    return await _resolve_user(db, token)
