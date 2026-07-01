from __future__ import annotations

import pytest

import app.services.feature.configs  # noqa: F401  # 触发 feature/discover schema 注册
from app.core.config_manager import ConfigManager
from app.services.feature.configs import DiscoverFeatureConfig
from app.services.feature.flags import is_discover_enabled


def test_default_enabled_when_no_config() -> None:
    ConfigManager.clear("feature")
    assert is_discover_enabled() is True


def test_disabled_when_cached_false() -> None:
    ConfigManager.clear("feature")
    ConfigManager._cache_config("feature", "discover", DiscoverFeatureConfig(enabled=False), None)
    assert is_discover_enabled() is False


def test_fail_open_on_read_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: object, **_k: object) -> object:
        raise RuntimeError("db down")

    monkeypatch.setattr(ConfigManager, "get_config", _boom)
    assert is_discover_enabled() is True
