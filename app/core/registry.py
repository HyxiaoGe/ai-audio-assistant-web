"""服务注册中心

提供服务自动注册、发现和管理功能，支持多厂商服务的统一管理。

核心功能：
- 服务注册：通过装饰器自动注册服务实现
- 服务发现：根据类型和名称获取服务实例
- 服务元数据：管理服务的优先级、成本、限流等信息
- 单例管理：确保每个服务只有一个实例

设计原则：
- 工业级：线程安全、类型安全、异常处理完善
- 可扩展：支持未来添加健康检查、负载均衡等功能
- 向后兼容：不影响现有工厂函数
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable, Dict, List, Optional, Type

logger = logging.getLogger(__name__)


@dataclass
class ServiceMetadata:
    """服务元数据

    记录服务的配置信息，用于后续的健康检查、负载均衡、成本优化等功能。

    Attributes:
        name: 服务名称（如 "doubao", "tencent", "minio"）
        service_type: 服务类型（"llm", "asr", "storage"）
        provider: 厂商标识（通常与 name 相同）
        priority: 优先级（数值越小优先级越高，默认 100）
        cost_per_request: 单次请求成本（元），用于成本优化
        rate_limit: 速率限制（请求/分钟），0 表示无限制
        timeout: 超时时间（秒）
        retry_count: 重试次数
        enabled: 是否启用（预留字段，用于动态禁用服务）
        description: 服务描述
        display_name: 用户友好的显示名称（如 "豆包 (Doubao)"、"DeepSeek Chat"）
        cost_per_million_tokens: 每百万 token 的成本（元），用于前端显示
    """

    name: str
    service_type: str
    provider: str = ""
    priority: int = 100
    cost_per_request: float = 0.0
    rate_limit: int = 0
    timeout: int = 30
    retry_count: int = 3
    enabled: bool = True
    description: str = ""
    display_name: str = ""
    cost_per_million_tokens: float = 0.0

    def __post_init__(self) -> None:
        """初始化后处理：如果未设置 provider，默认使用 name"""
        if not self.provider:
            self.provider = self.name
        if not self.display_name:
            self.display_name = self.name.capitalize()


class ServiceRegistry:
    """服务注册中心

    采用单例模式管理所有已注册的服务。提供线程安全的注册和获取功能。

    内部结构：
        _services: {
            "llm": {
                "doubao": (DoubaoLLMService, metadata, instance),
                "qwen": (QwenLLMService, metadata, instance),
            },
            "asr": {...},
            "storage": {...},
        }

    使用示例：
        # 注册服务
        ServiceRegistry.register("llm", "doubao", DoubaoLLMService, metadata)

        # 获取服务实例
        llm = ServiceRegistry.get("llm", "doubao")

        # 列出所有 LLM 服务
        llm_names = ServiceRegistry.list_services("llm")
    """

    # 类变量：存储所有已注册的服务
    # 格式: {service_type: {name: (service_class, metadata, instance)}}
    _services: Dict[str, Dict[str, tuple[Type[Any], ServiceMetadata, Optional[Any]]]] = {
        "llm": {},
        "asr": {},
        "storage": {},
    }

    # 线程锁：确保注册和实例化过程线程安全
    _lock = Lock()

    @classmethod
    def register(
        cls,
        service_type: str,
        name: str,
        service_class: Type[Any],
        metadata: Optional[ServiceMetadata] = None,
    ) -> None:
        """注册服务

        Args:
            service_type: 服务类型（"llm", "asr", "storage"）
            name: 服务名称（如 "doubao", "tencent"）
            service_class: 服务实现类
            metadata: 服务元数据，如果为 None 则自动创建默认元数据

        Raises:
            ValueError: 如果 service_type 不支持

        Example:
            ServiceRegistry.register(
                "llm",
                "doubao",
                DoubaoLLMService,
                ServiceMetadata(name="doubao", service_type="llm")
            )
        """
        if service_type not in cls._services:
            raise ValueError(
                f"Unsupported service_type: {service_type}. "
                f"Supported types: {list(cls._services.keys())}"
            )

        # 如果没有提供元数据，创建默认元数据
        if metadata is None:
            metadata = ServiceMetadata(name=name, service_type=service_type)

        with cls._lock:
            # 存储服务类、元数据和实例占位符（None）
            cls._services[service_type][name] = (service_class, metadata, None)
            logger.info(
                f"Registered {service_type} service: {name} "
                f"(class={service_class.__name__}, priority={metadata.priority})"
            )

    @classmethod
    def get(
        cls,
        service_type: str,
        name: str,
        force_new: bool = False,
        model_id: Optional[str] = None,
        config: Optional[Any] = None,
    ) -> Any:
        """获取服务实例（单例模式）

        首次调用时创建实例并缓存，后续调用返回缓存的实例。

        Args:
            service_type: 服务类型（"llm", "asr", "storage"）
            name: 服务名称（如 "doubao", "tencent"）
            force_new: 是否强制创建新实例（默认 False，使用单例）
            model_id: 模型ID（用于支持同一 provider 下多个模型的场景）
            config: 可选配置（用于用户级别配置覆盖）

        Returns:
            服务实例

        Raises:
            ValueError: 如果服务类型不支持或服务未注册
            RuntimeError: 如果服务实例化失败

        Example:
            llm = ServiceRegistry.get("llm", "doubao")
            llm = ServiceRegistry.get("llm", "openrouter", model_id="openai/gpt-4o")
        """
        if service_type not in cls._services:
            raise ValueError(f"Unsupported service_type: {service_type}")

        if name not in cls._services[service_type]:
            available = list(cls._services[service_type].keys())
            raise ValueError(
                f"Service '{name}' not registered for type '{service_type}'. "
                f"Available services: {available}"
            )

        with cls._lock:
            service_class, metadata, cached_instance = cls._services[service_type][name]

            # 如果提供了 model_id 或 config，总是创建新实例（不使用缓存）
            # 因为不同的 model_id 或配置需要不同实例
            if model_id or config is not None:
                force_new = True

            # 如果强制创建新实例或没有缓存实例，则创建
            if force_new or cached_instance is None:
                try:
                    # 尝试传入 model_id 参数（如果服务支持的话）
                    import inspect

                    sig = inspect.signature(service_class.__init__)
                    kwargs: dict[str, Any] = {}
                    if "model_id" in sig.parameters and model_id:
                        kwargs["model_id"] = model_id
                    if "config" in sig.parameters and config is not None:
                        kwargs["config"] = config
                    instance = service_class(**kwargs)

                    logger.debug(
                        f"Created new {service_type} service instance: {name}"
                        + (f" with model_id={model_id}" if model_id else "")
                    )

                    # 如果不是强制创建，则缓存实例
                    if not force_new:
                        cls._services[service_type][name] = (service_class, metadata, instance)

                    return instance
                except Exception as exc:
                    logger.error(
                        f"Failed to instantiate {service_type} service '{name}': {exc}",
                        exc_info=True,
                    )
                    raise RuntimeError(
                        f"Failed to instantiate {service_type} service '{name}': {exc}"
                    ) from exc

            return cached_instance

    @classmethod
    def list_services(cls, service_type: str) -> List[str]:
        """列出指定类型的所有已注册服务名称

        Args:
            service_type: 服务类型（"llm", "asr", "storage"）

        Returns:
            服务名称列表

        Raises:
            ValueError: 如果服务类型不支持

        Example:
            llm_services = ServiceRegistry.list_services("llm")
            # 返回: ["doubao", "qwen"]
        """
        if service_type not in cls._services:
            raise ValueError(f"Unsupported service_type: {service_type}")

        return list(cls._services[service_type].keys())

    @classmethod
    def get_metadata(cls, service_type: str, name: str) -> ServiceMetadata:
        """获取服务元数据

        Args:
            service_type: 服务类型
            name: 服务名称

        Returns:
            服务元数据

        Raises:
            ValueError: 如果服务未注册
        """
        if service_type not in cls._services:
            raise ValueError(f"Unsupported service_type: {service_type}")

        if name not in cls._services[service_type]:
            raise ValueError(f"Service '{name}' not registered for type '{service_type}'")

        _, metadata, _ = cls._services[service_type][name]
        return metadata

    @classmethod
    def is_registered(cls, service_type: str, name: str) -> bool:
        """检查服务是否已注册

        Args:
            service_type: 服务类型
            name: 服务名称

        Returns:
            True 如果已注册，否则 False
        """
        return service_type in cls._services and name in cls._services[service_type]

    @classmethod
    def clear(cls, service_type: Optional[str] = None) -> None:
        """清空注册表（主要用于测试）

        Args:
            service_type: 如果指定，只清空该类型的服务；否则清空所有
        """
        with cls._lock:
            if service_type:
                if service_type in cls._services:
                    cls._services[service_type].clear()
                    logger.info(f"Cleared all {service_type} services")
            else:
                for svc_type in cls._services:
                    cls._services[svc_type].clear()
                logger.info("Cleared all services")


def register_service(
    service_type: str,
    name: str,
    metadata: Optional[ServiceMetadata] = None,
) -> Callable[[Type[Any]], Type[Any]]:
    """服务注册装饰器

    自动将服务类注册到 ServiceRegistry，简化注册流程。

    Args:
        service_type: 服务类型（"llm", "asr", "storage"）
        name: 服务名称（如 "doubao", "tencent"）
        metadata: 服务元数据（可选）

    Returns:
        装饰器函数

    Example:
        @register_service("llm", "doubao")
        class DoubaoLLMService(LLMService):
            ...

        # 使用自定义元数据
        @register_service(
            "llm",
            "doubao",
            metadata=ServiceMetadata(
                name="doubao",
                service_type="llm",
                priority=10,
                cost_per_request=0.001,
            )
        )
        class DoubaoLLMService(LLMService):
            ...
    """

    def decorator(cls: Type[Any]) -> Type[Any]:
        # 注册服务
        ServiceRegistry.register(service_type, name, cls, metadata)
        return cls

    return decorator
