"""配置管理中心

统一管理各厂商服务的配置，提供验证、查询、热更新等功能。

核心功能：
- 配置注册：为每个服务注册配置 Schema
- 配置验证：自动验证配置的完整性和正确性
- 配置查询：根据服务类型和名称获取配置
- 配置缓存：缓存已验证的配置，避免重复验证
- 热更新：支持运行时重新加载配置（预留）

设计原则：
- 工业级：使用 Pydantic 进行严格的类型验证
- 向后兼容：优先从 settings 读取配置，逐步迁移
- 可扩展：支持未来从数据库、配置中心读取配置
- 渐进式：先搭建骨架，细节功能后续补充
"""

from __future__ import annotations

import logging
from threading import Lock
from typing import Any, Dict, Optional, Type

from pydantic import BaseModel, ValidationError

from app.config import settings

logger = logging.getLogger(__name__)


class ServiceConfig(BaseModel):
    """服务配置基类

    所有服务配置都应该继承此基类，提供统一的配置接口。

    Attributes:
        enabled: 是否启用该服务
        timeout: 超时时间（秒）
        retry_count: 重试次数
    """
    enabled: bool = True
    timeout: int = 30
    retry_count: int = 3

    class Config:
        """Pydantic 配置"""
        # 允许额外字段（向后兼容）
        extra = "allow"
        # 验证赋值
        validate_assignment = True


class ConfigManager:
    """配置管理中心

    采用单例模式管理所有服务配置。提供线程安全的配置注册和查询功能。

    内部结构：
        _schemas: {
            "llm": {
                "doubao": DoubaoConfig,
                "qwen": QwenConfig,
            },
            "asr": {...},
            "storage": {...},
        }

        _configs: {
            "llm": {
                "doubao": DoubaoConfig(api_key="...", ...),
            },
            ...
        }

    使用示例：
        # 1. 注册配置 Schema
        ConfigManager.register_schema("llm", "doubao", DoubaoConfig)

        # 2. 获取配置（自动从 settings 读取并验证）
        config = ConfigManager.get_config("llm", "doubao")
        print(config.api_key)

        # 3. 验证配置
        is_valid = ConfigManager.validate_config("llm", "doubao")
    """

    # 类变量：存储配置 Schema
    # 格式: {service_type: {name: ConfigClass}}
    _schemas: Dict[str, Dict[str, Type[ServiceConfig]]] = {
        "llm": {},
        "asr": {},
        "storage": {},
    }

    # 类变量：缓存已验证的配置实例
    # 格式: {service_type: {name: config_instance}}
    _configs: Dict[str, Dict[str, ServiceConfig]] = {
        "llm": {},
        "asr": {},
        "storage": {},
    }

    # 线程锁：确保注册和加载过程线程安全
    _lock = Lock()

    # 配置字段映射表：{service_type: {name: {field: settings_attr}}}
    # 数据驱动配置加载，避免 if-elif 分支（P2-1 优化）
    _CONFIG_MAPPING: Dict[str, Dict[str, Dict[str, str]]] = {
        "llm": {
            "doubao": {
                "api_key": "DOUBAO_API_KEY",
                "base_url": "DOUBAO_BASE_URL",
                "model": "DOUBAO_MODEL",
                "max_tokens": "DOUBAO_MAX_TOKENS",
            },
            "deepseek": {
                "api_key": "DEEPSEEK_API_KEY",
                "base_url": "DEEPSEEK_BASE_URL",
                "model": "DEEPSEEK_MODEL",
                "max_tokens": "DEEPSEEK_MAX_TOKENS",
            },
            "qwen": {
                "api_key": "QWEN_API_KEY",
                "model": "QWEN_MODEL",
            },
            "moonshot": {
                "api_key": "MOONSHOT_API_KEY",
                "base_url": "MOONSHOT_BASE_URL",
                "model": "MOONSHOT_MODEL",
                "max_tokens": "MOONSHOT_MAX_TOKENS",
            },
        },
        "asr": {
            "tencent": {
                "secret_id": "TENCENT_SECRET_ID",
                "secret_key": "TENCENT_SECRET_KEY",
                "region": "TENCENT_REGION",
                "engine_model_type": "TENCENT_ASR_ENGINE_MODEL_TYPE",
                "channel_num": "TENCENT_ASR_CHANNEL_NUM",
                "res_text_format": "TENCENT_ASR_RES_TEXT_FORMAT",
                "speaker_dia": "TENCENT_ASR_SPEAKER_DIA",
                "speaker_number": "TENCENT_ASR_SPEAKER_NUMBER",
                "poll_interval": "TENCENT_ASR_POLL_INTERVAL",
                "max_wait": "TENCENT_ASR_MAX_WAIT_SECONDS",
            },
            "aliyun": {
                "access_key_id": "ALIYUN_ACCESS_KEY_ID",
                "access_key_secret": "ALIYUN_ACCESS_KEY_SECRET",
            },
        },
        "storage": {
            "cos": {
                "region": "COS_REGION",
                "bucket": "COS_BUCKET",
                "secret_id": "COS_SECRET_ID",  # 回退到 TENCENT_SECRET_ID
                "secret_key": "COS_SECRET_KEY",  # 回退到 TENCENT_SECRET_KEY
                "use_ssl": "COS_USE_SSL",
                "public_read": "COS_PUBLIC_READ",
            },
            "oss": {
                "endpoint": "OSS_ENDPOINT",
                "region": "OSS_REGION",
                "access_key_id": "ALIYUN_ACCESS_KEY_ID",
                "access_key_secret": "ALIYUN_ACCESS_KEY_SECRET",
                "bucket": "OSS_BUCKET",
                "use_ssl": "OSS_USE_SSL",
            },
            "minio": {
                "endpoint": "MINIO_ENDPOINT",
                "access_key": "MINIO_ACCESS_KEY",
                "secret_key": "MINIO_SECRET_KEY",
                "bucket": "MINIO_BUCKET",
                "use_ssl": "MINIO_USE_SSL",
            },
        },
    }

    @classmethod
    def register_schema(
        cls,
        service_type: str,
        name: str,
        config_class: Type[ServiceConfig],
    ) -> None:
        """注册配置 Schema

        Args:
            service_type: 服务类型（"llm", "asr", "storage"）
            name: 服务名称（如 "doubao", "tencent"）
            config_class: 配置类（必须继承 ServiceConfig）

        Raises:
            ValueError: 如果 service_type 不支持或 config_class 不是 ServiceConfig 子类

        Example:
            class DoubaoConfig(ServiceConfig):
                api_key: str
                base_url: str

            ConfigManager.register_schema("llm", "doubao", DoubaoConfig)
        """
        if service_type not in cls._schemas:
            raise ValueError(
                f"Unsupported service_type: {service_type}. "
                f"Supported types: {list(cls._schemas.keys())}"
            )

        if not issubclass(config_class, ServiceConfig):
            raise ValueError(
                f"config_class must be a subclass of ServiceConfig, "
                f"got {config_class}"
            )

        with cls._lock:
            cls._schemas[service_type][name] = config_class
            logger.info(
                f"Registered config schema for {service_type}/{name}: "
                f"{config_class.__name__}"
            )

    @classmethod
    def get_config(
        cls,
        service_type: str,
        name: str,
        reload: bool = False,
    ) -> ServiceConfig:
        """获取服务配置（自动从 settings 加载和验证）

        首次调用时从 settings 读取配置并验证，后续调用返回缓存的配置。

        Args:
            service_type: 服务类型（"llm", "asr", "storage"）
            name: 服务名称（如 "doubao", "tencent"）
            reload: 是否强制重新加载配置（默认 False）

        Returns:
            配置实例

        Raises:
            ValueError: 如果服务类型不支持或未注册配置 Schema
            ValidationError: 如果配置验证失败

        Example:
            config = ConfigManager.get_config("llm", "doubao")
            print(config.api_key)  # 访问配置字段
        """
        if service_type not in cls._schemas:
            raise ValueError(f"Unsupported service_type: {service_type}")

        if name not in cls._schemas[service_type]:
            available = list(cls._schemas[service_type].keys())
            raise ValueError(
                f"No config schema registered for {service_type}/{name}. "
                f"Available: {available}"
            )

        with cls._lock:
            # 如果强制重新加载或没有缓存，则加载配置
            if reload or name not in cls._configs[service_type]:
                config_class = cls._schemas[service_type][name]

                # 从配置加载器加载配置
                config_data = cls._load_config_from_settings(service_type, name)

                # 验证并创建配置实例
                try:
                    config_instance = config_class(**config_data)
                    cls._configs[service_type][name] = config_instance
                    logger.debug(
                        f"Loaded and validated config for {service_type}/{name}"
                    )
                except ValidationError as exc:
                    logger.error(
                        f"Config validation failed for {service_type}/{name}: {exc}",
                        exc_info=True,
                    )
                    raise

            return cls._configs[service_type][name]

    @classmethod
    def validate_config(cls, service_type: str, name: str) -> bool:
        """验证配置是否完整且正确

        Args:
            service_type: 服务类型
            name: 服务名称

        Returns:
            True 如果配置有效，否则 False
        """
        try:
            cls.get_config(service_type, name, reload=True)
            return True
        except (ValueError, ValidationError) as exc:
            logger.warning(
                f"Config validation failed for {service_type}/{name}: {exc}"
            )
            return False

    @classmethod
    def is_schema_registered(cls, service_type: str, name: str) -> bool:
        """检查是否已注册配置 Schema

        Args:
            service_type: 服务类型
            name: 服务名称

        Returns:
            True 如果已注册，否则 False
        """
        return (
            service_type in cls._schemas
            and name in cls._schemas[service_type]
        )

    @classmethod
    def list_schemas(cls, service_type: str) -> list[str]:
        """列出指定类型的所有已注册配置 Schema

        Args:
            service_type: 服务类型

        Returns:
            配置名称列表

        Raises:
            ValueError: 如果服务类型不支持
        """
        if service_type not in cls._schemas:
            raise ValueError(f"Unsupported service_type: {service_type}")

        return list(cls._schemas[service_type].keys())

    @classmethod
    def clear(cls, service_type: Optional[str] = None) -> None:
        """清空配置缓存（主要用于测试）

        Args:
            service_type: 如果指定，只清空该类型的配置；否则清空所有
        """
        with cls._lock:
            if service_type:
                if service_type in cls._configs:
                    cls._configs[service_type].clear()
                    logger.info(f"Cleared all {service_type} configs")
            else:
                for svc_type in cls._configs:
                    cls._configs[svc_type].clear()
                logger.info("Cleared all configs")

    @classmethod
    def _load_config_from_settings(
        cls,
        service_type: str,
        name: str,
    ) -> Dict[str, Any]:
        """从 settings 加载配置（向后兼容，数据驱动）

        根据服务类型和名称，从 app.config.settings 读取对应的配置项。
        使用 _CONFIG_MAPPING 表驱动配置加载，避免 if-elif 分支（P2-1 优化）。

        Args:
            service_type: 服务类型
            name: 服务名称

        Returns:
            配置字典

        Note:
            这是一个过渡方法。未来可以扩展为从数据库、配置中心等读取配置。
        """
        # 检查服务类型是否在映射表中
        if service_type not in cls._CONFIG_MAPPING:
            logger.warning(f"Unknown service_type: {service_type}")
            return {}

        # 检查服务名称是否在映射表中
        if name not in cls._CONFIG_MAPPING[service_type]:
            logger.debug(
                f"No config mapping for {service_type}/{name}, "
                f"available: {list(cls._CONFIG_MAPPING[service_type].keys())}"
            )
            return {}

        # 获取字段映射表
        field_mapping = cls._CONFIG_MAPPING[service_type][name]
        config_data: Dict[str, Any] = {}

        # 数据驱动加载：遍历映射表，从 settings 读取对应的值
        for field_name, settings_attr in field_mapping.items():
            value = getattr(settings, settings_attr, None)

            # 特殊处理：COS 的 secret_id/secret_key 回退到 TENCENT_*
            if value is None and service_type == "storage" and name == "cos":
                if field_name == "secret_id":
                    value = getattr(settings, "TENCENT_SECRET_ID", None)
                elif field_name == "secret_key":
                    value = getattr(settings, "TENCENT_SECRET_KEY", None)

            # 只添加非 None 的值
            if value is not None:
                config_data[field_name] = value

        logger.debug(
            f"Loaded config for {service_type}/{name}: "
            f"{list(config_data.keys())} fields"
        )

        return config_data


def register_config_schema(
    service_type: str,
    name: str,
) -> Any:
    """配置 Schema 注册装饰器

    自动将配置类注册到 ConfigManager，简化注册流程。

    Args:
        service_type: 服务类型（"llm", "asr", "storage"）
        name: 服务名称（如 "doubao", "tencent"）

    Returns:
        装饰器函数

    Example:
        @register_config_schema("llm", "doubao")
        class DoubaoConfig(ServiceConfig):
            api_key: str
            base_url: str
            model: str
    """
    def decorator(cls: Type[ServiceConfig]) -> Type[ServiceConfig]:
        ConfigManager.register_schema(service_type, name, cls)
        return cls

    return decorator
