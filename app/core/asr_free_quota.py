"""ASR 免费额度工具函数

提供与免费额度周期计算相关的工具函数。

注意：定价配置（包括免费额度）现在存储在数据库 asr_pricing_configs 表中，
不再硬编码在代码中。请使用 app.services.asr_pricing_service 获取配置。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class QuotaResetPeriod(str, Enum):
    """额度刷新周期"""

    MONTHLY = "monthly"  # 每月刷新
    YEARLY = "yearly"  # 每年刷新
    NONE = "none"  # 不刷新（无免费额度）


def get_current_period_bounds(
    reset_period: str,
    now: Optional[datetime] = None,
) -> tuple[datetime, datetime]:
    """计算当前周期的开始和结束时间

    Args:
        reset_period: 刷新周期字符串 ('monthly', 'yearly', 'none')
        now: 当前时间（默认 UTC 时间）

    Returns:
        (period_start, period_end) 元组
    """
    now = now or datetime.now(timezone.utc)

    if reset_period == QuotaResetPeriod.MONTHLY or reset_period == "monthly":
        # 月度周期：当月1日 00:00:00 到 下月1日 00:00:00
        period_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        if now.month == 12:
            period_end = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            period_end = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    elif reset_period == QuotaResetPeriod.YEARLY or reset_period == "yearly":
        # 年度周期：当年1月1日 00:00:00 到 下年1月1日 00:00:00
        period_start = datetime(now.year, 1, 1, tzinfo=timezone.utc)
        period_end = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        # 无刷新周期：使用固定的起止时间（Unix 纪元到遥远的未来）
        period_start = datetime(1970, 1, 1, tzinfo=timezone.utc)
        period_end = datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

    return period_start, period_end


def get_period_type(reset_period: str) -> str:
    """获取周期类型字符串

    Args:
        reset_period: 刷新周期

    Returns:
        周期类型：month, year, total
    """
    if reset_period == QuotaResetPeriod.MONTHLY or reset_period == "monthly":
        return "month"
    elif reset_period == QuotaResetPeriod.YEARLY or reset_period == "yearly":
        return "year"
    else:
        return "total"


def reset_period_to_enum(reset_period: str) -> QuotaResetPeriod:
    """将字符串转换为 QuotaResetPeriod 枚举

    Args:
        reset_period: 刷新周期字符串

    Returns:
        QuotaResetPeriod 枚举值
    """
    if reset_period == "monthly":
        return QuotaResetPeriod.MONTHLY
    elif reset_period == "yearly":
        return QuotaResetPeriod.YEARLY
    else:
        return QuotaResetPeriod.NONE
