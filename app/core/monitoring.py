"""服务指标收集（@monitor 装饰器）。

仅保留进程内的调用计数/时延收集。历史上这里还有一套 bespoke 告警机器
(AlertManager 内存规则 + NotificationManager + 从未实例化的 no-op WebhookNotifier +
MonitoringSystem 的 daemon 告警循环),已在 P3-2 拆除:它们是死代码(Webhook 从不发 HTTP、
指标内存即丢、worker 进程根本不启循环),且与既有运维栈(Kuma / Feishu / dev-ops-sentinel)
重复。运维告警统一走那套外部栈,不在应用内重造。需要 /metrics 导出时再单独评估(prometheus
multiprocess + worker exporter 是独立基础设施任务)。
"""

from __future__ import annotations

import inspect
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from functools import wraps

logger = logging.getLogger("app.core.monitoring")


class MetricType(StrEnum):
    """指标类型"""

    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    SUMMARY = "summary"


@dataclass
class MonitoringConfig:
    """监控配置"""

    enabled: bool = True
    collect_interval: int = 10
    metrics_retention: int = 3600
    max_metrics_per_service: int = 1000
    enable_percentiles: bool = True


@dataclass
class MetricData:
    """指标数据"""

    name: str
    value: float
    timestamp: datetime
    labels: dict[str, str] = field(default_factory=dict)
    metric_type: MetricType = MetricType.GAUGE


@dataclass
class ServiceMetrics:
    """服务指标汇总"""

    service_type: str
    service_name: str
    total_calls: int = 0
    success_calls: int = 0
    failed_calls: int = 0
    response_times: deque = field(default_factory=lambda: deque(maxlen=1000))
    avg_response_time: float = 0.0
    max_response_time: float = 0.0
    p95_response_time: float = 0.0
    p99_response_time: float = 0.0
    window_start: datetime = field(default_factory=datetime.now)
    last_update: datetime = field(default_factory=datetime.now)

    @property
    def error_rate(self) -> float:
        if self.total_calls == 0:
            return 0.0
        return self.failed_calls / self.total_calls

    @property
    def success_rate(self) -> float:
        return 1.0 - self.error_rate

    def update_response_time(self, duration: float, enable_percentiles: bool = True) -> None:
        self.response_times.append(duration)
        if not self.response_times:
            return

        sorted_times = sorted(self.response_times)
        self.avg_response_time = sum(sorted_times) / len(sorted_times)
        self.max_response_time = sorted_times[-1]

        if enable_percentiles and len(sorted_times) >= 20:
            self.p95_response_time = _percentile(sorted_times, 0.95)
            self.p99_response_time = _percentile(sorted_times, 0.99)


def _percentile(sorted_values: list[float], percentile: float) -> float:
    if not sorted_values:
        return 0.0
    index = int(len(sorted_values) * percentile)
    index = min(max(index, 0), len(sorted_values) - 1)
    return sorted_values[index]


class MetricsCollector:
    """指标收集器（线程安全）"""

    def __init__(self, config: MonitoringConfig):
        self.config = config
        self._metrics: dict[str, ServiceMetrics] = {}
        self._lock = threading.Lock()

    def record_call(
        self,
        service_type: str,
        service_name: str,
        success: bool,
        duration: float,
    ) -> None:
        key = f"{service_type}:{service_name}"
        with self._lock:
            if key not in self._metrics:
                self._metrics[key] = ServiceMetrics(
                    service_type=service_type,
                    service_name=service_name,
                )

            metrics = self._metrics[key]
            metrics.total_calls += 1
            if success:
                metrics.success_calls += 1
            else:
                metrics.failed_calls += 1

            metrics.update_response_time(duration, self.config.enable_percentiles)
            metrics.last_update = datetime.now()

    def get_metrics(self, service_type: str, service_name: str) -> ServiceMetrics | None:
        key = f"{service_type}:{service_name}"
        with self._lock:
            return self._metrics.get(key)

    def get_all_metrics(self) -> dict[str, ServiceMetrics]:
        with self._lock:
            return dict(self._metrics)

    def reset_metrics(self, service_type: str, service_name: str) -> None:
        key = f"{service_type}:{service_name}"
        with self._lock:
            self._metrics.pop(key, None)


def monitor(service_type: str, service_name: str):
    """监控装饰器：自动收集调用指标"""

    def decorator(func):
        if inspect.isasyncgenfunction(func):

            @wraps(func)
            async def asyncgen_wrapper(*args, **kwargs):
                monitoring = MonitoringSystem.get_instance()
                if not monitoring.config.enabled:
                    async for item in func(*args, **kwargs):
                        yield item
                    return

                start_time = time.monotonic()
                success = False
                try:
                    async for item in func(*args, **kwargs):
                        yield item
                    success = True
                finally:
                    duration = time.monotonic() - start_time
                    monitoring.collector.record_call(
                        service_type,
                        service_name,
                        success,
                        duration,
                    )

            return asyncgen_wrapper

        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                monitoring = MonitoringSystem.get_instance()
                if not monitoring.config.enabled:
                    return await func(*args, **kwargs)

                start_time = time.monotonic()
                success = False
                try:
                    result = await func(*args, **kwargs)
                    success = True
                    return result
                finally:
                    duration = time.monotonic() - start_time
                    monitoring.collector.record_call(
                        service_type,
                        service_name,
                        success,
                        duration,
                    )

            return async_wrapper

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            monitoring = MonitoringSystem.get_instance()
            if not monitoring.config.enabled:
                return func(*args, **kwargs)

            start_time = time.monotonic()
            success = False
            try:
                result = func(*args, **kwargs)
                success = True
                return result
            finally:
                duration = time.monotonic() - start_time
                monitoring.collector.record_call(
                    service_type,
                    service_name,
                    success,
                    duration,
                )

        return sync_wrapper

    return decorator


class MonitoringSystem:
    """监控系统（单例）。

    仅持有进程内的 @monitor 计数器(self.collector)。bespoke 告警 daemon 循环已拆除(P3-2)。
    start()/stop() 保留为无操作 seam,使 app/main.py 的 startup/shutdown 与 SmartFactory 的
    _register_monitoring 既有 wiring 无需改动(同 smart_factory._wrap_fault_tolerance 的
    "保留接缝、掏空实现" 处理)。
    """

    _instance: MonitoringSystem | None = None
    _lock = threading.Lock()

    def __init__(self, config: MonitoringConfig | None = None):
        if config is None:
            config = MonitoringConfig()

        self.config = config
        self.collector = MetricsCollector(config)

    @classmethod
    def get_instance(cls, config: MonitoringConfig | None = None) -> MonitoringSystem:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(config)
        return cls._instance

    def start(self) -> None:
        """无操作 seam（历史上启动告警 daemon 循环;现已拆除,告警走 Kuma/Feishu）。"""

    def stop(self) -> None:
        """无操作 seam。"""
