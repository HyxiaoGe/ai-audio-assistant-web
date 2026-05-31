"""ConfigManager._cache_record 对无注册 schema 的记录必须告警而非静默丢弃（OSS-obs）。

DB 里若存在某 provider 的配置行但其 schema 未注册（孤儿/历史遗留行），_cache_record 会直接
early-return 把它丢掉。API 写入路径有 validate_config_data 兜底，所以这里只会被孤儿行触发——
影响低，但静默丢弃使这类配置漂移不可诊断。改为 WARNING（log-only，不改控制流、不写任何行）。
"""

from __future__ import annotations

import logging
import types

import pytest

from app.core.config_manager import ConfigManager


async def test_cache_record_warns_on_unregistered_schema(caplog: pytest.LogCaptureFixture) -> None:
    record = types.SimpleNamespace(
        service_type="storage",
        provider="ghoststore",  # 未注册 schema
        owner_user_id=None,
        config={},
        enabled=True,
    )

    with caplog.at_level(logging.WARNING, logger="app.core.config_manager"):
        await ConfigManager._cache_record(record)

    assert "ghoststore" in caplog.text
    assert any(r.levelno == logging.WARNING for r in caplog.records)
