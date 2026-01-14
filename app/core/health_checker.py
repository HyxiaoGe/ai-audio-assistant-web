"""健康检查机制

定期检查各服务的可用性，自动标记和剔除不健康的服务。

核心功能：
- 健康检查：调用服务的 health_check() 方法
- 状态跟踪：记录每个服务的健康状态和检查时间
- 故障统计：统计连续失败次数，超过阈值标记为不健康
- 自动恢复：不健康的服务会继续检查，恢复后自动标记为健康
- 查询接口：提供健康状态查询 API

设计原则：
- 工业级：线程安全、异常处理完善
- 可配置：支持配置检查间隔、失败阈值等参数
- 轻量级：健康检查应该快速、低开销
- 非阻塞：健康检查失败不应影响业务逻辑
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from threading import Lock
from typing import Dict, List, Optional

from app.core.config_manager import ConfigManager
from app.core.registry import ServiceRegistry

logger = logging.getLogger(__name__)


class HealthStatus(str, Enum):
    """健康状态枚举"""

    HEALTHY = "healthy"  # 健康
    UNHEALTHY = "unhealthy"  # 不健康
    UNKNOWN = "unknown"  # 未知（未检查过）
    CHECKING = "checking"  # 检查中


@dataclass
class HealthCheckResult:
    """健康检查结果

    Attributes:
        service_type: 服务类型
        service_name: 服务名称
        status: 健康状态
        last_check_time: 最后检查时间
        consecutive_failures: 连续失败次数
        total_checks: 总检查次数
        total_failures: 总失败次数
        error_message: 错误信息（如果失败）
    """

    service_type: str
    service_name: str
    status: HealthStatus = HealthStatus.UNKNOWN
    last_check_time: Optional[datetime] = None
    consecutive_failures: int = 0
    total_checks: int = 0
    total_failures: int = 0
    error_message: str = ""

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "service_type": self.service_type,
            "service_name": self.service_name,
            "status": self.status.value,
            "last_check_time": self.last_check_time.isoformat() if self.last_check_time else None,
            "consecutive_failures": self.consecutive_failures,
            "total_checks": self.total_checks,
            "total_failures": self.total_failures,
            "error_message": self.error_message,
        }


class HealthChecker:
    """健康检查器

    定期检查所有已注册服务的健康状态，并提供查询接口。

    内部结构：
        _results: {
            "llm": {
                "doubao": HealthCheckResult(...),
                "qwen": HealthCheckResult(...),
            },
            ...
        }

    配置参数：
        failure_threshold: 连续失败多少次标记为不健康（默认 3）
        check_timeout: 单次健康检查超时时间（秒，默认 5）

    使用示例：
        # 1. 检查单个服务
        result = await HealthChecker.check_service("llm", "doubao")

        # 2. 检查所有服务
        results = await HealthChecker.check_all()

        # 3. 获取健康状态
        is_healthy = HealthChecker.is_healthy("llm", "doubao")

        # 4. 获取所有健康服务
        healthy_llms = HealthChecker.get_healthy_services("llm")
    """

    # 类变量：存储健康检查结果
    # 格式: {service_type: {name: HealthCheckResult}}
    _results: Dict[str, Dict[str, HealthCheckResult]] = {
        "llm": {},
        "asr": {},
        "storage": {},
    }

    # 线程锁：确保结果更新线程安全
    _lock = Lock()

    # 配置参数
    failure_threshold: int = 3  # 连续失败阈值
    check_timeout: int = 5  # 检查超时时间（秒）
    cache_duration: int = 30  # 缓存时长（秒），ADR-003 要求

    @classmethod
    async def check_service(
        cls,
        service_type: str,
        name: str,
        force: bool = False,
    ) -> HealthCheckResult:
        """检查单个服务的健康状态

        Args:
            service_type: 服务类型
            name: 服务名称
            force: 是否强制刷新缓存（默认 False）

        Returns:
            健康检查结果

        Example:
            # 使用缓存（30秒内返回缓存结果）
            result = await HealthChecker.check_service("llm", "doubao")

            # 强制刷新
            result = await HealthChecker.check_service("llm", "doubao", force=True)
        """
        # 获取或创建健康检查结果
        with cls._lock:
            if name not in cls._results[service_type]:
                cls._results[service_type][name] = HealthCheckResult(
                    service_type=service_type,
                    service_name=name,
                )
            result = cls._results[service_type][name]

            # 检查缓存：如果在缓存时长内且不强制刷新，直接返回缓存结果（ADR-003）
            if not force and result.last_check_time:
                elapsed = (datetime.now() - result.last_check_time).total_seconds()
                if elapsed < cls.cache_duration:
                    return result

            result.status = HealthStatus.CHECKING

        # 执行健康检查
        is_healthy = False
        error_msg = ""
        is_fatal_error = False  # 标记是否为致命错误（配置问题等）

        try:
            if service_type == "llm" and name == "openrouter":
                config = ConfigManager.get_config("llm", "openrouter")
                if not getattr(config, "api_key", None):
                    raise RuntimeError("OpenRouter API key is not set")
                if not getattr(config, "model", None):
                    # OpenRouter 支持按请求指定 model_id，因此仅校验 API key
                    is_healthy = True
                    with cls._lock:
                        result.last_check_time = datetime.now()
                        result.total_checks += 1
                        result.status = HealthStatus.HEALTHY
                        result.consecutive_failures = 0
                        result.error_message = ""
                    return result

            # 获取服务实例
            service = ServiceRegistry.get(service_type, name)

            # 调用健康检查方法（带超时）
            is_healthy = await asyncio.wait_for(
                service.health_check(),
                timeout=cls.check_timeout,
            )

        except asyncio.TimeoutError:
            error_msg = f"Health check timeout ({cls.check_timeout}s)"
            logger.warning(f"Health check timeout for {service_type}/{name}")

        except NotImplementedError:
            # 如果服务未实现 health_check，默认认为健康
            is_healthy = True

        except (RuntimeError, ValueError, TypeError, KeyError) as exc:
            # 致命错误（配置缺失、参数错误等）：立即标记为不健康
            is_fatal_error = True
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.error(
                f"Fatal error in health check for {service_type}/{name}: {exc}",
                exc_info=True,
            )

        except Exception as exc:
            # 其他异常（网络问题、临时故障等）：累积失败次数后才标记不健康
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.error(
                f"Health check failed for {service_type}/{name}: {exc}",
                exc_info=True,
            )

        # 更新结果
        with cls._lock:
            result.last_check_time = datetime.now()
            result.total_checks += 1

            if is_healthy:
                result.status = HealthStatus.HEALTHY
                result.consecutive_failures = 0
                result.error_message = ""
            else:
                result.total_failures += 1
                result.consecutive_failures += 1
                result.error_message = error_msg

                # 致命错误：立即标记为 UNHEALTHY（ADR-009 修复）
                if is_fatal_error:
                    result.status = HealthStatus.UNHEALTHY
                    logger.warning(
                        "Service %s/%s marked as UNHEALTHY due to fatal error: %s",
                        service_type,
                        name,
                        error_msg,
                    )
                # 临时故障：判断是否超过失败阈值
                elif result.consecutive_failures >= cls.failure_threshold:
                    result.status = HealthStatus.UNHEALTHY
                    logger.warning(
                        f"Service {service_type}/{name} marked as UNHEALTHY "
                        f"(consecutive failures: {result.consecutive_failures})"
                    )
                else:
                    result.status = HealthStatus.HEALTHY  # 未超过阈值，仍认为健康

        return result

    @classmethod
    async def check_all(cls) -> Dict[str, Dict[str, HealthCheckResult]]:
        """检查所有已注册服务的健康状态

        Returns:
            所有服务的健康检查结果

        Example:
            results = await HealthChecker.check_all()
            # inspect results as needed
        """
        tasks = []

        # 为所有已注册服务创建检查任务
        for service_type in ["llm", "asr", "storage"]:
            service_names = ServiceRegistry.list_services(service_type)
            for name in service_names:
                task = cls.check_service(service_type, name)
                tasks.append(task)

        # 并发执行所有检查
        await asyncio.gather(*tasks, return_exceptions=True)

        # 返回所有结果
        with cls._lock:
            return {service_type: dict(services) for service_type, services in cls._results.items()}

    @classmethod
    def is_healthy(cls, service_type: str, name: str) -> bool:
        """检查服务是否健康

        Args:
            service_type: 服务类型
            name: 服务名称

        Returns:
            True 如果健康，否则 False
        """
        with cls._lock:
            if name not in cls._results[service_type]:
                return True  # 未检查过，默认认为健康

            result = cls._results[service_type][name]
            return result.status == HealthStatus.HEALTHY

    @classmethod
    def get_status(cls, service_type: str, name: str) -> Optional[HealthCheckResult]:
        """获取服务的健康检查结果

        Args:
            service_type: 服务类型
            name: 服务名称

        Returns:
            健康检查结果，如果未检查过则返回 None
        """
        with cls._lock:
            return cls._results[service_type].get(name)

    @classmethod
    def get_all_results(cls) -> Dict[str, Dict[str, HealthCheckResult]]:
        """获取所有服务的健康检查结果

        Returns:
            所有服务的健康检查结果
        """
        with cls._lock:
            return {service_type: dict(services) for service_type, services in cls._results.items()}

    @classmethod
    def get_healthy_services(cls, service_type: str) -> List[str]:
        """获取所有健康的服务名称

        Args:
            service_type: 服务类型

        Returns:
            健康服务名称列表

        Example:
            healthy_llms = HealthChecker.get_healthy_services("llm")
            # 返回: ["doubao", "qwen"]
        """
        with cls._lock:
            return [
                name
                for name, result in cls._results[service_type].items()
                if result.status == HealthStatus.HEALTHY
            ]

    @classmethod
    def get_unhealthy_services(cls, service_type: str) -> List[str]:
        """获取所有不健康的服务名称

        Args:
            service_type: 服务类型

        Returns:
            不健康服务名称列表
        """
        with cls._lock:
            return [
                name
                for name, result in cls._results[service_type].items()
                if result.status == HealthStatus.UNHEALTHY
            ]

    @classmethod
    def clear(cls) -> None:
        """清空所有健康检查结果（主要用于测试）"""
        with cls._lock:
            for service_type in cls._results:
                cls._results[service_type].clear()
            logger.info("Cleared all health check results")
