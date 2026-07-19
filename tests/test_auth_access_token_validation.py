"""auth-service 访问令牌的 issuer / audience / type 契约回归测试。"""

from __future__ import annotations

import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from app.core import security

KID = "auth-key-1"
ISSUER = "https://auth.example.com"
AUDIENCE = "audio-client"


@pytest.fixture(scope="module")
def keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def jwks(keypair):
    jwk = json.loads(RSAAlgorithm.to_jwk(keypair.public_key()))
    jwk.update({"kid": KID, "alg": "RS256", "use": "sig"})
    return {"keys": [jwk]}


def _mint(keypair, *, issuer: str = ISSUER, audience: str = AUDIENCE, token_type: str = "access") -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": "user-1",
            "email": "user@example.com",
            "iss": issuer,
            "aud": audience,
            "type": token_type,
            "iat": now,
            "exp": now + 3600,
        },
        keypair,
        algorithm="RS256",
        headers={"kid": KID},
    )


@pytest.fixture
def validator(monkeypatch: pytest.MonkeyPatch, jwks):
    monkeypatch.setattr(security.settings, "AUTH_SERVICE_URL", f"{ISSUER}/")
    monkeypatch.setattr(security.settings, "AUTH_SERVICE_INTERNAL_URL", None)
    monkeypatch.setattr(security.settings, "AUTH_SERVICE_JWKS_URL", "http://fake/jwks")
    monkeypatch.setattr(security.settings, "AUTH_SERVICE_CLIENT_ID", AUDIENCE, raising=False)
    monkeypatch.setattr(security, "_validator", None)

    configured = security.get_jwt_validator()
    configured._jwks_cache = jwks
    configured._cache_time = time.time()
    return configured


@pytest.mark.asyncio
async def test_correct_access_token_is_accepted(validator, keypair) -> None:
    user = await validator.verify_async(_mint(keypair))
    assert user.sub == "user-1"
    assert validator.issuer == ISSUER
    assert validator.audience == AUDIENCE
    assert validator.require_token_type == "access"


@pytest.mark.asyncio
async def test_other_application_audience_is_rejected(validator, keypair) -> None:
    with pytest.raises(jwt.InvalidAudienceError):
        await validator.verify_async(_mint(keypair, audience="fusion-client"))


@pytest.mark.asyncio
async def test_wrong_issuer_is_rejected(validator, keypair) -> None:
    with pytest.raises(jwt.InvalidIssuerError):
        await validator.verify_async(_mint(keypair, issuer="https://evil.example.com"))


@pytest.mark.asyncio
async def test_refresh_token_is_rejected(validator, keypair) -> None:
    with pytest.raises(jwt.InvalidTokenError):
        await validator.verify_async(_mint(keypair, token_type="refresh"))


def test_missing_client_id_does_not_disable_audience_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security.settings, "AUTH_SERVICE_CLIENT_ID", None, raising=False)
    monkeypatch.setattr(security, "_validator", None)

    with pytest.raises(RuntimeError, match="AUTH_SERVICE_CLIENT_ID"):
        security.get_jwt_validator()

    assert security._validator is None


@pytest.mark.parametrize("issuer", ["", "   ", "/"])
def test_invalid_issuer_does_not_disable_issuer_validation(
    monkeypatch: pytest.MonkeyPatch, issuer: str
) -> None:
    monkeypatch.setattr(security.settings, "AUTH_SERVICE_URL", issuer)
    monkeypatch.setattr(security.settings, "AUTH_SERVICE_CLIENT_ID", AUDIENCE)
    monkeypatch.setattr(security, "_validator", None)

    with pytest.raises(RuntimeError, match="AUTH_SERVICE_URL"):
        security.get_jwt_validator()

    assert security._validator is None
