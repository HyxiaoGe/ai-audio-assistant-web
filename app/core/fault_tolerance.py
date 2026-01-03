"""容错机制

实现重试、熔断、降级等容错策略，提升系统的可靠性和韧性。

核心功能：
- 重试（Retry）：指数退避重试，处理瞬时故障
- 熔断（Circuit Breaker）：防止级联故障，快速失败
- 降级（Fallback）：提供备用方案，保证服务可用性
- 超时控制：防止长时间等待

设计原则：
- 工业级：基于业界成熟的容错模式（Netflix Hystrix, Resilience4j）
- 可配置：支持灵活配置重试次数、超时时间等参数
- 可观测：记录重试、熔断事件，便于监控和调试
- 非侵入：使用装饰器模式，不修改业务逻辑
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from functools import wraps
from threading import Lock, RLock
from typing import Any, Callable, Dict, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar('T')


# ==================== 重试机制 ====================

@dataclass
class RetryConfig:
    """重试配置

    Attributes:
        max_attempts: 最大尝试次数（包括首次调用）
        initial_delay: 初始延迟（秒）
        max_delay: 最大延迟（秒）
        exponential_base: 指数退避基数
        jitter: 是否添加随机抖动（防止重试风暴）
    """
    max_attempts: int = 3
    initial_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    jitter: bool = True


def retry(
    config: Optional[RetryConfig] = None,
    exceptions: tuple = (Exception,),
) -> Callable:
    """重试装饰器（支持同步和异步函数，指数退避）

    Args:
        config: 重试配置，如果为 None 则使用默认配置
        exceptions: 需要重试的异常类型元组

    Returns:
        装饰器函数

    Example:
        @retry(RetryConfig(max_attempts=3, initial_delay=1.0))
        async def unstable_api_call():
            # 可能失败的 API 调用（异步）
            ...

        @retry(RetryConfig(max_attempts=3, initial_delay=1.0))
        def unstable_sync_call():
            # 可能失败的 API 调用（同步）
            ...
    """
    if config is None:
        config = RetryConfig()

    def decorator(func: Callable) -> Callable:
        # 检测函数是否为异步函数
        is_async = asyncio.iscoroutinefunction(func)

        if is_async:
            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                last_exception = None

                for attempt in range(1, config.max_attempts + 1):
                    try:
                        return await func(*args, **kwargs)

                    except exceptions as exc:
                        last_exception = exc

                        if attempt >= config.max_attempts:
                            logger.error(
                                f"Retry exhausted for {func.__name__} after {attempt} attempts: {exc}"
                            )
                            raise

                        # 计算延迟时间（指数退避）
                        delay = min(
                            config.initial_delay * (config.exponential_base ** (attempt - 1)),
                            config.max_delay,
                        )

                        # 添加随机抖动（0-50% 的延迟）
                        if config.jitter:
                            import random
                            delay = delay * (0.5 + random.random() * 0.5)

                        logger.warning(
                            f"Retry {attempt}/{config.max_attempts} for {func.__name__} "
                            f"after {delay:.2f}s: {exc}"
                        )

                        await asyncio.sleep(delay)

                # 理论上不会到达这里，但为了类型检查
                raise last_exception if last_exception else Exception("Retry failed")

            return async_wrapper
        else:
            @wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                last_exception = None

                for attempt in range(1, config.max_attempts + 1):
                    try:
                        return func(*args, **kwargs)

                    except exceptions as exc:
                        last_exception = exc

                        if attempt >= config.max_attempts:
                            logger.error(
                                f"Retry exhausted for {func.__name__} after {attempt} attempts: {exc}"
                            )
                            raise

                        # 计算延迟时间（指数退避）
                        delay = min(
                            config.initial_delay * (config.exponential_base ** (attempt - 1)),
                            config.max_delay,
                        )

                        # 添加随机抖动（0-50% 的延迟）
                        if config.jitter:
                            import random
                            delay = delay * (0.5 + random.random() * 0.5)

                        logger.warning(
                            f"Retry {attempt}/{config.max_attempts} for {func.__name__} "
                            f"after {delay:.2f}s: {exc}"
                        )

                        import time
                        time.sleep(delay)

                # 理论上不会到达这里，但为了类型检查
                raise last_exception if last_exception else Exception("Retry failed")

            return sync_wrapper

    return decorator


# ==================== 熔断器 ====================

class CircuitState(str, Enum):
    """熔断器状态"""
    CLOSED = "closed"          # 关闭（正常）
    OPEN = "open"              # 打开（熔断）
    HALF_OPEN = "half_open"    # 半开（探测）


@dataclass
class CircuitBreakerConfig:
    """熔断器配置

    Attributes:
        failure_threshold: 失败阈值（连续失败多少次后熔断）
        success_threshold: 成功阈值（半开状态下成功多少次后恢复）
        timeout: 熔断超时时间（秒），超时后进入半开状态
        expected_exception: 预期的异常类型（只有这些异常才会计入失败）
    """
    failure_threshold: int = 5
    success_threshold: int = 2
    timeout: float = 60.0
    expected_exception: tuple = (Exception,)


class CircuitBreaker:
    """熔断器

    基于 Netflix Hystrix 的熔断器实现，防止级联故障。

    状态转换：
        CLOSED -> OPEN: 失败次数达到阈值
        OPEN -> HALF_OPEN: 熔断超时
        HALF_OPEN -> CLOSED: 成功次数达到阈值
        HALF_OPEN -> OPEN: 任何失败

    使用示例:
        breaker = CircuitBreaker("llm_service", config)

        @breaker.protected
        async def call_llm():
            ...
    """

    # 全局熔断器注册表
    _breakers: Dict[str, CircuitBreaker] = {}
    _lock = RLock()

    def __init__(self, name: str, config: Optional[CircuitBreakerConfig] = None):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[datetime] = None
        self._state_lock = Lock()

        # 注册到全局注册表
        with CircuitBreaker._lock:
            CircuitBreaker._breakers[name] = self

    @classmethod
    def get_or_create(cls, name: str, config: Optional[CircuitBreakerConfig] = None) -> CircuitBreaker:
        """获取或创建熔断器"""
        with cls._lock:
            if name not in cls._breakers:
                cls._breakers[name] = CircuitBreaker(name, config)
            return cls._breakers[name]

    def _should_attempt_reset(self) -> bool:
        """判断是否应该尝试重置（从 OPEN -> HALF_OPEN）"""
        if self.state != CircuitState.OPEN:
            return False

        if self.last_failure_time is None:
            return True

        elapsed = (datetime.now() - self.last_failure_time).total_seconds()
        return elapsed >= self.config.timeout

    def _record_success(self) -> None:
        """记录成功"""
        with self._state_lock:
            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.config.success_threshold:
                    logger.info(f"Circuit breaker '{self.name}' recovered to CLOSED")
                    self.state = CircuitState.CLOSED
                    self.failure_count = 0
                    self.success_count = 0
            elif self.state == CircuitState.CLOSED:
                # 重置失败计数
                self.failure_count = 0

    def _record_failure(self) -> None:
        """记录失败"""
        with self._state_lock:
            self.last_failure_time = datetime.now()

            if self.state == CircuitState.HALF_OPEN:
                # 半开状态下任何失败都会重新打开
                logger.warning(f"Circuit breaker '{self.name}' reopened from HALF_OPEN")
                self.state = CircuitState.OPEN
                self.success_count = 0

            elif self.state == CircuitState.CLOSED:
                self.failure_count += 1
                if self.failure_count >= self.config.failure_threshold:
                    logger.warning(
                        f"Circuit breaker '{self.name}' opened "
                        f"(failures: {self.failure_count}/{self.config.failure_threshold})"
                    )
                    self.state = CircuitState.OPEN

    def protected(self, func: Callable) -> Callable:
        """熔断器保护装饰器

        Example:
            breaker = CircuitBreaker.get_or_create("my_service")

            @breaker.protected
            async def my_function():
                ...
        """
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # 检查是否应该尝试重置
            if self._should_attempt_reset():
                with self._state_lock:
                    logger.info(f"Circuit breaker '{self.name}' entering HALF_OPEN state")
                    self.state = CircuitState.HALF_OPEN
                    self.success_count = 0

            # 如果熔断器打开，快速失败
            if self.state == CircuitState.OPEN:
                raise CircuitBreakerOpenError(
                    f"Circuit breaker '{self.name}' is OPEN, failing fast"
                )

            # 尝试调用
            try:
                result = await func(*args, **kwargs)
                self._record_success()
                return result

            except self.config.expected_exception as exc:
                self._record_failure()
                raise

        return wrapper

    def get_state(self) -> CircuitState:
        """获取当前状态"""
        return self.state

    def reset(self) -> None:
        """手动重置熔断器"""
        with self._state_lock:
            logger.info(f"Circuit breaker '{self.name}' manually reset")
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            self.success_count = 0
            self.last_failure_time = None


class CircuitBreakerOpenError(Exception):
    """熔断器打开异常"""
    pass


# ==================== 降级机制 ====================

def fallback(fallback_func: Optional[Callable] = None, default_value: Any = None) -> Callable:
    """降级装饰器

    当主函数失败时，自动调用降级函数或返回默认值。

    Args:
        fallback_func: 降级函数（可选）
        default_value: 默认返回值（可选）

    Returns:
        装饰器函数

    Example:
        # 使用降级函数
        async def fallback_summary(text, *args, **kwargs):
            return "摘要生成服务暂时不可用"

        @fallback(fallback_func=fallback_summary)
        async def generate_summary(text):
            # 可能失败的摘要生成
            ...

        # 使用默认值
        @fallback(default_value="服务暂时不可用")
        async def get_data():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await func(*args, **kwargs)

            except Exception as exc:
                logger.warning(
                    f"Function {func.__name__} failed, using fallback: {exc}"
                )

                if fallback_func is not None:
                    # 调用降级函数
                    if asyncio.iscoroutinefunction(fallback_func):
                        return await fallback_func(*args, **kwargs)
                    else:
                        return fallback_func(*args, **kwargs)

                elif default_value is not None:
                    # 返回默认值
                    return default_value

                else:
                    # 没有降级策略，重新抛出异常
                    raise

        return wrapper

    return decorator


# ==================== 组合使用示例 ====================

def resilient(
    retry_config: Optional[RetryConfig] = None,
    circuit_breaker_name: Optional[str] = None,
    circuit_config: Optional[CircuitBreakerConfig] = None,
    fallback_func: Optional[Callable] = None,
    default_value: Any = None,
) -> Callable:
    """韧性装饰器（组合重试、熔断、降级）

    这是一个便捷装饰器，组合了重试、熔断和降级三种机制。

    Args:
        retry_config: 重试配置
        circuit_breaker_name: 熔断器名称
        circuit_config: 熔断器配置
        fallback_func: 降级函数
        default_value: 默认值

    Example:
        @resilient(
            retry_config=RetryConfig(max_attempts=3),
            circuit_breaker_name="llm_service",
            fallback_func=lambda *args, **kwargs: "服务暂时不可用"
        )
        async def call_external_service():
            ...
    """
    def decorator(func: Callable) -> Callable:
        # 应用装饰器链：fallback -> circuit_breaker -> retry -> func
        result_func = func

        # 1. 重试（最内层）
        if retry_config is not None:
            result_func = retry(retry_config)(result_func)

        # 2. 熔断器
        if circuit_breaker_name is not None:
            breaker = CircuitBreaker.get_or_create(circuit_breaker_name, circuit_config)
            result_func = breaker.protected(result_func)

        # 3. 降级（最外层）
        if fallback_func is not None or default_value is not None:
            result_func = fallback(fallback_func, default_value)(result_func)

        return result_func

    return decorator
