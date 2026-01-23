"""ASR 配额预警服务

提供配额使用率预警功能：
1. 当使用率达到阈值时发送通知
2. 每个阈值每天最多发送一次
3. 支持 80%、90%、100% 三级预警
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asr_quota import AsrQuota
from app.models.notification import Notification

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
    owner_user_id: Optional[str]


def _active_window_clause(now: datetime) -> object:
    return and_(AsrQuota.window_start <= now, AsrQuota.window_end >= now)


async def check_quota_alerts(
    db: AsyncSession,
    now: Optional[datetime] = None,
) -> list[QuotaAlertInfo]:
    """检查所有配额的使用率，返回需要预警的配额列表

    Args:
        db: 数据库会话
        now: 当前时间

    Returns:
        需要预警的配额信息列表
    """
    now = now or datetime.now(timezone.utc)

    # 查询所有活跃的配额
    result = await db.execute(
        select(AsrQuota).where(_active_window_clause(now)).where(AsrQuota.quota_seconds > 0)
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
    now: Optional[datetime] = None,
) -> Optional[Notification]:
    """发送配额预警通知

    每个阈值每天最多发送一次通知。

    Args:
        db: 数据库会话
        user_id: 用户 ID
        alert: 预警信息
        now: 当前时间

    Returns:
        创建的通知对象，如果今天已发送则返回 None
    """
    now = now or datetime.now(timezone.utc)

    # 检查今天是否已发送过此阈值的通知
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

    existing = await db.execute(
        select(Notification)
        .where(Notification.user_id == user_id)
        .where(Notification.category == "system")
        .where(Notification.action == "quota_alert")
        .where(Notification.created_at >= today_start)
        .where(
            Notification.extra_data["provider"].astext == alert.provider,
            Notification.extra_data["variant"].astext == alert.variant,
            Notification.extra_data["threshold"].astext == str(alert.threshold),
        )
    )

    if existing.first():
        logger.debug(
            "Quota alert already sent today: provider=%s, variant=%s, threshold=%d",
            alert.provider,
            alert.variant,
            alert.threshold,
        )
        return None

    # 生成通知内容
    used_hours = alert.used_seconds / 3600
    quota_hours = alert.quota_seconds / 3600

    if alert.threshold == 100:
        title = f"ASR 配额已耗尽: {alert.provider}"
        message = (
            f"您的 {alert.provider} ({alert.variant}) ASR 配额已用完。"
            f"已使用 {used_hours:.1f} 小时，配额 {quota_hours:.1f} 小时。"
            f"请联系管理员增加配额或切换其他提供商。"
        )
        priority = "high"
    elif alert.threshold == 90:
        title = f"ASR 配额即将耗尽: {alert.provider}"
        message = (
            f"您的 {alert.provider} ({alert.variant}) ASR 配额使用率已达 {alert.usage_percent:.1f}%。"
            f"已使用 {used_hours:.1f} 小时，配额 {quota_hours:.1f} 小时。"
            f"剩余配额即将耗尽，请注意使用。"
        )
        priority = "high"
    else:  # 80%
        title = f"ASR 配额使用提醒: {alert.provider}"
        message = (
            f"您的 {alert.provider} ({alert.variant}) ASR 配额使用率已达 {alert.usage_percent:.1f}%。"
            f"已使用 {used_hours:.1f} 小时，配额 {quota_hours:.1f} 小时。"
        )
        priority = "normal"

    notification = Notification(
        user_id=user_id,
        category="system",
        action="quota_alert",
        title=title,
        message=message,
        priority=priority,
        extra_data={
            "provider": alert.provider,
            "variant": alert.variant,
            "threshold": alert.threshold,
            "usage_percent": alert.usage_percent,
            "used_seconds": alert.used_seconds,
            "quota_seconds": alert.quota_seconds,
        },
    )

    db.add(notification)
    await db.commit()
    await db.refresh(notification)

    logger.info(
        "Quota alert notification sent: user_id=%s, provider=%s, threshold=%d%%",
        user_id,
        alert.provider,
        alert.threshold,
    )

    return notification


async def process_all_quota_alerts(
    db: AsyncSession,
    now: Optional[datetime] = None,
) -> int:
    """处理所有配额预警

    检查所有配额，为达到阈值的配额发送预警通知。

    Args:
        db: 数据库会话
        now: 当前时间

    Returns:
        发送的通知数量
    """
    now = now or datetime.now(timezone.utc)

    alerts = await check_quota_alerts(db, now)
    sent_count = 0

    for alert in alerts:
        if alert.owner_user_id:
            # 用户级配额，通知对应用户
            result = await send_quota_alert_notification(db, alert.owner_user_id, alert, now)
            if result:
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
