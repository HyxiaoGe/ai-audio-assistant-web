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
    _reject_privileged_user_fields({"timeout": 30, "retry_count": 3, "default_model": "m", "use_ssl": True})


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


# --------------------------------------------------------------------------- #
# P1-B.1: rollback_my_config 对称补特权字段拦截
# --------------------------------------------------------------------------- #
from types import SimpleNamespace

import app.api.v1.config_center as cc
from app.i18n.codes import ErrorCode
from app.models.service_config import ServiceConfig
from app.models.service_config_history import ServiceConfigHistory


class _FakeExecResult:
    def __init__(self, scalar: object = None, first: object = None) -> None:
        self._scalar = scalar
        self._first = first

    def scalar_one_or_none(self) -> object:
        return self._scalar

    def scalars(self) -> _FakeExecResult:
        return self

    def first(self) -> object:
        return self._first


class _FakeRollbackDb:
    """两次 execute:先 ServiceConfig,后 ServiceConfigHistory。"""

    def __init__(self, record: object, history: object) -> None:
        self._results = [_FakeExecResult(scalar=record), _FakeExecResult(first=history)]
        self.added: list[object] = []

    async def execute(self, stmt: object) -> _FakeExecResult:
        return self._results.pop(0)

    def add(self, item: object) -> None:
        self.added.append(item)

    async def commit(self) -> None:
        return None

    async def refresh(self, obj: object) -> None:
        return None


async def _async_noop(*args: object, **kwargs: object) -> None:
    return None


@pytest.mark.asyncio
async def test_rollback_rejects_privileged_history_config() -> None:
    record = ServiceConfig(
        service_type="llm", provider="deepseek", owner_user_id="u1", config={}, enabled=True, version=2
    )
    history = ServiceConfigHistory(
        service_type="llm",
        provider="deepseek",
        owner_user_id="u1",
        version=1,
        config={"base_url": "http://evil.internal"},  # 特权字段
        enabled=True,
    )
    db = _FakeRollbackDb(record, history)
    user = SimpleNamespace(id="u1")
    payload = SimpleNamespace(version=1, note=None)

    with pytest.raises(BusinessError) as ei:
        await cc.rollback_my_config("llm", "deepseek", payload, db=db, user=user)
    assert ei.value.code == ErrorCode.PERMISSION_DENIED
    assert db.added == []  # 拒绝发生在写历史快照之前


@pytest.mark.asyncio
async def test_rollback_allows_benign_history_config(monkeypatch: pytest.MonkeyPatch) -> None:
    record = ServiceConfig(
        service_type="llm", provider="deepseek", owner_user_id="u1", config={"model": "old"}, enabled=True, version=2
    )
    history = ServiceConfigHistory(
        service_type="llm",
        provider="deepseek",
        owner_user_id="u1",
        version=1,
        config={"model": "new"},  # 无特权字段
        enabled=True,
    )
    db = _FakeRollbackDb(record, history)
    user = SimpleNamespace(id="u1")
    payload = SimpleNamespace(version=1, note=None)

    monkeypatch.setattr(cc.ConfigManager, "refresh_from_db", _async_noop)
    monkeypatch.setattr(cc, "_serialize_config", lambda rec: {"ok": True})

    await cc.rollback_my_config("llm", "deepseek", payload, db=db, user=user)

    assert record.config == {"model": "new"}  # 回滚生效
    assert record.version == 3  # 版本自增
