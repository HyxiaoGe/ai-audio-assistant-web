"""ASR 免费额度配置

定义各平台的免费额度，代码内置，无需手动配置。

数据来源：
- 腾讯云: https://cloud.tencent.com/document/product/1093/35686
- 阿里云: https://help.aliyun.com/zh/isi/product-overview/billing-10
- 火山引擎: https://www.volcengine.com/docs/6561/1359370
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class QuotaResetPeriod(str, Enum):
    """额度刷新周期"""

    MONTHLY = "monthly"  # 每月刷新
    YEARLY = "yearly"  # 每年刷新
    NONE = "none"  # 不刷新（无免费额度）


@dataclass(frozen=True)
class FreeQuotaConfig:
    """免费额度配置"""

    provider: str  # 服务商：tencent, aliyun, volcengine
    variant: str  # 服务变体：file, file_fast
    free_seconds: float  # 免费额度（秒）
    reset_period: QuotaResetPeriod  # 刷新周期
    cost_per_hour: float  # 超出后单价（元/小时）


# 各平台免费额度配置
# 免费额度是平台固定发放的，在代码中静态定义
FREE_QUOTA_CONFIGS: dict[tuple[str, str], FreeQuotaConfig] = {
    # 腾讯云
    ("tencent", "file"): FreeQuotaConfig(
        provider="tencent",
        variant="file",
        free_seconds=0,  # 无免费额度
        reset_period=QuotaResetPeriod.NONE,
        cost_per_hour=1.25,
    ),
    ("tencent", "file_fast"): FreeQuotaConfig(
        provider="tencent",
        variant="file_fast",
        free_seconds=5 * 3600,  # 5小时/月
        reset_period=QuotaResetPeriod.MONTHLY,
        cost_per_hour=3.10,
    ),
    # 阿里云 - 无免费额度，全部按量付费
    ("aliyun", "file"): FreeQuotaConfig(
        provider="aliyun",
        variant="file",
        free_seconds=0,
        reset_period=QuotaResetPeriod.NONE,
        cost_per_hour=2.5,
    ),
    ("aliyun", "file_fast"): FreeQuotaConfig(
        provider="aliyun",
        variant="file_fast",
        free_seconds=0,
        reset_period=QuotaResetPeriod.NONE,
        cost_per_hour=3.3,
    ),
    # 火山引擎
    ("volcengine", "file"): FreeQuotaConfig(
        provider="volcengine",
        variant="file",
        free_seconds=20 * 3600,  # 20小时/年
        reset_period=QuotaResetPeriod.YEARLY,
        cost_per_hour=0.8,
    ),
    ("volcengine", "file_fast"): FreeQuotaConfig(
        provider="volcengine",
        variant="file_fast",
        free_seconds=0,  # 流式识别无免费额度
        reset_period=QuotaResetPeriod.NONE,
        cost_per_hour=1.2,
    ),
}


def get_free_quota_config(provider: str, variant: str) -> Optional[FreeQuotaConfig]:
    """获取免费额度配置

    Args:
        provider: 服务商
        variant: 服务变体

    Returns:
        免费额度配置，未找到返回 None
    """
    return FREE_QUOTA_CONFIGS.get((provider, variant))


def get_all_free_quota_configs() -> list[FreeQuotaConfig]:
    """获取所有有免费额度的配置

    Returns:
        有免费额度的配置列表
    """
    return [config for config in FREE_QUOTA_CONFIGS.values() if config.free_seconds > 0]


def get_current_period_bounds(
    reset_period: QuotaResetPeriod,
    now: Optional[datetime] = None,
) -> tuple[datetime, datetime]:
    """计算当前周期的开始和结束时间

    Args:
        reset_period: 刷新周期
        now: 当前时间（默认 UTC 时间）

    Returns:
        (period_start, period_end) 元组
    """
    now = now or datetime.now(timezone.utc)

    if reset_period == QuotaResetPeriod.MONTHLY:
        # 月度周期：当月1日 00:00:00 到 下月1日 00:00:00
        period_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        if now.month == 12:
            period_end = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            period_end = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    elif reset_period == QuotaResetPeriod.YEARLY:
        # 年度周期：当年1月1日 00:00:00 到 下年1月1日 00:00:00
        period_start = datetime(now.year, 1, 1, tzinfo=timezone.utc)
        period_end = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        # 无刷新周期：使用固定的起止时间（Unix 纪元到遥远的未来）
        period_start = datetime(1970, 1, 1, tzinfo=timezone.utc)
        period_end = datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

    return period_start, period_end


def get_period_type(reset_period: QuotaResetPeriod) -> str:
    """获取周期类型字符串

    Args:
        reset_period: 刷新周期

    Returns:
        周期类型：month, year, total
    """
    if reset_period == QuotaResetPeriod.MONTHLY:
        return "month"
    elif reset_period == QuotaResetPeriod.YEARLY:
        return "year"
    else:
        return "total"
