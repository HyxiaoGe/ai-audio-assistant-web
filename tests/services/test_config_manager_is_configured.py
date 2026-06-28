"""单元：ConfigManager.is_configured 静默判定 provider 是否已配置。

关键契约：未配置/校验失败时返回 False 且**不打 ERROR/WARNING 日志**——它专供
best-effort 多 provider 探测（任务清理遍历 oss/cos/minio），未配置是良性情况，绝不能
触发运维哨兵告警。

注意：is_configured 如实反映当前 settings，故测试不依赖环境是否恰好配了某后端，而是
显式清空/填充 minio 字段后断言（本机开发 .env 可能配齐所有后端，dev box 则只配 OSS）。
"""

from __future__ import annotations

import logging

# 触发 storage schema 注册（COSConfig/OSSConfig/MinioConfig 等）
import app.services.storage.configs  # noqa: F401
from app.config import settings
from app.core.config_manager import ConfigManager

_MINIO_FIELDS = ("MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_BUCKET")


def _clear_minio(monkeypatch) -> None:
    for attr in _MINIO_FIELDS:
        monkeypatch.setattr(settings, attr, None, raising=False)


def test_is_configured_false_when_required_fields_missing(monkeypatch) -> None:
    _clear_minio(monkeypatch)
    assert ConfigManager.is_configured("storage", "minio") is False


def test_is_configured_false_for_unknown_schema() -> None:
    assert ConfigManager.is_configured("storage", "does-not-exist") is False
    assert ConfigManager.is_configured("nope", "minio") is False


def test_is_configured_is_silent_no_error_or_warning(caplog, monkeypatch) -> None:
    """探测未配置 provider 不得产生 ERROR/WARNING 级日志（否则哨兵会误报）。"""
    _clear_minio(monkeypatch)
    with caplog.at_level(logging.WARNING):
        assert ConfigManager.is_configured("storage", "minio") is False
    offending = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert offending == [], f"is_configured 不应打 WARNING/ERROR，实际: {[r.getMessage() for r in offending]}"


def test_is_configured_true_when_settings_present(monkeypatch) -> None:
    """settings 提供完整字段时判定为已配置（构造出合法配置实例）。"""
    monkeypatch.setattr(settings, "MINIO_ENDPOINT", "localhost:9000", raising=False)
    monkeypatch.setattr(settings, "MINIO_ACCESS_KEY", "ak", raising=False)
    monkeypatch.setattr(settings, "MINIO_SECRET_KEY", "sk", raising=False)
    monkeypatch.setattr(settings, "MINIO_BUCKET", "audio-assistant", raising=False)

    assert ConfigManager.is_configured("storage", "minio") is True
