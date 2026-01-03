import asyncio

import pytest

from app.core.monitoring import (
    AlertLevel,
    AlertManager,
    MetricsCollector,
    MonitoringConfig,
    MonitoringSystem,
    ServiceMetrics,
    monitor,
)


def test_metrics_collection() -> None:
    collector = MetricsCollector(MonitoringConfig(enable_percentiles=False))
    collector.record_call("llm", "doubao", success=True, duration=1.5)
    collector.record_call("llm", "doubao", success=True, duration=2.0)
    collector.record_call("llm", "doubao", success=False, duration=0.5)

    metrics = collector.get_metrics("llm", "doubao")
    assert metrics is not None
    assert metrics.total_calls == 3
    assert metrics.success_calls == 2
    assert metrics.failed_calls == 1
    assert metrics.error_rate == 1 / 3
    assert 1.0 < metrics.avg_response_time < 2.0


def test_alert_rules() -> None:
    alert_manager = AlertManager(MonitoringConfig())
    metrics = ServiceMetrics("llm", "doubao")
    metrics.total_calls = 100
    metrics.failed_calls = 10
    metrics.avg_response_time = 6.0
    metrics.p99_response_time = 12.0

    alerts = alert_manager.check_rules(metrics)
    rule_ids = {alert.rule_id for alert in alerts}

    assert "high_error_rate" in rule_ids
    assert "slow_response" in rule_ids
    assert "high_p99_latency" in rule_ids
    assert any(alert.level == AlertLevel.WARNING for alert in alerts)


@pytest.mark.asyncio
async def test_monitor_decorator() -> None:
    MonitoringSystem._instance = None
    monitoring = MonitoringSystem.get_instance(MonitoringConfig())

    @monitor("test", "service")
    async def test_function() -> str:
        await asyncio.sleep(0.01)
        return "success"

    result = await test_function()
    assert result == "success"

    metrics = monitoring.collector.get_metrics("test", "service")
    assert metrics is not None
    assert metrics.total_calls == 1
    assert metrics.success_calls == 1
