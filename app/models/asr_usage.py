from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseRecord


class ASRUsage(BaseRecord):
    """ASR 调用详情记录表

    记录每次 ASR 调用的完整信息，用于：
    1. 详细用量统计和分析
    2. 与第三方账单对账
    3. 异常消耗检测
    4. 成本归因和分摊
    """

    __tablename__ = "asr_usages"
    __table_args__ = (
        Index("idx_asr_usages_user", "user_id"),
        Index("idx_asr_usages_task", "task_id"),
        Index("idx_asr_usages_provider", "provider"),
        Index("idx_asr_usages_created_at", "created_at"),
        Index("idx_asr_usages_user_provider_created", "user_id", "provider", "created_at"),
        Index("idx_asr_usages_billing", "provider", "external_task_id"),
    )

    # 关联字段
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )

    # ASR 提供商信息
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    variant: Mapped[str] = mapped_column(String(30), nullable=False, server_default=text("'file'"))

    # 外部任务追踪
    external_task_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # 用量信息
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    estimated_cost: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0"))
    actual_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # 请求信息
    audio_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    audio_format: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # 状态
    status: Mapped[str] = mapped_column(String(20), server_default=text("'success'"))
    error_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # 性能指标
    processing_time_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # 扩展字段
    request_params: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    response_metadata: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )

    # 免费额度分拆
    free_quota_consumed: Mapped[float] = mapped_column(
        Float, nullable=False, default=0, server_default=text("0")
    )
    paid_duration_seconds: Mapped[float] = mapped_column(
        Float, nullable=False, default=0, server_default=text("0")
    )
    actual_paid_cost: Mapped[float] = mapped_column(
        Float, nullable=False, default=0, server_default=text("0")
    )

    # 对账标记
    reconciled: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    reconciled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
