"""负载均衡器

提供多策略负载均衡能力，支持健康检查过滤、权重配置和最少连接策略。
"""

from __future__ import annotations

import logging
import random
import threading
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Dict, List, Optional

from app.core.health_checker import HealthChecker
from app.core.registry import ServiceMetadata, ServiceRegistry

if TYPE_CHECKING:
    from app.core.cost_optimizer import CostOptimizer

logger = logging.getLogger(__name__)


class BalancingStrategy(str, Enum):
    """负载均衡策略枚举"""

    ROUND_ROBIN = "round_robin"
    WEIGHTED_ROUND_ROBIN = "weighted_round_robin"
    RANDOM = "random"
    LEAST_CONNECTIONS = "least_connections"


@dataclass
class LoadBalancerConfig:
    """负载均衡器配置"""

    strategy: BalancingStrategy = BalancingStrategy.ROUND_ROBIN
    enable_health_check: bool = True
    fallback_to_any: bool = False


class LoadBalancer(ABC):
    """负载均衡器抽象基类"""

    def __init__(self, config: LoadBalancerConfig):
        self.config = config

    @abstractmethod
    def select_service(
        self,
        service_type: str,
        available_services: List[str],
    ) -> Optional[str]:
        """从可用服务中选择一个服务"""

    def get_healthy_services(self, service_type: str) -> List[str]:
        """获取健康的服务列表"""
        return HealthChecker.get_healthy_services(service_type)

    def _get_all_registered_services(self, service_type: str) -> List[str]:
        """获取所有已注册的服务列表"""
        return ServiceRegistry.list_services(service_type)

    def select(self, service_type: str) -> Optional[str]:
        """公共入口：选择一个服务"""
        if self.config.enable_health_check:
            available = self.get_healthy_services(service_type)
        else:
            available = self._get_all_registered_services(service_type)

        if not available and self.config.fallback_to_any:
            available = self._get_all_registered_services(service_type)

        if not available:
            logger.warning("No available services for type: %s", service_type)
            return None

        return self.select_service(service_type, available)


class RoundRobinBalancer(LoadBalancer):
    """轮询负载均衡器"""

    def __init__(self, config: LoadBalancerConfig):
        super().__init__(config)
        self._counters: Dict[str, int] = {}
        self._lock = threading.Lock()

    def select_service(
        self,
        service_type: str,
        available_services: List[str],
    ) -> Optional[str]:
        if not available_services:
            return None

        with self._lock:
            index = self._counters.get(service_type, 0)
            selected = available_services[index % len(available_services)]
            self._counters[service_type] = (index + 1) % len(available_services)
            return selected


class WeightedRoundRobinBalancer(LoadBalancer):
    """加权轮询负载均衡器（平滑加权轮询）"""

    def __init__(self, config: LoadBalancerConfig):
        super().__init__(config)
        self._current_weights: Dict[str, Dict[str, int]] = {}
        self._lock = threading.Lock()

    def select_service(
        self,
        service_type: str,
        available_services: List[str],
    ) -> Optional[str]:
        if not available_services:
            return None

        with self._lock:
            if service_type not in self._current_weights:
                self._current_weights[service_type] = {}

            weights = self._current_weights[service_type]
            total_weight = 0
            selected = None
            selected_weight = None

            for name in available_services:
                weight = self._get_weight(service_type, name)
                total_weight += weight
                weights[name] = weights.get(name, 0) + weight

                if selected is None or weights[name] > selected_weight:
                    selected = name
                    selected_weight = weights[name]

            if selected is None:
                return None

            weights[selected] -= total_weight
            for name in list(weights.keys()):
                if name not in available_services:
                    weights.pop(name, None)

            return selected

    def _get_weight(self, service_type: str, service_name: str) -> int:
        metadata = ServiceRegistry.get_metadata(service_type, service_name)
        return self._weight_from_metadata(metadata)

    @staticmethod
    def _weight_from_metadata(metadata: ServiceMetadata) -> int:
        priority = max(metadata.priority, 1)
        return max(1, 100 // priority)


class RandomBalancer(LoadBalancer):
    """随机负载均衡器"""

    def select_service(
        self,
        service_type: str,
        available_services: List[str],
    ) -> Optional[str]:
        if not available_services:
            return None
        return random.choice(available_services)


class ConnectionTracker:
    """连接数跟踪器"""

    def __init__(self) -> None:
        self._connections: Dict[str, Dict[str, int]] = {}
        self._lock = threading.Lock()

    def increment(self, service_type: str, service_name: str) -> None:
        with self._lock:
            if service_type not in self._connections:
                self._connections[service_type] = {}
            current = self._connections[service_type].get(service_name, 0)
            self._connections[service_type][service_name] = current + 1

    def decrement(self, service_type: str, service_name: str) -> None:
        with self._lock:
            if service_type not in self._connections:
                return
            current = self._connections[service_type].get(service_name, 0)
            self._connections[service_type][service_name] = max(0, current - 1)

    def get_count(self, service_type: str, service_name: str) -> int:
        with self._lock:
            return self._connections.get(service_type, {}).get(service_name, 0)

    def get_all_counts(self, service_type: str) -> Dict[str, int]:
        with self._lock:
            return dict(self._connections.get(service_type, {}))


class LeastConnectionsBalancer(LoadBalancer):
    """最少连接数负载均衡器"""

    def __init__(self, config: LoadBalancerConfig):
        super().__init__(config)
        self.tracker = ConnectionTracker()

    def select_service(
        self,
        service_type: str,
        available_services: List[str],
    ) -> Optional[str]:
        if not available_services:
            return None

        counts = {name: self.tracker.get_count(service_type, name) for name in available_services}
        min_count = min(counts.values())
        candidates = [name for name, count in counts.items() if count == min_count]
        return random.choice(candidates)

    @asynccontextmanager
    async def track_request(self, service_type: str, service_name: str):
        """异步上下文管理器，用于自动跟踪连接数"""
        self.tracker.increment(service_type, service_name)
        try:
            yield
        finally:
            self.tracker.decrement(service_type, service_name)


class LoadBalancerFactory:
    """负载均衡器工厂"""

    _instances: Dict[BalancingStrategy, LoadBalancer] = {}
    _lock = threading.Lock()

    @classmethod
    def create(
        cls,
        strategy: BalancingStrategy,
        config: Optional[LoadBalancerConfig] = None,
    ) -> LoadBalancer:
        if strategy not in cls._instances:
            with cls._lock:
                if strategy not in cls._instances:
                    if config is None:
                        config = LoadBalancerConfig(strategy=strategy)

                    balancer_map: dict[BalancingStrategy, type[LoadBalancer]] = {
                        BalancingStrategy.ROUND_ROBIN: RoundRobinBalancer,
                        BalancingStrategy.WEIGHTED_ROUND_ROBIN: WeightedRoundRobinBalancer,
                        BalancingStrategy.RANDOM: RandomBalancer,
                        BalancingStrategy.LEAST_CONNECTIONS: LeastConnectionsBalancer,
                    }
                    balancer_cls = balancer_map[strategy]
                    cls._instances[strategy] = balancer_cls(config)  # type: ignore[abstract]

        return cls._instances[strategy]

    @classmethod
    def get_default(cls) -> LoadBalancer:
        return cls.create(BalancingStrategy.ROUND_ROBIN)


class CostAwareBalancer(LoadBalancer):
    """成本感知的负载均衡器"""

    def __init__(
        self,
        config: LoadBalancerConfig,
        cost_optimizer: Optional["CostOptimizer"] = None,
    ) -> None:
        super().__init__(config)
        if cost_optimizer is None:
            from app.core.cost_optimizer import CostOptimizer, CostOptimizerConfig

            cost_optimizer = CostOptimizer(
                CostOptimizerConfig(enable_health_filter=config.enable_health_check)
            )
        self.cost_optimizer = cost_optimizer

    def select_service(
        self,
        service_type: str,
        available_services: List[str],
    ) -> Optional[str]:
        if not available_services:
            return None
        return random.choice(available_services)

    def select_with_params(
        self,
        service_type: str,
        request_params: Dict[str, object],
    ) -> Optional[str]:
        if self.config.enable_health_check:
            available = self.get_healthy_services(service_type)
        else:
            available = self._get_all_registered_services(service_type)

        if not available and self.config.fallback_to_any:
            available = self._get_all_registered_services(service_type)

        if not available:
            return None

        selected = self.cost_optimizer.select_service(service_type, request_params)
        if selected in available:
            return selected
        return random.choice(available)
