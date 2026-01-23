"""ASR 用量周期统计表

追踪每个周期的用量，区分免费和付费用量。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseRecord


class AsrUsagePeriod(BaseRecord):
    """ASR 用量周期统计表

    记录每个周期的累计用量，用于：
    1. 跟踪免费额度消耗
    2. 计算付费用量
    3. 周期性统计和报表
    """

    __tablename__ = "asr_usage_periods"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "provider",
            "variant",
            "period_type",
            "period_start",
            name="uk_asr_usage_periods_unique",
        ),
    )

    # 所属用户（NULL 表示全局统计）
    owner_user_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)

    # ASR 提供商信息
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    variant: Mapped[str] = mapped_column(String(30), nullable=False)

    # 周期信息
    period_type: Mapped[str] = mapped_column(String(10), nullable=False)  # month | year | total
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # 用量统计
    used_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    free_quota_used: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    paid_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    total_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0)
