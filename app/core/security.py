from __future__ import annotations

from auth import AuthenticatedUser, JWTValidator

from app.config import settings
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode

# Singleton JWTValidator instance
_validator: JWTValidator | None = None


def get_jwt_validator() -> JWTValidator:
    global _validator
    if _validator is None:
        jwks_url = settings.AUTH_SERVICE_JWKS_URL or f"{settings.AUTH_SERVICE_URL}/.well-known/jwks.json"
        _validator = JWTValidator(jwks_url=jwks_url, cache_ttl=300)
    return _validator


def extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise BusinessError(ErrorCode.AUTH_TOKEN_NOT_PROVIDED)
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise BusinessError(ErrorCode.AUTH_TOKEN_INVALID)
    return token


async def verify_access_token(token: str) -> AuthenticatedUser:
    if not token:
        raise BusinessError(ErrorCode.AUTH_TOKEN_NOT_PROVIDED)
    validator = get_jwt_validator()
    try:
        return await validator.verify_async(token)
    except Exception as exc:
        error_msg = str(exc).lower()
        if "expired" in error_msg:
            raise BusinessError(ErrorCode.AUTH_TOKEN_EXPIRED) from exc
        raise BusinessError(ErrorCode.AUTH_TOKEN_INVALID) from exc
