from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping
from typing import Any

from auth import AuthenticatedUser, JWTValidator
from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTError

from app.config import settings
from app.core.exceptions import BusinessError
from app.core.redis import get_redis_client
from app.i18n.codes import ErrorCode

logger = logging.getLogger(__name__)

# Singleton JWTValidator instance
_validator: JWTValidator | None = None

# Single Logout: auth-service writes ``revoked_user:{sub}`` = logout instant into the SHARED
# Redis (the same instance audio-web already uses). We reject any access token whose ``iat``
# predates that marker -- see auth-service docs/AUTH_CONTRACT.md for the cross-app contract.
USER_REVOKED_PREFIX = "revoked_user:"
# 新 token 的 sid 是认证会话标识，不是用户标识。auth-service 注销单会话时写入
# revoked_sid:{sid}=1，TTL 为刷新令牌寿命加 60 秒；旧 token 无 sid 时继续兼容。
SESSION_REVOKED_PREFIX = "revoked_sid:"
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# --- Self-signed scoped tickets (media/SSE URLs) ----------------------------
# These short-lived tickets are handed to the browser so that <img>/<audio>/
# EventSource requests -- which cannot send an Authorization header -- can be
# authenticated via a ``?token=`` query string WITHOUT exposing the long-lived
# RS256 access JWT in URLs/proxy logs.
#
# They are deliberately isolated from the access JWT:
#   * signed with the symmetric ``settings.JWT_SECRET`` (HMAC), not the
#     auth-service RSA key;
#   * stamped with a distinct ``typ``/``iss`` so a foreign token can never be
#     accepted here;
#   * verified with the algorithm HARD-PINNED to HS256 -- never widened, never
#     read from config -- which closes the classic RS256<->HS256 confusion.
_SCOPED_ALG = "HS256"
_SCOPED_TYP = "scoped-ticket"
_SCOPED_ISS = "aaa-web"

SCOPE_MEDIA = "media"
SCOPE_STREAM = "stream"


def extract_session_id(payload: Mapping[str, object] | None) -> str | None:
    """读取并校验可选 sid；sid 只标识认证会话，绝不替代业务用户 sub。"""
    if not payload or "sid" not in payload:
        return None
    sid = payload["sid"]
    if not isinstance(sid, str) or not sid or not _SESSION_ID_RE.fullmatch(sid):
        raise ValueError("JWT sid claim must be a non-empty URL-safe string")
    return sid


def issue_scoped_token(
    *,
    sub: str,
    scope: str,
    ttl: int,
    resource: dict[str, Any] | None = None,
    sid: str | None = None,
) -> str:
    """Mint a short-lived HS256 ticket bound to ``sub`` and ``scope``.

    ``resource`` (e.g. ``{"task_id": ..., "summary_type": ...}``) further pins
    the ticket to a single resource for SSE streams.
    """
    if not settings.JWT_SECRET:
        raise BusinessError(ErrorCode.INTERNAL_SERVER_ERROR)
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": sub,
        "scope": scope,
        "typ": _SCOPED_TYP,
        "iss": _SCOPED_ISS,
        "iat": now,
        "exp": now + ttl,
    }
    if resource:
        claims["resource"] = resource
    if sid is not None:
        claims["sid"] = extract_session_id({"sid": sid})
    return jwt.encode(claims, settings.JWT_SECRET, algorithm=_SCOPED_ALG)


def verify_scoped_token(token: str) -> dict[str, Any]:
    """Verify a scoped ticket and return its claims.

    Raises ``BusinessError`` with ``AUTH_TOKEN_*`` on any failure. This NEVER
    accepts an RS256 access JWT: the algorithm whitelist is pinned to HS256 and
    the ``typ``/``iss`` markers are checked explicitly.
    """
    if not token:
        raise BusinessError(ErrorCode.AUTH_TOKEN_NOT_PROVIDED)
    if not settings.JWT_SECRET:
        raise BusinessError(ErrorCode.AUTH_TOKEN_INVALID)
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[_SCOPED_ALG],
        )
    except ExpiredSignatureError as exc:
        raise BusinessError(ErrorCode.AUTH_TOKEN_EXPIRED) from exc
    except JWTError as exc:
        raise BusinessError(ErrorCode.AUTH_TOKEN_INVALID) from exc
    if (
        "exp" not in claims
        or claims.get("typ") != _SCOPED_TYP
        or claims.get("iss") != _SCOPED_ISS
        or not claims.get("sub")
        or not claims.get("scope")
    ):
        raise BusinessError(ErrorCode.AUTH_TOKEN_INVALID)
    try:
        extract_session_id(claims)
    except ValueError as exc:
        raise BusinessError(ErrorCode.AUTH_TOKEN_INVALID) from exc
    return claims


def get_jwt_validator() -> JWTValidator:
    global _validator
    if _validator is None:
        client_id = settings.AUTH_SERVICE_CLIENT_ID
        if not client_id or not client_id.strip():
            # auth-client 在 audience=None 时会关闭 aud 校验。这里必须 fail closed，避免任意由同一
            # auth-service 签发、但属于 Fusion 等其它应用的访问令牌被 audio-web 接受。
            raise RuntimeError("AUTH_SERVICE_CLIENT_ID 未配置，拒绝关闭 JWT audience 校验")
        issuer = settings.auth_service_issuer
        if not issuer:
            # SDK 在 issuer 为 falsy 时同样会关闭 iss 校验；任何环境都不允许以降级方式创建校验器。
            raise RuntimeError("AUTH_SERVICE_URL 未配置有效 issuer，拒绝关闭 JWT issuer 校验")
        # JWKS 取用优先走 LAN 内部基址（见 settings.resolved_auth_jwks_url 的说明）：经公网拉
        # JWKS ~1.5s/次，缓存 300s 一过期、并发认证请求各拉一遍即造成「开页齐卡」；LAN 仅 ~13ms。
        _validator = JWTValidator(
            jwks_url=settings.resolved_auth_jwks_url,
            issuer=issuer,
            audience=client_id.strip(),
            cache_ttl=300,
            require_token_type="access",
        )
    return _validator


def extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise BusinessError(ErrorCode.AUTH_TOKEN_NOT_PROVIDED)
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise BusinessError(ErrorCode.AUTH_TOKEN_INVALID)
    return token


async def is_user_access_revoked(sub: str, token_iat: float | int | None) -> bool:
    """True iff the user's Single-Logout marker post-dates this token's ``iat``.

    Mirrors auth-service's check (AUTH_CONTRACT.md): the marker is a float wall-clock instant
    while a JWT ``iat`` is integer epoch seconds, so strict ``<`` deliberately over-revokes --
    every token minted before the logout is rejected, and a re-login (which takes several OAuth
    round-trips) lands in a later second and survives.

    FAILS OPEN: this runs on every authenticated request, so a shared-Redis blip must not 500
    the whole API -- we log and treat the token as not-revoked, degrading the revocation lag to
    the token's own <=15-min expiry until Redis recovers.
    """
    if not sub or token_iat is None:
        return False
    try:
        raw = await get_redis_client().get(f"{USER_REVOKED_PREFIX}{sub}")
    except Exception:
        logger.warning("SLO revocation check unavailable (Redis); failing open", exc_info=True)
        return False
    if raw is None:
        return False
    return float(token_iat) < float(raw)


async def is_session_access_revoked(sid: str | None) -> bool:
    """会话 sid 是否已被 auth-service 即时吊销；Redis 故障沿用 SLO 失败开放。"""
    if sid is None:
        return False
    try:
        raw = await get_redis_client().get(f"{SESSION_REVOKED_PREFIX}{sid}")
    except Exception:
        logger.warning("SLO session revocation check unavailable (Redis); failing open", exc_info=True)
        return False
    return raw is not None


async def is_auth_context_revoked(
    *,
    sub: str,
    token_iat: float | int | None,
    session_id: str | None,
) -> bool:
    """同时复核单会话与全设备登出标记，供短票和长连接复用。"""
    return await is_session_access_revoked(session_id) or await is_user_access_revoked(sub, token_iat)


async def verify_access_token(token: str) -> AuthenticatedUser:
    if not token:
        raise BusinessError(ErrorCode.AUTH_TOKEN_NOT_PROVIDED)
    validator = get_jwt_validator()
    try:
        user = await validator.verify_async(token)
    except Exception as exc:
        error_msg = str(exc).lower()
        if "expired" in error_msg:
            raise BusinessError(ErrorCode.AUTH_TOKEN_EXPIRED) from exc
        raise BusinessError(ErrorCode.AUTH_TOKEN_INVALID) from exc
    try:
        session_id = extract_session_id(user.raw_payload)
    except ValueError as exc:
        raise BusinessError(ErrorCode.AUTH_TOKEN_INVALID) from exc

    # Single Logout: the signature above is still valid after a foreign logout, so consult the
    # shared-Redis markers before exposing the authenticated user to application code.
    if await is_auth_context_revoked(
        sub=user.sub,
        token_iat=user.raw_payload.get("iat"),
        session_id=session_id,
    ):
        raise BusinessError(ErrorCode.AUTH_TOKEN_INVALID)
    return user
