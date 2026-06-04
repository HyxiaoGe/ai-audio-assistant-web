"""Tests for the security patches.

P1: upload file_key ownership/shape validation (task_service)
P2: self-service config credential/endpoint field rejection (config_center)
P3: field-level encryption at rest (app.core.crypto)
P4: strict ingest URL validation against SSRF (task_service)
P9: full-entropy media object ids (no 8-hex truncation)
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.api.v1.config_center import _reject_privileged_user_fields
from app.core import crypto
from app.core.exceptions import BusinessError
from app.services.task_service import TaskService


# --------------------------------------------------------------------------- #
# P3: field encryption at rest
# --------------------------------------------------------------------------- #
def _set_key(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    monkeypatch.setattr(crypto.settings, "FIELD_ENCRYPTION_KEY", value, raising=False)
    crypto._cipher_cache.clear()


def test_encrypt_decrypt_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch, Fernet.generate_key().decode())
    enc = crypto.encrypt_secret("super-secret-token")
    assert enc.startswith("enc:v1:")
    assert "super-secret-token" not in enc
    assert crypto.decrypt_secret(enc) == "super-secret-token"


def test_decrypt_legacy_plaintext_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    # Rows written before encryption was enabled have no prefix and must still read.
    _set_key(monkeypatch, Fernet.generate_key().decode())
    assert crypto.decrypt_secret("legacy-plaintext-token") == "legacy-plaintext-token"


def test_no_key_is_plaintext_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch, None)
    assert crypto.encrypt_secret("x") == "x"
    assert crypto.decrypt_secret("x") == "x"


def test_key_rotation_decrypts_old(monkeypatch: pytest.MonkeyPatch) -> None:
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    _set_key(monkeypatch, old)
    enc = crypto.encrypt_secret("rotate-me")
    # Rotate: new key encrypts, old key retained for decrypt (MultiFernet).
    _set_key(monkeypatch, f"{new},{old}")
    assert crypto.decrypt_secret(enc) == "rotate-me"


# --------------------------------------------------------------------------- #
# P1: upload file_key validation
# --------------------------------------------------------------------------- #
def test_valid_upload_key_accepted() -> None:
    TaskService._validate_upload_file_key("upload/u1/2026/05/30/" + "a" * 32 + ".mp3", "u1")
    TaskService._validate_upload_file_key("upload/u1/2026/05/30/" + "0" * 32, "u1")  # no extension


@pytest.mark.parametrize(
    "key",
    [
        "upload/u2/2026/05/30/" + "a" * 32 + ".mp3",  # another tenant's prefix
        "upload/u1/../u2/2026/05/30/" + "a" * 32,  # path traversal
        "upload/u1/2026/05/30/not-a-uuid.mp3",  # malformed object id
        "evil/u1/2026/05/30/" + "a" * 32,  # wrong root
        "upload/u1/2026/05/30/" + "a" * 32 + "/etc/passwd",  # trailing segment
    ],
)
def test_invalid_upload_key_rejected(key: str) -> None:
    with pytest.raises(BusinessError):
        TaskService._validate_upload_file_key(key, "u1")


# --------------------------------------------------------------------------- #
# P2: self-service config field rejection
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "field",
    ["base_url", "endpoint", "api_key", "secret_key", "access_key_id", "ACCESS_KEY", "Api_Key"],
)
def test_reject_privileged_fields(field: str) -> None:
    with pytest.raises(BusinessError):
        _reject_privileged_user_fields({field: "http://attacker.example"})


def test_allow_benign_fields() -> None:
    # Non-credential tuning fields must still be permitted.
    _reject_privileged_user_fields(
        {"timeout": 30, "retry_count": 3, "default_model": "m", "use_ssl": True}
    )


# --------------------------------------------------------------------------- #
# P4: SSRF — strict ingest URL validation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abcdefghijk",
        "https://youtu.be/abcdefghijk",
        "https://www.bilibili.com/video/BV1xx",
        "http://b23.tv/abcd",
        "HTTPS://YouTube.Com/x",  # host is case-insensitive
    ],
)
def test_ingest_url_allowed(url: str) -> None:
    TaskService.validate_ingest_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/youtube.com",  # metadata IP, substring bypass
        "http://youtube.com.attacker.tld/v",  # suffix spoof
        "http://attacker.tld/?next=youtube.com",  # query substring
        "http://youtu.be@169.254.169.254/",  # userinfo trick
        "http://127.0.0.1/youtube.com",  # loopback v4
        "http://[::1]/youtu.be",  # loopback v6
        "http://10.0.0.5/",  # RFC1918
        "https://2130706433/youtu.be",  # decimal-IP encoding (allowlist must catch)
        "//youtube.com/x",  # scheme-relative (no scheme)
        "file:///etc/passwd",  # non-http scheme
        "httpx://youtube.com/x",  # bogus scheme passing old startswith('http')
        "https://evil.com/",  # disallowed host
        None,  # missing
        "",  # empty
    ],
)
def test_ingest_url_rejected(url: str | None) -> None:
    with pytest.raises(BusinessError):
        TaskService.validate_ingest_url(url)


# --------------------------------------------------------------------------- #
# P9: full-entropy media object ids (defense-in-depth for the media proxy)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("module", "var"),
    [
        ("worker/tasks/image_generator.py", "image_id"),
    ],
)
def test_media_object_ids_use_full_uuid(module: str, var: str) -> None:
    # Read the source (no heavy worker import) and assert the 8-hex truncation
    # is gone and a full 128-bit hex id is used instead. Stops a regression of
    # the format constant that made keys brute-forceable.
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    src = (root / module).read_text(encoding="utf-8")
    assert "uuid.uuid4())[:8]" not in src, f"{module} still truncates the object id"
    assert f"{var} = uuid.uuid4().hex" in src, f"{module} missing full-entropy id"
