"""ASR 定价配置模型

存储各 ASR 平台的定价信息，支持动态修改。
"""

from __future__ import annotations

from sqlalchemy import Boolean, Float, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseRecord


class AsrPricingConfig(BaseRecord):
    """ASR 定价配置

    存储各 ASR 平台的定价信息：
    - 单价（元/小时）
    - 平台免费额度
    - 额度刷新周期
    """

    __tablename__ = "asr_pricing_configs"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "variant",
            name="uk_asr_pricing_configs_provider_variant",
        ),
    )

    # 平台标识
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    variant: Mapped[str] = mapped_column(String(30), nullable=False)

    # 定价信息
    cost_per_hour: Mapped[float] = mapped_column(Float, nullable=False)

    # 平台免费额度
    free_quota_seconds: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0")
    )
    reset_period: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'none'")
    )  # none, monthly, yearly

    # 状态
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    # 提供商能力（用于智能调度）
    quality_score: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0.8")
    )  # 识别质量评分 0-1
    supports_diarization: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )  # 支持说话人分离
    supports_word_level: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )  # 支持词级时间戳（秒级输出）
