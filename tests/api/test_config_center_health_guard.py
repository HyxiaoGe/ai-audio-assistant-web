from __future__ import annotations

from typing import Any

import pytest

from app.api.v1 import config_center
from app.core.config_manager import ConfigManager
from app.core.health_checker import HealthChecker
from app.schemas.config_center import ConfigUpdateRequest


class _FakeResult:
    def scalar_one_or_none(self) -> Any:
        return None


class _FakeDB:
    async def execute(self, _stmt: Any) -> _FakeResult:
        return _FakeResult()

    def add(self, _obj: Any) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def refresh(self, _obj: Any) -> None:
        return None


class _User:
    id = "00000000-0000-0000-0000-000000000000"


@pytest.fixture()
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    recorded: list[tuple[str, str]] = []

    def _validate(_st: str, _pr: str, _data: dict[str, Any]) -> None:
        return None

    async def _refresh(*_a: Any, **_k: Any) -> None:
        return None

    async def _check(service_type: str, provider: str, force: bool = False) -> None:
        recorded.append((service_type, provider))

    monkeypatch.setattr(ConfigManager, "validate_config_data", _validate)
    monkeypatch.setattr(ConfigManager, "refresh_from_db", _refresh)
    monkeypatch.setattr(HealthChecker, "check_service", _check)
    monkeypatch.setattr(config_center, "success", lambda **_k: {"ok": True})
    return recorded


async def test_feature_config_skips_health_check(calls: list[tuple[str, str]]) -> None:
    payload = ConfigUpdateRequest(config={}, enabled=False, note="kill")
    await config_center.upsert_config("feature", "discover", payload, _FakeDB(), _User())
    assert calls == []


async def test_provider_config_runs_health_check(calls: list[tuple[str, str]]) -> None:
    payload = ConfigUpdateRequest(config={}, enabled=True, note="x")
    await config_center.upsert_config("asr", "tencent", payload, _FakeDB(), _User())
    assert calls == [("asr", "tencent")]
