from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


def redact_audio_url(url: str) -> str:
    """剥离（可能是预签名的）URL 的 query 与 userinfo，便于安全地写日志。

    预签名下载 URL 把签名/凭证/有效期放在 query（q-signature / X-Amz-Signature /
    X-Amz-Credential）；原样打日志等于把一条有时效的可下载链接交给任何能看日志的人。
    本函数返回 scheme://host[:port]/path，去掉 query、fragment 以及 user:pass@ userinfo。
    不像 URL 的不透明串（对象 key、本地路径）原样返回。
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<redacted-url>"
    if not parts.scheme and not parts.netloc:
        return url  # 非 URL（对象 key / 本地路径），无可剥离
    host_port = parts.netloc.rsplit("@", 1)[-1]  # 去掉 user:pass@，保留 [ipv6]:port
    return urlunsplit((parts.scheme, host_port, parts.path, "", ""))


@dataclass(frozen=True)
class WordTimestamp:
    """词级时间戳"""

    word: str
    start_time: float
    end_time: float
    confidence: float | None = None


@dataclass(frozen=True)
class TranscriptSegment:
    """转写结果片段

    Attributes:
        speaker_id: 说话人ID（如 "speaker_0"）
        start_time: 开始时间（秒）
        end_time: 结束时间（秒）
        content: 转写文本内容
        confidence: 置信度（0.0-1.0），None 表示未提供
        words: 词级时间戳（可选）
    """

    speaker_id: str | None
    start_time: float
    end_time: float
    content: str
    confidence: float | None
    words: list[WordTimestamp] | None = None


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
        status_callback: Callable[[str], Awaitable[None]] | None = None,
    ) -> list[TranscriptSegment]:
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
        audio_urls: list[str],
        status_callback: Callable[[str, int, int], Awaitable[None]] | None = None,
    ) -> list[list[TranscriptSegment]]:
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
    def estimate_cost(self, duration_seconds: int, variant: str = "file") -> float:
        """估算成本

        Args:
            duration_seconds: 音频时长（秒）
            variant: 服务变体（file=标准版, file_fast=极速版），不同变体单价不同

        Returns:
            预估成本（单位：元）

        Raises:
            NotImplementedError: 暂未实现（骨架方法）
        """
        raise NotImplementedError("成本估算功能待实现")

    def engine_for_variant(self, asr_variant: str | None = None) -> str | None:
        """该 provider 在指定变体下实际使用的引擎/模型标识（如 tencent 的 16k_zh）。

        用于 Task 级溯源：记录这次转写背后的具体引擎，而不仅是 provider 名。基类默认返回
        None —— 没有「引擎/模型」概念（或未配置）的 provider 不必覆写，溯源列留 NULL，
        前端遇 NULL 不显示徽章。具备引擎概念的 provider 覆写本方法，按 variant 返回真实引擎。

        Args:
            asr_variant: 服务变体（file=标准版, file_fast=极速版）

        Returns:
            引擎/模型标识，未知或不适用时返回 None
        """
        return None
