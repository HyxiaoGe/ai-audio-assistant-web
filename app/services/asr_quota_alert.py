"""ASR 配额预警服务

提供配额使用率预警功能：
1. 当使用率达到阈值时发送通知
2. 每个阈值每天最多发送一次
3. 支持 80%、90%、100% 三级预警
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asr_user_quota import AsrUserQuota
from app.services.notifications.service import NotificationService
from app.services.notifications.types import NotificationType

logger = logging.getLogger(__name__)

# 预警阈值配置
ALERT_THRESHOLDS = [80, 90, 100]


@dataclass
class QuotaAlertInfo:
    """配额预警信息"""

    provider: str
    variant: str
    window_type: str
    quota_seconds: float
    used_seconds: float
    usage_percent: float
    threshold: int
    owner_user_id: str | None


def _active_window_clause(now: datetime) -> object:
    return and_(AsrUserQuota.window_start <= now, AsrUserQuota.window_end >= now)


async def check_quota_alerts(
    db: AsyncSession,
    now: datetime | None = None,
) -> list[QuotaAlertInfo]:
    """检查所有配额的使用率，返回需要预警的配额列表

    Args:
        db: 数据库会话
        now: 当前时间

    Returns:
        需要预警的配额信息列表
    """
    now = now or datetime.now(UTC)

    # 查询所有活跃的配额
    result = await db.execute(
        select(AsrUserQuota).where(_active_window_clause(now)).where(AsrUserQuota.quota_seconds > 0)
    )
    quotas = result.scalars().all()

    alerts: list[QuotaAlertInfo] = []

    for quota in quotas:
        usage_percent = (quota.used_seconds / quota.quota_seconds) * 100

        # 检查是否达到任一阈值
        for threshold in ALERT_THRESHOLDS:
            if usage_percent >= threshold:
                alerts.append(
                    QuotaAlertInfo(
                        provider=quota.provider,
                        variant=quota.variant,
                        window_type=quota.window_type,
                        quota_seconds=quota.quota_seconds,
                        used_seconds=quota.used_seconds,
                        usage_percent=usage_percent,
                        threshold=threshold,
                        owner_user_id=quota.owner_user_id,
                    )
                )
                break  # 只记录最高达到的阈值

    return alerts


async def send_quota_alert_notification(
    db: AsyncSession,
    user_id: str,
    alert: QuotaAlertInfo,
    now: datetime | None = None,
) -> None:
    """发送配额预警通知。

    去重交给 dedup_key=quota:{provider}:{variant}:{threshold}:{utc_date} 的唯一索引，
    撞索引由 InAppChannel 吞为「已通知」，原子无竞态（替掉旧的「先查再插」）。
    """
    now = now or datetime.now(UTC)
    dedup_key = f"quota:{alert.provider}:{alert.variant}:{alert.threshold}:{now.date().isoformat()}"

    NotificationService.notify(
        db,
        type=NotificationType.QUOTA_ALERT,
        user_id=user_id,
        params={
            "provider": alert.provider,
            "variant": alert.variant,
            "threshold": alert.threshold,
            "usage_percent": alert.usage_percent,
            "used_seconds": alert.used_seconds,
            "quota_seconds": alert.quota_seconds,
        },
        dedup_key=dedup_key,
    )

    logger.info(
        "Quota alert dispatched: user_id=%s, provider=%s, threshold=%d%%",
        user_id,
        alert.provider,
        alert.threshold,
    )


async def process_all_quota_alerts(
    db: AsyncSession,
    now: datetime | None = None,
) -> int:
    """处理所有配额预警

    检查所有配额，为达到阈值的配额发送预警通知。

    Args:
        db: 数据库会话
        now: 当前时间

    Returns:
        发送的通知数量
    """
    now = now or datetime.now(UTC)

    alerts = await check_quota_alerts(db, now)
    sent_count = 0

    for alert in alerts:
        if alert.owner_user_id:
            await send_quota_alert_notification(db, alert.owner_user_id, alert, now)
            sent_count += 1
        else:
            # 全局配额，通知所有管理员
            # TODO: 实现管理员通知逻辑
            logger.warning(
                "Global quota alert: provider=%s, variant=%s, usage=%.1f%%",
                alert.provider,
                alert.variant,
                alert.usage_percent,
            )

    logger.info("Quota alert processing completed: sent %d notifications", sent_count)
    return sent_count
