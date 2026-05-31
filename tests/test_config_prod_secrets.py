"""生产环境必需密钥的 fail-fast 校验（app.config.Settings._require_prod_secrets）。

显式 kwargs 优先级最高，覆盖磁盘 .env，因此这些用例与本机 .env 无关、可重复。
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.config import Settings


def test_prod_requires_field_encryption_key() -> None:
    with pytest.raises(ValidationError) as exc:
        Settings(APP_ENV="production", FIELD_ENCRYPTION_KEY=None, DATABASE_URL="x", REDIS_URL="x")
    assert "FIELD_ENCRYPTION_KEY" in str(exc.value)


def test_prod_with_key_ok() -> None:
    s = Settings(
        APP_ENV="production",
        FIELD_ENCRYPTION_KEY=Fernet.generate_key().decode(),
        JWT_SECRET="prod-jwt-secret",
        DATABASE_URL="x",
        REDIS_URL="x",
    )
    assert s.APP_ENV == "production"


def test_dev_allows_missing_key() -> None:
    s = Settings(APP_ENV="development", FIELD_ENCRYPTION_KEY=None, DATABASE_URL="x", REDIS_URL="x")
    assert s.APP_ENV == "development"


def test_prod_requires_jwt_secret() -> None:
    # _env_file=None 隔离本机 .env，确保 JWT_SECRET 不被磁盘值悄悄填上。
    with pytest.raises(ValidationError) as exc:
        Settings(
            _env_file=None,
            APP_ENV="production",
            FIELD_ENCRYPTION_KEY=Fernet.generate_key().decode(),
            JWT_SECRET=None,
            DATABASE_URL="x",
            REDIS_URL="x",
        )
    assert "JWT_SECRET" in str(exc.value)


def test_dev_allows_missing_jwt_secret() -> None:
    s = Settings(_env_file=None, APP_ENV="development", JWT_SECRET=None, DATABASE_URL="x", REDIS_URL="x")
    assert s.JWT_SECRET is None
