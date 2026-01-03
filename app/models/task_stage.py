"""任务处理阶段模型"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.task import Task


class TaskStage(BaseModel):
    """任务处理阶段记录

    追踪任务处理的各个阶段（下载、上传、转写、摘要等）的执行状态。
    支持阶段级别的重试和错误追踪。
    """

    __tablename__ = "task_stages"
    __table_args__ = (
        # 确保每个任务的每个阶段类型只有一条 active 记录
        Index(
            "idx_task_stages_unique_active",
            "task_id",
            "stage_type",
            unique=True,
            postgresql_where=text("is_active = true"),
        ),
        Index("idx_task_stages_task", "task_id"),
        Index("idx_task_stages_status", "status"),
    )

    # 关联的任务
    task_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )

    # 阶段类型
    stage_type: Mapped[str] = mapped_column(String(50), nullable=False)
    """
    阶段类型：
    - 'resolve_youtube': 解析 YouTube 信息
    - 'download': 下载音视频
    - 'transcode': 转码为 WAV
    - 'upload_storage': 上传到存储（COS + MinIO）
    - 'transcribe': ASR 转写
    - 'summarize': LLM 摘要
    """

    # 阶段状态
    status: Mapped[str] = mapped_column(
        String(20), server_default=text("'pending'"), nullable=False
    )
    """
    - 'pending': 等待执行
    - 'processing': 执行中
    - 'completed': 已完成
    - 'failed': 失败
    - 'skipped': 跳过（复用之前的结果）
    """

    # 是否为当前活跃记录（支持重试历史）
    is_active: Mapped[bool] = mapped_column(
        server_default=text("true"), nullable=False
    )

    # 执行时间
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # 错误信息
    error_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 重试次数（这个阶段重试了几次）
    attempt: Mapped[int] = mapped_column(
        Integer, server_default=text("1"), nullable=False
    )

    # 阶段特定的元数据
    stage_metadata: Mapped[dict[str, object]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    """
    存储阶段特定的元数据，例如：
    - download: {"file_size": 1024000, "format": "mp4"}
    - upload_storage: {"cos_key": "...", "minio_key": "..."}
    - transcribe: {"word_count": 1500, "duration_seconds": 300}
    - summarize: {"model": "doubao-1.5-pro", "tokens": 2000}
    """

    # Relationship
    task: Mapped["Task"] = relationship("Task", back_populates="stages")
