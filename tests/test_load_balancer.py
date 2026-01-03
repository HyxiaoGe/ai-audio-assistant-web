import pytest

from app.core.load_balancer import (
    BalancingStrategy,
    LeastConnectionsBalancer,
    LoadBalancerConfig,
    LoadBalancerFactory,
    RandomBalancer,
    RoundRobinBalancer,
    WeightedRoundRobinBalancer,
)
from app.core.registry import ServiceMetadata


def test_round_robin() -> None:
    balancer = RoundRobinBalancer(LoadBalancerConfig())
    services = ["doubao", "qwen"]

    assert balancer.select_service("llm", services) == "doubao"
    assert balancer.select_service("llm", services) == "qwen"
    assert balancer.select_service("llm", services) == "doubao"


def test_weighted_round_robin(monkeypatch: pytest.MonkeyPatch) -> None:
    balancer = WeightedRoundRobinBalancer(LoadBalancerConfig())
    services = ["doubao", "qwen"]

    def fake_metadata(service_type: str, name: str) -> ServiceMetadata:
        priority = 10 if name == "doubao" else 20
        return ServiceMetadata(name=name, service_type=service_type, priority=priority)

    monkeypatch.setattr(
        "app.core.load_balancer.ServiceRegistry.get_metadata",
        fake_metadata,
    )

    counts = {"doubao": 0, "qwen": 0}
    for _ in range(120):
        selected = balancer.select_service("llm", services)
        counts[selected] += 1

    assert 70 <= counts["doubao"] <= 90
    assert 30 <= counts["qwen"] <= 50


def test_random_balancer() -> None:
    balancer = RandomBalancer(LoadBalancerConfig())
    services = ["doubao", "qwen"]
    assert balancer.select_service("llm", services) in services


def test_least_connections() -> None:
    balancer = LeastConnectionsBalancer(LoadBalancerConfig())
    services = ["doubao", "qwen"]

    balancer.tracker.increment("llm", "doubao")
    selected = balancer.select_service("llm", services)
    assert selected == "qwen"


def test_health_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_healthy(service_type: str) -> list[str]:
        return ["doubao"]

    def fake_all(service_type: str) -> list[str]:
        return ["doubao", "qwen"]

    monkeypatch.setattr(
        "app.core.load_balancer.HealthChecker.get_healthy_services",
        fake_healthy,
    )
    monkeypatch.setattr(
        "app.core.load_balancer.ServiceRegistry.list_services",
        fake_all,
    )

    balancer = LoadBalancerFactory.create(BalancingStrategy.ROUND_ROBIN)
    for _ in range(5):
        assert balancer.select("llm") == "doubao"
