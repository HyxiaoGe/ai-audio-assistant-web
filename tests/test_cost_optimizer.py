from datetime import date

import pytest

from app.core.cost_optimizer import CostOptimizer, CostOptimizerConfig, CostStrategy, CostTracker
from app.core.registry import ServiceMetadata


class _FakeLLM:
    def __init__(self, cost: float) -> None:
        self._cost = cost

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return self._cost


def test_lowest_cost_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    optimizer = CostOptimizer(
        CostOptimizerConfig(
            strategy=CostStrategy.LOWEST_COST,
            enable_health_filter=False,
        )
    )
    services = ["doubao", "qwen"]

    def fake_list_services(service_type: str) -> list[str]:
        return services

    def fake_get(service_type: str, name: str) -> _FakeLLM:
        return _FakeLLM(0.0001 if name == "doubao" else 0.0002)

    def fake_metadata(service_type: str, name: str) -> ServiceMetadata:
        return ServiceMetadata(name=name, service_type=service_type, priority=10)

    monkeypatch.setattr(
        "app.core.cost_optimizer.ServiceRegistry.list_services",
        fake_list_services,
    )
    monkeypatch.setattr("app.core.cost_optimizer.ServiceRegistry.get", fake_get)
    monkeypatch.setattr(
        "app.core.cost_optimizer.ServiceRegistry.get_metadata",
        fake_metadata,
    )

    selected = optimizer.select_service(
        "llm",
        {"input_tokens": 100, "output_tokens": 50},
    )
    assert selected == "doubao"


def test_cost_performance_balance(monkeypatch: pytest.MonkeyPatch) -> None:
    optimizer = CostOptimizer(
        CostOptimizerConfig(
            strategy=CostStrategy.COST_PERFORMANCE_BALANCE,
            cost_weight=0.5,
            performance_weight=0.5,
            enable_health_filter=False,
        )
    )
    services = ["doubao", "qwen"]

    def fake_list_services(service_type: str) -> list[str]:
        return services

    def fake_get(service_type: str, name: str) -> _FakeLLM:
        return _FakeLLM(0.0001 if name == "doubao" else 0.0002)

    def fake_metadata(service_type: str, name: str) -> ServiceMetadata:
        priority = 50 if name == "doubao" else 10
        return ServiceMetadata(name=name, service_type=service_type, priority=priority)

    monkeypatch.setattr(
        "app.core.cost_optimizer.ServiceRegistry.list_services",
        fake_list_services,
    )
    monkeypatch.setattr("app.core.cost_optimizer.ServiceRegistry.get", fake_get)
    monkeypatch.setattr(
        "app.core.cost_optimizer.ServiceRegistry.get_metadata",
        fake_metadata,
    )

    selected = optimizer.select_service(
        "llm",
        {"input_tokens": 100, "output_tokens": 50},
    )
    assert selected == "qwen"


def test_budget_constrained(monkeypatch: pytest.MonkeyPatch) -> None:
    optimizer = CostOptimizer(
        CostOptimizerConfig(
            strategy=CostStrategy.BUDGET_CONSTRAINED,
            daily_budget=0.5,
            enable_cost_tracking=True,
        )
    )
    services = ["tencent"]

    def fake_list_services(service_type: str) -> list[str]:
        return services

    def fake_get(service_type: str, name: str) -> _FakeLLM:
        return _FakeLLM(1.0)

    def fake_metadata(service_type: str, name: str) -> ServiceMetadata:
        return ServiceMetadata(name=name, service_type=service_type, priority=10)

    monkeypatch.setattr(
        "app.core.cost_optimizer.ServiceRegistry.list_services",
        fake_list_services,
    )
    monkeypatch.setattr("app.core.cost_optimizer.ServiceRegistry.get", fake_get)
    monkeypatch.setattr(
        "app.core.cost_optimizer.ServiceRegistry.get_metadata",
        fake_metadata,
    )

    selected = optimizer.select_service("asr", {"duration_seconds": 3600})
    assert selected is None


def test_cost_tracking() -> None:
    tracker = CostTracker()
    tracker.record_usage("llm", "doubao", {"input_tokens": 100}, 0.01)
    tracker.record_usage("llm", "doubao", {"input_tokens": 200}, 0.02)

    today_cost = tracker.get_daily_cost(date.today())
    assert today_cost == 0.03

    breakdown = tracker.get_service_breakdown()
    assert breakdown["llm"]["doubao"] == 0.03
