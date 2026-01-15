from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, List


class LLMService(ABC):
    """LLM 服务抽象基类

    定义了 LLM 服务的标准接口，所有 LLM 厂商实现都必须遵循此接口。
    """

    @property
    @abstractmethod
    def model_name(self) -> str:
        """模型名称

        Returns:
            模型的完整名称，如 "doubao-1.5-pro-32k"
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def provider(self) -> str:
        """厂商名称

        Returns:
            厂商标识，如 "doubao", "qwen", "openai"
        """
        raise NotImplementedError

    @abstractmethod
    async def summarize(self, text: str, summary_type: str, content_style: str = "meeting") -> str:
        """生成摘要（非流式）

        Args:
            text: 转写文本
            summary_type: 摘要类型 (overview/key_points/action_items)
            content_style: 内容风格 (meeting/lecture/podcast/video/general)

        Returns:
            生成的摘要文本

        Raises:
            BusinessError: 业务错误（参数错误、配额不足等）
            Exception: 其他异常
        """
        raise NotImplementedError

    @abstractmethod
    async def summarize_stream(
        self, text: str, summary_type: str, content_style: str = "meeting"
    ) -> AsyncIterator[str]:
        """流式生成摘要

        Args:
            text: 转写文本
            summary_type: 摘要类型 (overview/key_points/action_items)
            content_style: 内容风格 (meeting/lecture/podcast/video/general)

        Yields:
            摘要文本片段

        Raises:
            BusinessError: 业务错误
            Exception: 其他异常
        """
        raise NotImplementedError
        yield  # Make it a generator

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system_message: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> str:
        """通用文本生成（非流式）

        用于章节划分、自定义prompt等需要灵活生成的场景

        Args:
            prompt: 用户提示词
            system_message: 系统消息（可选）
            temperature: 温度参数（可选，使用服务默认值）
            max_tokens: 最大token数（可选，使用服务默认值）
            **kwargs: 其他参数

        Returns:
            生成的文本

        Raises:
            BusinessError: 业务错误
            Exception: 其他异常
        """
        raise NotImplementedError

    @abstractmethod
    async def chat(self, messages: List[Dict[str, str]], **kwargs: Any) -> str:
        """通用对话接口（非流式）

        Args:
            messages: 对话消息列表，格式：[{"role": "user", "content": "..."}]
            **kwargs: 额外参数（temperature, max_tokens 等）

        Returns:
            AI 回复文本

        Raises:
            NotImplementedError: 暂未实现（骨架方法）
        """
        raise NotImplementedError("通用对话功能待实现")

    @abstractmethod
    async def chat_stream(
        self, messages: List[Dict[str, str]], **kwargs: Any
    ) -> AsyncIterator[str]:
        """通用对话接口（流式）

        Args:
            messages: 对话消息列表
            **kwargs: 额外参数

        Yields:
            AI 回复文本片段

        Raises:
            NotImplementedError: 暂未实现（骨架方法）
        """
        raise NotImplementedError("流式对话功能待实现")
        yield

    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查

        检查服务是否可用（如 API key 是否有效、网络是否连通等）

        Returns:
            True: 服务健康
            False: 服务不可用

        Raises:
            NotImplementedError: 暂未实现（骨架方法）
        """
        raise NotImplementedError("健康检查功能待实现")

    @abstractmethod
    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """估算成本

        Args:
            input_tokens: 输入 token 数量
            output_tokens: 输出 token 数量

        Returns:
            预估成本（单位：元）

        Raises:
            NotImplementedError: 暂未实现（骨架方法）
        """
        raise NotImplementedError("成本估算功能待实现")
