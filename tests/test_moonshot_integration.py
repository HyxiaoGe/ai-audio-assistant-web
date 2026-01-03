"""Moonshot 服务集成测试（验证接入流程）"""

import pytest

from app.core.registry import ServiceRegistry
from app.core.smart_factory import SelectionStrategy, SmartFactory
import app.services.llm  # noqa: F401


def test_moonshot_service_registered() -> None:
    services = ServiceRegistry.list_services("llm")
    assert "moonshot" in services


def test_moonshot_metadata() -> None:
    metadata = ServiceRegistry.get_metadata("llm", "moonshot")
    assert metadata.name == "moonshot"
    assert metadata.service_type == "llm"
    assert metadata.priority == 15
    assert "Moonshot" in metadata.description or "月之暗面" in metadata.description


def test_moonshot_service_instantiation() -> None:
    try:
        service = ServiceRegistry.get("llm", "moonshot")
        assert service is not None
        assert service.provider == "moonshot"
    except RuntimeError as exc:
        if "Moonshot settings are not set" in str(exc):
            pytest.skip("Moonshot API key not configured")
        raise


@pytest.mark.asyncio
async def test_moonshot_health_check() -> None:
    try:
        service = ServiceRegistry.get("llm", "moonshot")
        health = await service.health_check()
        assert isinstance(health, bool)
    except RuntimeError as exc:
        if "Moonshot settings are not set" in str(exc):
            pytest.skip("Moonshot API key not configured")
        raise


def test_moonshot_cost_estimation() -> None:
    try:
        service = ServiceRegistry.get("llm", "moonshot")
        cost = service.estimate_cost(input_tokens=1000, output_tokens=500)
        assert cost > 0
        assert isinstance(cost, float)
        assert 0.015 < cost < 0.02
    except RuntimeError as exc:
        if "Moonshot settings are not set" in str(exc):
            pytest.skip("Moonshot API key not configured")
        raise


@pytest.mark.asyncio
async def test_smart_factory_can_select_moonshot() -> None:
    try:
        service = await SmartFactory.get_service("llm", provider="moonshot")
        assert service.provider == "moonshot"
    except (RuntimeError, ValueError):
        pytest.skip("Moonshot not available")


@pytest.mark.asyncio
async def test_moonshot_in_balanced_selection() -> None:
    try:
        service = await SmartFactory.get_service(
            "llm",
            strategy=SelectionStrategy.BALANCED,
            request_params={"input_tokens": 1000, "output_tokens": 500},
        )
        assert service.provider in ["moonshot", "doubao", "qwen"]
    except (RuntimeError, ValueError):
        pytest.skip("No LLM services available")
