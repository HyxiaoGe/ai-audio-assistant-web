import asyncio

import pytest

from app.core.monitoring import (
    MetricsCollector,
    MonitoringConfig,
    MonitoringSystem,
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


def test_no_bespoke_alert_machinery() -> None:
    """P3-2: bespoke 告警/通知机器已拆除。

    原 AlertManager(内存规则)+ NotificationManager(LogNotifier + 从未实例化的 no-op
    WebhookNotifier)+ MonitoringSystem 的 daemon 告警循环全是死代码:WebhookNotifier 从
    不发 HTTP、指标内存即丢、worker 进程根本不启循环,且与 Kuma/Feishu/dev-ops-sentinel 重复。
    本守卫钉死这些符号不复活(运维告警统一走既有 ops 栈,勿在应用内重造)。
    """
    import app.core.monitoring as m

    for name in (
        "AlertLevel",
        "AlertRule",
        "Alert",
        "AlertManager",
        "NotifierInterface",
        "LogNotifier",
        "WebhookNotifier",
        "NotificationManager",
    ):
        assert not hasattr(m, name), f"{name} 应已删除(勿复活 bespoke 告警循环)"


def test_start_stop_are_noops_and_collector_survives() -> None:
    """start()/stop() 保留为无操作 seam(main.py / SmartFactory 仍调用),计数器照常工作。"""
    MonitoringSystem._instance = None
    system = MonitoringSystem.get_instance(MonitoringConfig())
    system.start()  # 不再 spawn daemon 线程
    system.stop()
    system.collector.record_call("llm", "proxy", success=True, duration=0.1)
    metrics = system.collector.get_metrics("llm", "proxy")
    assert metrics is not None
    assert metrics.total_calls == 1


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
