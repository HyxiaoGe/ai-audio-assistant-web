from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional


@dataclass(frozen=True)
class TranscriptSegment:
    """转写结果片段

    Attributes:
        speaker_id: 说话人ID（如 "speaker_0"）
        start_time: 开始时间（秒）
        end_time: 结束时间（秒）
        content: 转写文本内容
        confidence: 置信度（0.0-1.0），None 表示未提供
    """

    speaker_id: Optional[str]
    start_time: float
    end_time: float
    content: str
    confidence: Optional[float]


class ASRService(ABC):
    """ASR 服务抽象基类

    定义了语音转写服务的标准接口，所有 ASR 厂商实现都必须遵循此接口。
    """

    @property
    @abstractmethod
    def provider(self) -> str:
        """厂商名称

        Returns:
            厂商标识，如 "tencent", "aliyun", "aws"
        """
        raise NotImplementedError

    @abstractmethod
    async def transcribe(
        self,
        audio_url: str,
        status_callback: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> List[TranscriptSegment]:
        """转写音频（单个文件）

        Args:
            audio_url: 音频文件 URL（需要厂商可访问）
            status_callback: 状态回调函数（可选）
                - "asr_submitting": 提交任务中
                - "asr_polling": 轮询结果中
                - "asr_completed": 转写完成

        Returns:
            转写结果列表，按时间顺序排序

        Raises:
            BusinessError: 业务错误（文件格式不支持、配额不足等）
            Exception: 其他异常
        """
        raise NotImplementedError

    @abstractmethod
    async def get_task_status(self, task_id: str) -> str:
        """查询任务状态

        Args:
            task_id: 任务 ID

        Returns:
            任务状态：
            - "pending": 等待中
            - "processing": 处理中
            - "completed": 已完成
            - "failed": 失败

        Raises:
            NotImplementedError: 暂未实现（骨架方法）
        """
        raise NotImplementedError("查询任务状态功能待实现")

    @abstractmethod
    async def cancel_task(self, task_id: str) -> bool:
        """取消任务

        Args:
            task_id: 任务 ID

        Returns:
            True: 取消成功
            False: 取消失败（任务已完成或不存在）

        Raises:
            NotImplementedError: 暂未实现（骨架方法）
        """
        raise NotImplementedError("取消任务功能待实现")

    @abstractmethod
    async def batch_transcribe(
        self,
        audio_urls: List[str],
        status_callback: Optional[Callable[[str, int, int], Awaitable[None]]] = None,
    ) -> List[List[TranscriptSegment]]:
        """批量转写音频

        Args:
            audio_urls: 音频文件 URL 列表
            status_callback: 批量状态回调（可选）
                参数：(status, completed_count, total_count)

        Returns:
            转写结果列表，每个元素对应一个音频文件的结果

        Raises:
            NotImplementedError: 暂未实现（骨架方法）
        """
        raise NotImplementedError("批量转写功能待实现")

    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查

        检查服务是否可用（如 API 密钥是否有效、配额是否充足等）

        Returns:
            True: 服务健康
            False: 服务不可用

        Raises:
            NotImplementedError: 暂未实现（骨架方法）
        """
        raise NotImplementedError("健康检查功能待实现")

    @abstractmethod
    def estimate_cost(self, duration_seconds: int) -> float:
        """估算成本

        Args:
            duration_seconds: 音频时长（秒）

        Returns:
            预估成本（单位：元）

        Raises:
            NotImplementedError: 暂未实现（骨架方法）
        """
        raise NotImplementedError("成本估算功能待实现")
