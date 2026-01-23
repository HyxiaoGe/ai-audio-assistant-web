"""ASR 用户配额模型

用于限制用户的 ASR 使用量，与平台定价无关。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseRecord


class AsrUserQuota(BaseRecord):
    """ASR 用户配额限制

    用于限制用户在某个时间窗口内的 ASR 使用量。
    例如：用户每月最多使用 1 小时的 ASR 服务。

    注意：这是用户配额限制，与平台定价（AsrPricingConfig）是独立的概念。
    """

    __tablename__ = "asr_user_quotas"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "variant",
            "window_type",
            "window_start",
            "owner_user_id",
            name="uk_asr_user_quotas_provider_window",
        ),
    )

    # 用户 ID（NULL 表示全局配额）
    owner_user_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)

    # 服务商和变体
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    variant: Mapped[str] = mapped_column(String(30), nullable=False, server_default=text("'file'"))

    # 时间窗口
    window_type: Mapped[str] = mapped_column(String(10), nullable=False)  # day | month | total
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # 配额和使用量
    quota_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    used_seconds: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0"))

    # 状态
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'active'"))
