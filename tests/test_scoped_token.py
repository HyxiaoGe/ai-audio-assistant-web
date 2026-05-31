"""Unit tests for self-signed scoped media tickets (HS256).

These tickets are intentionally isolated from the long-lived RS256 access JWT:
- signed with the symmetric ``settings.JWT_SECRET`` instead of the auth-service key
- carry a distinct ``typ``/``iss`` so a foreign token can never masquerade as one
- verified with the algorithm hard-pinned to HS256 (algorithm-confusion guard)
"""

from __future__ import annotations

import time

import pytest
from jose import jwt

from app.config import settings
from app.core.exceptions import BusinessError
from app.core.security import issue_scoped_token, verify_scoped_token
from app.i18n.codes import ErrorCode

_TEST_SECRET = "unit-test-secret-please-ignore-0123456789"


@pytest.fixture(autouse=True)
def _force_secret(monkeypatch):
    # Decouple the suite from whatever .env / CI happens to provide.
    monkeypatch.setattr(settings, "JWT_SECRET", _TEST_SECRET)
    monkeypatch.setattr(settings, "JWT_ALGORITHM", "HS256")


def test_round_trip_returns_claims():
    token = issue_scoped_token(sub="user-1", scope="media", ttl=300)
    claims = verify_scoped_token(token)
    assert claims["sub"] == "user-1"
    assert claims["scope"] == "media"


def test_resource_binding_is_preserved():
    token = issue_scoped_token(
        sub="user-1",
        scope="stream",
        ttl=300,
        resource={"task_id": "t-1", "summary_type": "overview"},
    )
    claims = verify_scoped_token(token)
    assert claims["resource"] == {"task_id": "t-1", "summary_type": "overview"}


def test_expired_ticket_is_rejected():
    token = issue_scoped_token(sub="user-1", scope="media", ttl=-1)
    with pytest.raises(BusinessError) as exc_info:
        verify_scoped_token(token)
    assert exc_info.value.code == ErrorCode.AUTH_TOKEN_EXPIRED


def test_tampered_ticket_is_rejected():
    token = issue_scoped_token(sub="user-1", scope="media", ttl=300)
    with pytest.raises(BusinessError) as exc_info:
        verify_scoped_token(token + "tampered")
    assert exc_info.value.code == ErrorCode.AUTH_TOKEN_INVALID


def test_empty_token_is_rejected():
    with pytest.raises(BusinessError) as exc_info:
        verify_scoped_token("")
    assert exc_info.value.code == ErrorCode.AUTH_TOKEN_NOT_PROVIDED


def test_token_signed_with_other_secret_is_rejected():
    forged = jwt.encode(
        {"sub": "user-1", "scope": "media", "typ": "scoped-ticket", "iss": "aaa-web", "exp": int(time.time()) + 300},
        "a-completely-different-secret",
        algorithm="HS256",
    )
    with pytest.raises(BusinessError) as exc_info:
        verify_scoped_token(forged)
    assert exc_info.value.code == ErrorCode.AUTH_TOKEN_INVALID


def test_foreign_hs256_token_without_typ_is_rejected():
    # Correct secret + HS256, but missing our typ/iss markers: must not be accepted.
    foreign = jwt.encode(
        {"sub": "user-1", "scope": "media", "exp": int(time.time()) + 300},
        _TEST_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(BusinessError) as exc_info:
        verify_scoped_token(foreign)
    assert exc_info.value.code == ErrorCode.AUTH_TOKEN_INVALID


def test_other_algorithm_is_rejected():
    # Same secret and our markers, but HS512 instead of HS256: proves the verify
    # whitelist is pinned to HS256 (algorithm-confusion defense).
    other_alg = jwt.encode(
        {"sub": "user-1", "scope": "media", "typ": "scoped-ticket", "iss": "aaa-web", "exp": int(time.time()) + 300},
        _TEST_SECRET,
        algorithm="HS512",
    )
    with pytest.raises(BusinessError) as exc_info:
        verify_scoped_token(other_alg)
    assert exc_info.value.code == ErrorCode.AUTH_TOKEN_INVALID


def test_token_without_exp_is_rejected():
    no_exp = jwt.encode(
        {"sub": "user-1", "scope": "media", "typ": "scoped-ticket", "iss": "aaa-web"},
        _TEST_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(BusinessError) as exc_info:
        verify_scoped_token(no_exp)
    assert exc_info.value.code == ErrorCode.AUTH_TOKEN_INVALID
