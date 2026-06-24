"""死任务兜底巡检(beat 每 15min)。

调研「中断→死任务」(2026-06-25)确认全系统零兜底巡检,自愈完全押在 Celery 消息重投上,
重投覆盖不到的尾部(派发消息丢失 / broker 丢消息 / 任务体非 worker 死亡异常)会永久卡死。
本任务两件独立的事(各自 try/except,互不拖累):
  1a. 重派卡 pending 的 overview 配图(completed 任务 + is_active overview + 久无更新 + 含 pending 槽);
  1b. 把卡在非终态、长时间无更新的任务标 failed,使其可被现有 retry_task 恢复。
均用 worker sync session(同 cleanup_task)。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.i18n.codes import ErrorCode
from app.models.summary import Summary
from app.models.task import Task
from app.services.summary.style_catalog import normalize_content_style
from worker.celery_app import celery_app
from worker.db import get_sync_db_session
from worker.redis_client import get_sync_redis_client

logger = logging.getLogger("worker.dead_task_sweeper")

IMAGE_STALE_SECONDS = 30 * 60  # 配图槽陈旧阈值:30min(安全越过图硬超时 1300s;每写一张图刷新 updated_at)
TASK_STALE_SECONDS = 2 * 60 * 60  # 任务陈旧阈值:2h(安全越过任务硬超时 2000s + 一个 3600s 重投周期)
SWEEP_BATCH_LIMIT = 100  # 每轮每类上限,防巡检自身长跑
IMAGE_RECONCILE_COOLDOWN_SECONDS = 1800  # 同 summary 重派冷却,防队列积压时反复重派
# 死任务判定用排除法(deny-list):凡 Task.status 不在终态集合、且久无更新者皆视为卡死。
# 用排除法而非枚举非终态——audio/youtube 两条管线的非终态多达十余种(queued / 摄入各阶段 /
# 转写 / 润色 / 摘要…),枚举漏一个就静默放过(原 bug:漏了 queued 与全部 youtube 摄入态,
# 且 "pending" 仅是 DB server_default 从无代码写入=死枚举);新增管线阶段也自动覆盖。
# 终态仅三个:completed / failed / cancelled。
TERMINAL_STATUSES = ("completed", "failed", "cancelled")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _reconcile_stuck_image_slots(session: Session) -> int:
    """重新派发卡 pending 的 overview 配图。返回成功派发的 summary 数。"""
    cutoff = _utcnow() - timedelta(seconds=IMAGE_STALE_SECONDS)
    rows = session.execute(
        select(Summary, Task)
        .join(Task, Task.id == Summary.task_id)
        .where(
            Summary.summary_type == "overview",
            Summary.is_active.is_(True),
            Summary.updated_at < cutoff,
            Task.status == "completed",
            Task.deleted_at.is_(None),
        )
        .order_by(Summary.updated_at.asc())
        .limit(SWEEP_BATCH_LIMIT)
    ).all()

    redis_client = get_sync_redis_client()
    dispatched = 0
    for summary, task in rows:
        if not summary.images or not any(i.get("status") == "pending" for i in summary.images):
            continue
        cooldown_key = f"summary:imgreconcile:lock:{summary.id}"
        if not redis_client.set(cooldown_key, "1", nx=True, ex=IMAGE_RECONCILE_COOLDOWN_SECONDS):
            continue  # 近期已派发,防抖
        content_style = normalize_content_style((task.options or {}).get("summary_style"))
        try:
            celery_app.send_task(
                "worker.tasks.generate_summary_images_async",
                kwargs={
                    "task_id": str(task.id),
                    "user_id": str(task.user_id),
                    "summary_id": str(summary.id),
                    "content": summary.content,
                    "content_style": content_style,
                },
            )
            dispatched += 1
        except Exception:
            logger.warning("dead_task_sweep: re-enqueue images failed for summary %s", summary.id, exc_info=True)
            try:
                redis_client.delete(cooldown_key)
            except Exception:
                logger.warning("dead_task_sweep: failed to release cooldown key %s", cooldown_key, exc_info=True)
    return dispatched


def _fail_stuck_tasks(session: Session) -> int:
    """把卡在非终态、长时间无更新的任务标 failed(使其可被现有 retry 恢复)。返回标记数。"""
    cutoff = _utcnow() - timedelta(seconds=TASK_STALE_SECONDS)
    tasks = (
        session.execute(
            select(Task)
            .where(
                Task.status.notin_(TERMINAL_STATUSES),
                Task.updated_at < cutoff,
                Task.deleted_at.is_(None),
            )
            .order_by(Task.updated_at.asc())
            .limit(SWEEP_BATCH_LIMIT)
        )
        .scalars()
        .all()
    )
    count = 0
    for task in tasks:
        task.status = "failed"
        task.progress = 0
        task.error_code = ErrorCode.TASK_STALLED.value
        task.error_message = "任务长时间未完成，已自动标记为失败，请重试"
        count += 1
    if count:
        session.commit()
    return count


@celery_app.task(name="worker.tasks.run_dead_task_sweep")
def run_dead_task_sweep() -> dict[str, int]:
    """beat 入口:跑两类巡检,各自独立容错,任何异常都不冒泡(避免触发重投把巡检自身重复)。"""
    if not settings.DEAD_TASK_SWEEP_ENABLED:
        return {"images_reconciled": 0, "tasks_failed": 0, "skipped": 1}

    images_reconciled = 0
    tasks_failed = 0
    try:
        with get_sync_db_session() as session:
            images_reconciled = _reconcile_stuck_image_slots(session)
    except Exception:
        logger.exception("dead_task_sweep: image reconcile failed")
    try:
        with get_sync_db_session() as session:
            tasks_failed = _fail_stuck_tasks(session)
    except Exception:
        logger.exception("dead_task_sweep: stuck-task fail failed")
    logger.info("dead_task_sweep done: images_reconciled=%d tasks_failed=%d", images_reconciled, tasks_failed)
    return {"images_reconciled": images_reconciled, "tasks_failed": tasks_failed}
