"""静态数据字段级加密（secrets at rest）。

提供一个 SQLAlchemy ``TypeDecorator``（:class:`EncryptedString`），在写入时
透明加密、读取时透明解密，使用 Fernet（AES-128-CBC + HMAC）。落库的密文字段
（如 OAuth access/refresh token）即使数据库或备份泄露也无法直接复用。

密钥管理：
- 密钥来自 ``settings.FIELD_ENCRYPTION_KEY``，为 urlsafe-base64 的 32 字节 Fernet 密钥。
  生成方式::

      python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

- 支持逗号分隔多把密钥以便轮换：第一把用于加密，所有密钥都会尝试用于解密
  （:class:`~cryptography.fernet.MultiFernet`）。

向后兼容（渐进迁移）：
- 密文以 ``enc:v1:`` 前缀存储。没有该前缀的值被视为历史明文，读取时原样返回，
  因此存量行仍可正常工作，并在下次写入时自动被加密。
- 若未配置密钥，则以明文存储（并打印一次告警）。**生产环境必须设置
  ``FIELD_ENCRYPTION_KEY``。**
"""

from __future__ import annotations

import logging
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.config import settings

logger = logging.getLogger(__name__)

_ENC_PREFIX = "enc:v1:"

# 按密钥内容缓存 cipher，避免每次写入都重建；settings 变更（如测试）会自然失效。
_cipher_cache: dict[str, MultiFernet] = {}


def _get_cipher() -> MultiFernet | None:
    raw = settings.FIELD_ENCRYPTION_KEY
    if not raw:
        return None
    keys = tuple(k.strip() for k in raw.split(",") if k.strip())
    if not keys:
        return None
    cache_key = ",".join(keys)
    cipher = _cipher_cache.get(cache_key)
    if cipher is None:
        cipher = MultiFernet([Fernet(k.encode()) for k in keys])
        _cipher_cache[cache_key] = cipher
    return cipher


def encrypt_secret(value: str) -> str:
    """加密一个字符串密钥；未配置密钥时原样返回明文（仅限非生产）。"""
    cipher = _get_cipher()
    if cipher is None:
        logger.warning("FIELD_ENCRYPTION_KEY 未设置；密钥字段将以明文落库（请勿用于生产）")
        return value
    token = cipher.encrypt(value.encode()).decode()
    return f"{_ENC_PREFIX}{token}"


def decrypt_secret(value: str) -> str:
    """解密；无前缀视为历史明文原样返回，便于存量数据平滑迁移。"""
    if not value.startswith(_ENC_PREFIX):
        return value
    cipher = _get_cipher()
    if cipher is None:
        raise RuntimeError("发现密文字段但 FIELD_ENCRYPTION_KEY 未设置，无法解密")
    try:
        return cipher.decrypt(value[len(_ENC_PREFIX) :].encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("解密失败（FIELD_ENCRYPTION_KEY 不匹配？）") from exc


class EncryptedString(TypeDecorator[str]):
    """对字符串列做落库透明加密 / 读取透明解密的列类型。

    底层仍为 ``TEXT``，因此从现有 ``Text`` 列切换无需变更数据库 schema。
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Any) -> str | None:
        if value is None:
            return None
        return encrypt_secret(value)

    def process_result_value(self, value: str | None, dialect: Any) -> str | None:
        if value is None:
            return None
        return decrypt_secret(value)
