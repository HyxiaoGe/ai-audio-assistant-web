from __future__ import annotations

from typing import Optional

from jose import ExpiredSignatureError, JWTError, jwt

from app.config import settings
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode


def _get_jwt_config() -> tuple[str, str]:
    secret = settings.JWT_SECRET
    algorithm = settings.JWT_ALGORITHM
    if not secret or not algorithm:
        raise RuntimeError("JWT_SECRET or JWT_ALGORITHM is not set")
    return secret, algorithm


def extract_bearer_token(authorization: Optional[str]) -> str:
    if not authorization:
        raise BusinessError(ErrorCode.AUTH_TOKEN_NOT_PROVIDED)
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise BusinessError(ErrorCode.AUTH_TOKEN_INVALID)
    return token


def decode_access_token(token: str) -> dict[str, object]:
    if not token:
        raise BusinessError(ErrorCode.AUTH_TOKEN_NOT_PROVIDED)
    secret, algorithm = _get_jwt_config()
    try:
        payload: dict[str, object] = jwt.decode(
            token,
            secret,
            algorithms=[algorithm],
            options={"verify_aud": False},
        )
        return payload
    except ExpiredSignatureError as exc:
        raise BusinessError(ErrorCode.AUTH_TOKEN_EXPIRED) from exc
    except JWTError as exc:
        raise BusinessError(ErrorCode.AUTH_TOKEN_INVALID) from exc
