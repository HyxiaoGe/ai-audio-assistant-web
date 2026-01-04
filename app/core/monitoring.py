"""监控告警系统。

提供指标收集、告警规则检测与通知能力，支持装饰器自动上报。
"""

from __future__ import annotations

import inspect
import logging
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from functools import wraps
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("app.core.monitoring")


class AlertLevel(str, Enum):
    """告警级别"""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class MetricType(str, Enum):
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
    enable_alerts: bool = True
    alert_check_interval: int = 30
    alert_cooldown: int = 300
    max_metrics_per_service: int = 1000
    enable_percentiles: bool = True


@dataclass
class MetricData:
    """指标数据"""

    name: str
    value: float
    timestamp: datetime
    labels: Dict[str, str] = field(default_factory=dict)
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


def _percentile(sorted_values: List[float], percentile: float) -> float:
    if not sorted_values:
        return 0.0
    index = int(len(sorted_values) * percentile)
    index = min(max(index, 0), len(sorted_values) - 1)
    return sorted_values[index]


@dataclass
class AlertRule:
    """告警规则"""

    rule_id: str
    name: str
    condition: Callable[[ServiceMetrics], bool]
    level: AlertLevel
    message_template: str
    enabled: bool = True
    cooldown: int = 300
    last_triggered: Optional[datetime] = None


@dataclass
class Alert:
    """告警实例"""

    alert_id: str
    rule_id: str
    service_type: str
    service_name: str
    level: AlertLevel
    message: str
    timestamp: datetime
    metrics: ServiceMetrics
    resolved: bool = False
    resolved_at: Optional[datetime] = None


class MetricsCollector:
    """指标收集器（线程安全）"""

    def __init__(self, config: MonitoringConfig):
        self.config = config
        self._metrics: Dict[str, ServiceMetrics] = {}
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

    def get_metrics(self, service_type: str, service_name: str) -> Optional[ServiceMetrics]:
        key = f"{service_type}:{service_name}"
        with self._lock:
            return self._metrics.get(key)

    def get_all_metrics(self) -> Dict[str, ServiceMetrics]:
        with self._lock:
            return dict(self._metrics)

    def reset_metrics(self, service_type: str, service_name: str) -> None:
        key = f"{service_type}:{service_name}"
        with self._lock:
            self._metrics.pop(key, None)


class AlertManager:
    """告警管理器（线程安全）"""

    def __init__(self, config: MonitoringConfig):
        self.config = config
        self._rules: Dict[str, AlertRule] = {}
        self._active_alerts: List[Alert] = []
        self._alert_history: deque[Alert] = deque(maxlen=1000)
        self._lock = threading.Lock()
        self._register_default_rules()

    def _register_default_rules(self) -> None:
        self.add_rule(
            AlertRule(
                rule_id="high_error_rate",
                name="服务错误率过高",
                condition=lambda m: m.error_rate > 0.05,
                level=AlertLevel.WARNING,
                message_template=(
                    "服务 {service_type}:{service_name} 错误率过高: " "{error_rate:.2%}"
                ),
                cooldown=self.config.alert_cooldown,
            )
        )
        self.add_rule(
            AlertRule(
                rule_id="slow_response",
                name="响应时间过长",
                condition=lambda m: m.avg_response_time > 5.0,
                level=AlertLevel.WARNING,
                message_template=(
                    "服务 {service_type}:{service_name} 响应时间过长: " "{avg_response_time:.2f}s"
                ),
                cooldown=self.config.alert_cooldown,
            )
        )
        self.add_rule(
            AlertRule(
                rule_id="high_p99_latency",
                name="P99延迟过高",
                condition=lambda m: m.p99_response_time > 10.0,
                level=AlertLevel.WARNING,
                message_template=(
                    "服务 {service_type}:{service_name} P99延迟过高: " "{p99_response_time:.2f}s"
                ),
                cooldown=self.config.alert_cooldown,
            )
        )

    def add_rule(self, rule: AlertRule) -> None:
        with self._lock:
            self._rules[rule.rule_id] = rule

    def check_rules(self, metrics: ServiceMetrics) -> List[Alert]:
        triggered_alerts: List[Alert] = []
        with self._lock:
            for rule in self._rules.values():
                if not rule.enabled:
                    continue

                if rule.last_triggered:
                    elapsed = (datetime.now() - rule.last_triggered).total_seconds()
                    if elapsed < rule.cooldown:
                        continue

                try:
                    if rule.condition(metrics):
                        alert = self._create_alert(rule, metrics)
                        triggered_alerts.append(alert)
                        rule.last_triggered = datetime.now()
                except Exception as exc:
                    logger.error("告警规则检查失败: %s, 错误: %s", rule.rule_id, exc)

        return triggered_alerts

    def _create_alert(self, rule: AlertRule, metrics: ServiceMetrics) -> Alert:
        message = rule.message_template.format(
            service_type=metrics.service_type,
            service_name=metrics.service_name,
            error_rate=metrics.error_rate,
            success_rate=metrics.success_rate,
            avg_response_time=metrics.avg_response_time,
            max_response_time=metrics.max_response_time,
            p95_response_time=metrics.p95_response_time,
            p99_response_time=metrics.p99_response_time,
            total_calls=metrics.total_calls,
        )

        alert = Alert(
            alert_id=f"{rule.rule_id}_{datetime.now().timestamp()}",
            rule_id=rule.rule_id,
            service_type=metrics.service_type,
            service_name=metrics.service_name,
            level=rule.level,
            message=message,
            timestamp=datetime.now(),
            metrics=metrics,
        )

        self._active_alerts.append(alert)
        self._alert_history.append(alert)
        return alert

    def get_active_alerts(self) -> List[Alert]:
        with self._lock:
            return [alert for alert in self._active_alerts if not alert.resolved]

    def resolve_alert(self, alert_id: str) -> None:
        with self._lock:
            for alert in self._active_alerts:
                if alert.alert_id == alert_id:
                    alert.resolved = True
                    alert.resolved_at = datetime.now()


class NotifierInterface(ABC):
    """通知器接口"""

    @abstractmethod
    def send(self, alert: Alert) -> None:
        """发送告警通知"""


class LogNotifier(NotifierInterface):
    """日志通知器"""

    def send(self, alert: Alert) -> None:
        log_func = {
            AlertLevel.DEBUG: logger.debug,
            AlertLevel.INFO: logger.info,
            AlertLevel.WARNING: logger.warning,
            AlertLevel.CRITICAL: logger.critical,
        }.get(alert.level, logger.info)

        log_func(
            "[ALERT] %s - %s (服务: %s:%s)",
            alert.level.upper(),
            alert.message,
            alert.service_type,
            alert.service_name,
        )


class WebhookNotifier(NotifierInterface):
    """Webhook 通知器"""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send(self, alert: Alert) -> None:
        logger.info("发送 Webhook 通知到 %s: %s", self.webhook_url, alert.message)


class NotificationManager:
    """通知管理器"""

    def __init__(self) -> None:
        self._notifiers: List[NotifierInterface] = []
        self.register_notifier(LogNotifier())

    def register_notifier(self, notifier: NotifierInterface) -> None:
        self._notifiers.append(notifier)

    def send_notification(self, alert: Alert) -> None:
        for notifier in self._notifiers:
            try:
                notifier.send(alert)
            except Exception as exc:
                logger.error("发送通知失败: %s", exc)


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
    """监控系统（单例模式）"""

    _instance: Optional["MonitoringSystem"] = None
    _lock = threading.Lock()

    def __init__(self, config: Optional[MonitoringConfig] = None):
        if config is None:
            config = MonitoringConfig()

        self.config = config
        self.collector = MetricsCollector(config)
        self.alert_manager = AlertManager(config)
        self.notification_manager = NotificationManager()
        self._running = False
        self._monitor_thread: Optional[threading.Thread] = None

    @classmethod
    def get_instance(cls, config: Optional[MonitoringConfig] = None) -> "MonitoringSystem":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(config)
        return cls._instance

    def start(self) -> None:
        if not self.config.enabled:
            logger.info("监控系统已禁用")
            return

        if self._running:
            return

        self._running = True
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        logger.info("监控系统已启动")

    def stop(self) -> None:
        self._running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
        logger.info("监控系统已停止")

    def _monitor_loop(self) -> None:
        while self._running:
            try:
                if self.config.enable_alerts:
                    all_metrics = self.collector.get_all_metrics()
                    for metrics in all_metrics.values():
                        alerts = self.alert_manager.check_rules(metrics)
                        for alert in alerts:
                            self.notification_manager.send_notification(alert)
            except Exception as exc:
                logger.error("监控循环异常: %s", exc)

            time.sleep(self.config.alert_check_interval)
