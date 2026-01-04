"""Smart service factory.

Unifies registry, health checks, load balancing, cost optimization, and monitoring.
"""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum
from typing import Any, Callable, Dict, Optional

from pydantic import BaseModel, Field

from app.core.cost_optimizer import CostOptimizer, CostOptimizerConfig, CostStrategy
from app.core.health_checker import HealthChecker
from app.core.load_balancer import (
    BalancingStrategy,
    LoadBalancer,
    LoadBalancerConfig,
    LoadBalancerFactory,
)
from app.core.monitoring import MonitoringSystem
from app.core.registry import ServiceMetadata, ServiceRegistry

logger = logging.getLogger(__name__)


class SelectionStrategy(str, Enum):
    """Service selection strategy."""

    HEALTH_FIRST = "health_first"
    COST_FIRST = "cost_first"
    PERFORMANCE_FIRST = "performance_first"
    BALANCED = "balanced"
    CUSTOM = "custom"


class SmartFactoryConfig(BaseModel):
    """Smart factory configuration."""

    default_strategy: SelectionStrategy = SelectionStrategy.HEALTH_FIRST
    load_balancing_strategy: BalancingStrategy = BalancingStrategy.WEIGHTED_ROUND_ROBIN
    cost_strategy: CostStrategy = CostStrategy.LOWEST_COST
    enable_monitoring: bool = True
    enable_fault_tolerance: bool = True
    balanced_weights: Dict[str, float] = Field(
        default_factory=lambda: {
            "health": 0.4,
            "cost": 0.3,
            "performance": 0.3,
        }
    )
    cache_instances: bool = True
    cache_ttl: int = 0


class SmartFactory:
    """Smart service factory."""

    _instance: Optional["SmartFactory"] = None
    _lock = threading.Lock()

    def __init__(self, config: Optional[SmartFactoryConfig] = None):
        self._config = config or SmartFactoryConfig()
        self._load_balancer: Optional[LoadBalancer] = None
        self._cost_optimizer: Optional[CostOptimizer] = None
        self._service_cache: Dict[str, Dict[str, tuple[Any, float]]] = {}

    @classmethod
    def get_instance(cls, config: Optional[SmartFactoryConfig] = None) -> "SmartFactory":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(config)
        return cls._instance

    @classmethod
    async def get_service(
        cls,
        service_type: str,
        strategy: Optional[SelectionStrategy] = None,
        provider: Optional[str] = None,
        *,
        model_id: Optional[str] = None,
        request_params: Optional[Dict[str, Any]] = None,
        custom_scorer: Optional[Callable[[Any, ServiceMetadata], float]] = None,
    ) -> Any:
        instance = cls.get_instance()

        if service_type == "llm" and not model_id:
            raise ValueError("model_id is required for llm services")

        if provider:
            return await instance._get_specific_service(service_type, provider, model_id)

        strategy = strategy or instance._config.default_strategy
        selected_provider = await instance._select_service(
            service_type,
            strategy,
            request_params,
            custom_scorer,
        )
        if not selected_provider:
            raise ValueError(f"No available {service_type} service found")

        return await instance._get_specific_service(service_type, selected_provider, model_id)

    async def _select_service(
        self,
        service_type: str,
        strategy: SelectionStrategy,
        request_params: Optional[Dict[str, Any]],
        custom_scorer: Optional[Callable[[Any, ServiceMetadata], float]],
    ) -> Optional[str]:
        all_services = ServiceRegistry.list_services(service_type)
        if not all_services:
            return None

        # 先检查是否有健康检查结果，如果没有则运行一次
        healthy_services = HealthChecker.get_healthy_services(service_type)
        if not healthy_services:
            # 可能是健康检查还没运行过，手动触发一次
            logger.info("No health check results for %s, running health check...", service_type)
            for service_name in all_services:
                await HealthChecker.check_service(service_type, service_name, force=True)

            # 再次获取健康服务列表
            healthy_services = HealthChecker.get_healthy_services(service_type)
            if not healthy_services:
                logger.warning(
                    "No healthy %s services available after health check; "
                    "falling back to all services",
                    service_type,
                )
                healthy_services = all_services

        if strategy == SelectionStrategy.HEALTH_FIRST:
            return self._select_by_health(service_type, healthy_services)
        if strategy == SelectionStrategy.COST_FIRST:
            return self._select_by_cost(service_type, healthy_services, request_params)
        if strategy == SelectionStrategy.PERFORMANCE_FIRST:
            return self._select_by_performance(service_type, healthy_services)
        if strategy == SelectionStrategy.BALANCED:
            return self._select_balanced(service_type, healthy_services, request_params)
        if strategy == SelectionStrategy.CUSTOM:
            if not custom_scorer:
                raise ValueError("Custom strategy requires custom_scorer function")
            return self._select_custom(service_type, healthy_services, custom_scorer)

        return healthy_services[0] if healthy_services else None

    def _select_by_health(self, service_type: str, healthy_services: list[str]) -> Optional[str]:
        if not self._load_balancer:
            self._load_balancer = LoadBalancerFactory.create(
                self._config.load_balancing_strategy,
                LoadBalancerConfig(strategy=self._config.load_balancing_strategy),
            )
        return self._load_balancer.select_service(service_type, healthy_services)

    def _select_by_cost(
        self,
        service_type: str,
        healthy_services: list[str],
        request_params: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        if not self._cost_optimizer:
            self._cost_optimizer = CostOptimizer(
                CostOptimizerConfig(
                    strategy=self._config.cost_strategy,
                    enable_health_filter=False,
                )
            )

        selected = self._cost_optimizer.select_service(
            service_type,
            request_params or {},
            candidate_services=healthy_services,
        )
        return selected

    def _select_by_performance(
        self,
        service_type: str,
        healthy_services: list[str],
    ) -> Optional[str]:
        services_with_priority = []
        for name in healthy_services:
            metadata = ServiceRegistry.get_metadata(service_type, name)
            services_with_priority.append((name, metadata.priority if metadata else 100))

        services_with_priority.sort(key=lambda item: item[1])
        return services_with_priority[0][0] if services_with_priority else None

    def _select_balanced(
        self,
        service_type: str,
        healthy_services: list[str],
        request_params: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        weights = self._config.balanced_weights
        cost_scores = self._calculate_cost_scores(service_type, healthy_services, request_params)
        scores: Dict[str, float] = {}

        for name in healthy_services:
            health_score = 1.0
            metadata = ServiceRegistry.get_metadata(service_type, name)
            priority = metadata.priority if metadata else 50
            performance_score = max(0.0, 1.0 - min(priority / 100.0, 1.0))
            cost_score = cost_scores.get(name, 0.5)
            total_score = (
                health_score * weights.get("health", 0.4)
                + cost_score * weights.get("cost", 0.3)
                + performance_score * weights.get("performance", 0.3)
            )
            scores[name] = total_score

        return max(scores, key=scores.get) if scores else None

    def _calculate_cost_scores(
        self,
        service_type: str,
        healthy_services: list[str],
        request_params: Optional[Dict[str, Any]],
    ) -> Dict[str, float]:
        if not request_params:
            return {name: 0.5 for name in healthy_services}

        if not self._cost_optimizer:
            self._cost_optimizer = CostOptimizer(
                CostOptimizerConfig(
                    strategy=self._config.cost_strategy,
                    enable_health_filter=False,
                )
            )

        costs = {
            name: self._cost_optimizer.estimate_request_cost(
                service_type,
                name,
                request_params,
            )
            for name in healthy_services
        }
        max_cost = max(costs.values()) if costs else 0.0
        min_cost = min(costs.values()) if costs else 0.0

        scores: Dict[str, float] = {}
        for name, cost in costs.items():
            if max_cost == min_cost:
                scores[name] = 1.0
            else:
                normalized = (cost - min_cost) / (max_cost - min_cost)
                scores[name] = 1.0 - normalized
        return scores

    def _select_custom(
        self,
        service_type: str,
        healthy_services: list[str],
        custom_scorer: Callable[[Any, ServiceMetadata], float],
    ) -> Optional[str]:
        scores: Dict[str, float] = {}
        for name in healthy_services:
            try:
                service = ServiceRegistry.get(service_type, name)
                metadata = ServiceRegistry.get_metadata(service_type, name)
                scores[name] = custom_scorer(service, metadata)
            except Exception as exc:
                logger.error("Custom scorer failed for %s/%s: %s", service_type, name, exc)
        return max(scores, key=scores.get) if scores else None

    async def _get_specific_service(
        self, service_type: str, provider: str, model_id: Optional[str] = None
    ) -> Any:
        # LLM/ASR 使用包含 model_id 的缓存键；其他类型使用 provider
        cache_key = f"{provider}:{model_id}" if model_id else provider
        cached = self._get_cached_service(service_type, cache_key)
        if cached is not None:
            return cached

        if model_id is None:
            service = ServiceRegistry.get(service_type, provider)
        else:
            service = ServiceRegistry.get(service_type, provider, model_id=model_id)
        if not service:
            raise ValueError(f"Service {service_type}:{provider} not found in registry")

        if self._config.enable_fault_tolerance:
            service = self._wrap_fault_tolerance(service, service_type, provider)

        if self._config.enable_monitoring:
            self._register_monitoring()

        self._cache_service(service_type, cache_key, service)
        return service

    def _wrap_fault_tolerance(self, service: Any, service_type: str, provider: str) -> Any:
        """包装服务以提供统一的容错能力

        注意：当前实现保持简单，因为：
        1. 所有服务类已通过装饰器实现容错（@retry, @circuit_breaker）
        2. 服务层容错配置更灵活（每个服务可有不同配置）
        3. 避免重复包装导致的副作用（双重重试、双重熔断）

        未来如需为第三方服务提供统一容错，可在此方法中实现代理模式。

        Args:
            service: 服务实例
            service_type: 服务类型
            provider: 服务提供商

        Returns:
            包装后的服务实例（当前直接返回原服务）
        """
        # 当前策略：服务自身已实现容错，无需额外包装
        # 优点：避免重复包装，保持服务配置灵活性
        # 如需强制包装，可取消下面代码的注释：

        # from functools import wraps
        # from app.core.fault_tolerance import retry, RetryConfig
        #
        # class FaultTolerantProxy:
        #     """容错代理：为未实现容错的服务提供统一包装"""
        #     def __init__(self, target):
        #         self._target = target
        #
        #     def __getattr__(self, name):
        #         attr = getattr(self._target, name)
        #         if callable(attr) and not name.startswith('_'):
        #             @retry(RetryConfig(max_attempts=3))
        #             async def wrapped(*args, **kwargs):
        #                 return await attr(*args, **kwargs)
        #             return wrapped
        #         return attr
        #
        # return FaultTolerantProxy(service)

        return service

    def _register_monitoring(self) -> None:
        MonitoringSystem.get_instance().start()

    def _get_cached_service(self, service_type: str, provider: str) -> Optional[Any]:
        if not self._config.cache_instances:
            return None

        cached = self._service_cache.get(service_type, {}).get(provider)
        if not cached:
            return None

        instance, cached_at = cached
        if self._config.cache_ttl > 0:
            if (time.time() - cached_at) > self._config.cache_ttl:
                self._service_cache[service_type].pop(provider, None)
                return None
        return instance

    def _cache_service(self, service_type: str, provider: str, service: Any) -> None:
        if not self._config.cache_instances:
            return

        if service_type not in self._service_cache:
            self._service_cache[service_type] = {}

        self._service_cache[service_type][provider] = (service, time.time())

    @classmethod
    def configure(cls, config: SmartFactoryConfig) -> None:
        cls._instance = cls(config)

    @classmethod
    def reset(cls) -> None:
        if cls._instance:
            cls._instance._service_cache.clear()
        cls._instance = None
