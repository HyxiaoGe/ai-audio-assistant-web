"""任务处理阶段定义和状态机"""

from enum import Enum


class StageType(str, Enum):
    """阶段类型枚举"""

    RESOLVE_YOUTUBE = "resolve_youtube"  # YouTube 信息解析
    DOWNLOAD = "download"  # 下载
    TRANSCODE = "transcode"  # 转码
    UPLOAD_STORAGE = "upload_storage"  # 上传存储
    TRANSCRIBE = "transcribe"  # ASR 转写
    SUMMARIZE = "summarize"  # LLM 摘要


class StageStatus(str, Enum):
    """阶段状态枚举"""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"  # 跳过（复用之前结果）


class RetryMode(str, Enum):
    """重试模式枚举"""

    FULL = "full"  # 完整重试（清空所有阶段，从头开始）
    AUTO = "auto"  # 智能重试（自动从失败的阶段继续）
    FROM_TRANSCRIBE = "from_transcribe"  # 从转写开始（复用下载/上传）
    TRANSCRIBE_ONLY = "transcribe_only"  # 仅重新转写
    SUMMARIZE_ONLY = "summarize_only"  # 仅重新生成摘要


# YouTube 任务的标准处理流程
YOUTUBE_STAGE_FLOW = [
    StageType.RESOLVE_YOUTUBE,
    StageType.DOWNLOAD,
    StageType.TRANSCODE,
    StageType.UPLOAD_STORAGE,
    StageType.TRANSCRIBE,
    StageType.SUMMARIZE,
]

# 音频文件上传任务的标准处理流程
AUDIO_STAGE_FLOW = [
    StageType.UPLOAD_STORAGE,
    StageType.TRANSCRIBE,
    StageType.SUMMARIZE,
]


# 重试模式到起始阶段的映射
RETRY_MODE_START_STAGE: dict[RetryMode, StageType | None] = {
    RetryMode.FULL: None,  # None 表示从第一个阶段开始
    RetryMode.AUTO: None,  # 由系统自动判断
    RetryMode.FROM_TRANSCRIBE: StageType.TRANSCRIBE,
    RetryMode.TRANSCRIBE_ONLY: StageType.TRANSCRIBE,
    RetryMode.SUMMARIZE_ONLY: StageType.SUMMARIZE,
}

# 重试模式需要清空的阶段
RETRY_MODE_CLEAR_STAGES: dict[RetryMode, str | list[StageType]] = {
    RetryMode.FULL: "all",  # 清空所有阶段
    RetryMode.AUTO: "from_failed",  # 清空失败阶段及其后续阶段
    RetryMode.FROM_TRANSCRIBE: [StageType.TRANSCRIBE, StageType.SUMMARIZE],
    RetryMode.TRANSCRIBE_ONLY: [StageType.TRANSCRIBE, StageType.SUMMARIZE],
    RetryMode.SUMMARIZE_ONLY: [StageType.SUMMARIZE],
}


def get_stage_flow(source_type: str) -> list[StageType]:
    """根据任务类型获取处理流程"""
    if source_type == "youtube":
        return YOUTUBE_STAGE_FLOW
    else:
        return AUDIO_STAGE_FLOW
