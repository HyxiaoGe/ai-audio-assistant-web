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


def _scoped_user(
    token: str, *, expected_scope: str, resource: dict[str, str] | None = None
) -> CurrentUser:
    """Authenticate a short-lived scoped ticket carried in ``?token=``.

    Raises ``AUTH_TOKEN_EXPIRED`` / ``AUTH_TOKEN_INVALID`` directly. The legacy
    long-lived access JWT is NO LONGER accepted on ``?token=`` (Phase 3): media
    and SSE URLs must carry a scoped ticket of the expected scope (and, when
    given, bound to the expected resource). Because verification is no longer
    swallowed into a fallback, an expired ticket now surfaces as EXPIRED rather
    than being masked as INVALID.
    """
    claims = verify_scoped_token(token)
    if claims.get("scope") != expected_scope:
        raise BusinessError(ErrorCode.AUTH_TOKEN_INVALID)
    if resource is not None and claims.get("resource") != resource:
        raise BusinessError(ErrorCode.AUTH_TOKEN_INVALID)
    # Ticket-authenticated requests only ever need the owner id (ownership gates
    # downstream); the ticket intentionally carries no email/scopes.
    return CurrentUser(id=str(claims["sub"]), email="")


async def get_media_user(
    db: AsyncSession = Depends(get_db),
    token: str | None = Query(default=None, description="media ticket"),
    authorization: str | None = Header(default=None),
) -> CurrentUser:
    """Auth for media proxy / image URLs.

    Accepts an Authorization header, or a short-lived ``media`` ticket in
    ``?token=``. The legacy long-lived access JWT is no longer accepted on
    ``?token=`` (Phase 3).
    """
    if authorization:
        return await get_current_user(db, authorization)
    if not token:
        raise BusinessError(ErrorCode.AUTH_TOKEN_NOT_PROVIDED)
    return _scoped_user(token, expected_scope=SCOPE_MEDIA)


async def get_stream_user(
    task_id: str,
    summary_type: str = Query(..., description="摘要类型"),
    db: AsyncSession = Depends(get_db),
    token: str | None = Query(default=None, description="stream ticket"),
    authorization: str | None = Header(default=None),
) -> CurrentUser:
    """Auth for SSE summary streams.

    A ``stream`` ticket pins the request to a single ``(task_id, summary_type)``
    pair so it cannot be replayed against another stream. The legacy access JWT
    is no longer accepted on ``?token=`` (Phase 3).
    """
    if authorization:
        return await get_current_user(db, authorization)
    if not token:
        raise BusinessError(ErrorCode.AUTH_TOKEN_NOT_PROVIDED)
    return _scoped_user(
        token,
        expected_scope=SCOPE_STREAM,
        resource={"task_id": task_id, "summary_type": summary_type},
    )
