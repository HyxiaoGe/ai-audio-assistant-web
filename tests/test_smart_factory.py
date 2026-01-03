import pytest

from app.core.smart_factory import SelectionStrategy, SmartFactory, SmartFactoryConfig
from app.core.registry import ServiceMetadata


class _FakeService:
    def __init__(self, name: str, cost: float = 0.0) -> None:
        self.name = name
        self._cost = cost

    def estimate_cost(self, *args, **kwargs) -> float:
        return self._cost


def _configure_factory() -> None:
    SmartFactory.reset()
    SmartFactory.configure(
        SmartFactoryConfig(
            default_strategy=SelectionStrategy.HEALTH_FIRST,
            enable_monitoring=False,
            enable_fault_tolerance=False,
            cache_instances=True,
        )
    )


@pytest.mark.asyncio
async def test_get_service_with_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_factory()

    def fake_get(service_type: str, name: str) -> _FakeService:
        return _FakeService(name)

    monkeypatch.setattr("app.core.smart_factory.ServiceRegistry.get", fake_get)

    service = await SmartFactory.get_service("llm", provider="doubao")
    assert service.name == "doubao"


@pytest.mark.asyncio
async def test_health_first_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_factory()

    monkeypatch.setattr(
        "app.core.smart_factory.ServiceRegistry.list_services",
        lambda service_type: ["svc1", "svc2"],
    )
    monkeypatch.setattr(
        "app.core.smart_factory.HealthChecker.get_healthy_services",
        lambda service_type: ["svc1"],
    )
    monkeypatch.setattr(
        "app.core.smart_factory.ServiceRegistry.get",
        lambda service_type, name: _FakeService(name),
    )
    monkeypatch.setattr(
        "app.core.smart_factory.ServiceRegistry.get_metadata",
        lambda service_type, name: ServiceMetadata(
            name=name,
            service_type=service_type,
            priority=10,
        ),
    )

    service = await SmartFactory.get_service("llm")
    assert service.name == "svc1"


@pytest.mark.asyncio
async def test_cost_first_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_factory()

    monkeypatch.setattr(
        "app.core.smart_factory.HealthChecker.get_healthy_services",
        lambda service_type: ["cheap", "expensive"],
    )
    monkeypatch.setattr(
        "app.core.smart_factory.ServiceRegistry.list_services",
        lambda service_type: ["cheap", "expensive"],
    )

    def fake_get(service_type: str, name: str) -> _FakeService:
        return _FakeService(name, cost=0.1 if name == "cheap" else 0.5)

    def fake_metadata(service_type: str, name: str) -> ServiceMetadata:
        return ServiceMetadata(name=name, service_type=service_type, priority=10)

    monkeypatch.setattr("app.core.smart_factory.ServiceRegistry.get", fake_get)
    monkeypatch.setattr(
        "app.core.smart_factory.ServiceRegistry.get_metadata",
        fake_metadata,
    )

    service = await SmartFactory.get_service(
        "llm",
        strategy=SelectionStrategy.COST_FIRST,
        request_params={"input_tokens": 10, "output_tokens": 5},
    )
    assert service.name == "cheap"


@pytest.mark.asyncio
async def test_performance_first_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_factory()

    monkeypatch.setattr(
        "app.core.smart_factory.HealthChecker.get_healthy_services",
        lambda service_type: ["fast", "slow"],
    )
    monkeypatch.setattr(
        "app.core.smart_factory.ServiceRegistry.list_services",
        lambda service_type: ["fast", "slow"],
    )
    monkeypatch.setattr(
        "app.core.smart_factory.ServiceRegistry.get",
        lambda service_type, name: _FakeService(name),
    )

    def fake_metadata(service_type: str, name: str) -> ServiceMetadata:
        priority = 5 if name == "fast" else 50
        return ServiceMetadata(name=name, service_type=service_type, priority=priority)

    monkeypatch.setattr(
        "app.core.smart_factory.ServiceRegistry.get_metadata",
        fake_metadata,
    )

    service = await SmartFactory.get_service(
        "llm",
        strategy=SelectionStrategy.PERFORMANCE_FIRST,
    )
    assert service.name == "fast"


@pytest.mark.asyncio
async def test_balanced_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    SmartFactory.reset()
    SmartFactory.configure(
        SmartFactoryConfig(
            default_strategy=SelectionStrategy.HEALTH_FIRST,
            enable_monitoring=False,
            enable_fault_tolerance=False,
            cache_instances=True,
            balanced_weights={
                "health": 0.2,
                "cost": 0.2,
                "performance": 0.6,
            },
        )
    )

    monkeypatch.setattr(
        "app.core.smart_factory.HealthChecker.get_healthy_services",
        lambda service_type: ["low_cost", "high_perf"],
    )
    monkeypatch.setattr(
        "app.core.smart_factory.ServiceRegistry.list_services",
        lambda service_type: ["low_cost", "high_perf"],
    )

    def fake_get(service_type: str, name: str) -> _FakeService:
        cost = 1.0 if name == "low_cost" else 3.0
        return _FakeService(name, cost=cost)

    def fake_metadata(service_type: str, name: str) -> ServiceMetadata:
        priority = 90 if name == "low_cost" else 10
        return ServiceMetadata(name=name, service_type=service_type, priority=priority)

    monkeypatch.setattr("app.core.smart_factory.ServiceRegistry.get", fake_get)
    monkeypatch.setattr(
        "app.core.smart_factory.ServiceRegistry.get_metadata",
        fake_metadata,
    )

    service = await SmartFactory.get_service(
        "llm",
        strategy=SelectionStrategy.BALANCED,
        request_params={"input_tokens": 10, "output_tokens": 5},
    )
    assert service.name == "high_perf"


@pytest.mark.asyncio
async def test_custom_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_factory()

    monkeypatch.setattr(
        "app.core.smart_factory.HealthChecker.get_healthy_services",
        lambda service_type: ["a", "b"],
    )
    monkeypatch.setattr(
        "app.core.smart_factory.ServiceRegistry.list_services",
        lambda service_type: ["a", "b"],
    )
    monkeypatch.setattr(
        "app.core.smart_factory.ServiceRegistry.get",
        lambda service_type, name: _FakeService(name),
    )
    monkeypatch.setattr(
        "app.core.smart_factory.ServiceRegistry.get_metadata",
        lambda service_type, name: ServiceMetadata(
            name=name,
            service_type=service_type,
            priority=10,
        ),
    )

    def fake_metadata(service_type: str, name: str) -> ServiceMetadata:
        priority = 10 if name == "a" else 30
        return ServiceMetadata(name=name, service_type=service_type, priority=priority)

    monkeypatch.setattr(
        "app.core.smart_factory.ServiceRegistry.get_metadata",
        fake_metadata,
    )

    def custom_scorer(service: _FakeService, metadata: ServiceMetadata) -> float:
        return 100 - metadata.priority

    service = await SmartFactory.get_service(
        "llm",
        strategy=SelectionStrategy.CUSTOM,
        custom_scorer=custom_scorer,
    )
    assert service.name == "a"


@pytest.mark.asyncio
async def test_service_caching(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_factory()
    calls = {"count": 0}

    def fake_get(service_type: str, name: str) -> _FakeService:
        calls["count"] += 1
        return _FakeService(name)

    monkeypatch.setattr("app.core.smart_factory.ServiceRegistry.get", fake_get)

    service1 = await SmartFactory.get_service("llm", provider="doubao")
    service2 = await SmartFactory.get_service("llm", provider="doubao")
    assert service1 is service2
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_no_healthy_services_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_factory()

    monkeypatch.setattr(
        "app.core.smart_factory.ServiceRegistry.list_services",
        lambda service_type: ["fallback"],
    )
    monkeypatch.setattr(
        "app.core.smart_factory.HealthChecker.get_healthy_services",
        lambda service_type: [],
    )
    monkeypatch.setattr(
        "app.core.smart_factory.ServiceRegistry.get",
        lambda service_type, name: _FakeService(name),
    )
    monkeypatch.setattr(
        "app.core.smart_factory.ServiceRegistry.get_metadata",
        lambda service_type, name: ServiceMetadata(
            name=name,
            service_type=service_type,
            priority=10,
        ),
    )

    service = await SmartFactory.get_service("llm")
    assert service.name == "fallback"
