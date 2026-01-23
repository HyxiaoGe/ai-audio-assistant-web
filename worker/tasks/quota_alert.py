"""ASR 配额预警定时任务

定时检查配额使用率并发送预警通知。
"""

from __future__ import annotations

import asyncio
import logging

from celery import shared_task

from app.db import async_session_factory
from app.services.asr_quota_alert import process_all_quota_alerts

logger = logging.getLogger(__name__)


@shared_task(name="worker.tasks.quota_alert.check_asr_quota_alerts")
def check_asr_quota_alerts() -> dict:
    """检查 ASR 配额预警

    定时任务，检查所有配额的使用率，为达到阈值的配额发送预警通知。

    Returns:
        任务执行结果
    """

    async def _run() -> int:
        async with async_session_factory() as session:
            return await process_all_quota_alerts(session)

    try:
        sent_count = asyncio.run(_run())
        logger.info("ASR quota alert check completed: sent %d notifications", sent_count)
        return {"status": "success", "sent_count": sent_count}
    except Exception as e:
        logger.exception("ASR quota alert check failed: %s", str(e))
        return {"status": "error", "error": str(e)}
