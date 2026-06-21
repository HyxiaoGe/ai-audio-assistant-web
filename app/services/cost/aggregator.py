"""按用户聚合各成本来源。

- ASR(¥):走厂商直连 SDK,app 侧 ASRUsage 账本是权威来源 → SQL GROUP BY user_id 求和。
- 配图(¥):远端 image-service 不回成本,按「ready 图片数 × 每模型价」app 侧计 → 见 image_cost_by_user。
- LLM($):全量经 LiteLLM 代理,LiteLLM 是权威来源 → 不在此处,见 app/services/llm/spend_client.py。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asr_usage import ASRUsage
from app.models.summary import Summary
from app.models.task import Task


def build_asr_cost_by_user_statement(
    start: datetime | None = None,
    end: datetime | None = None,
) -> Select:
    """按 user_id 聚合 ASR 成本(¥)的查询语句。

    estimated_cost = 毛成本(按用量估);actual_paid_cost = 扣免费额度后实付。两者都求和,
    由上层决定展示口径(默认用 estimated 作「成本花销」头条)。
    """
    stmt = (
        select(
            ASRUsage.user_id,
            func.coalesce(func.sum(ASRUsage.estimated_cost), 0.0).label("estimated_cny"),
            func.coalesce(func.sum(ASRUsage.actual_paid_cost), 0.0).label("paid_cny"),
            func.count().label("call_count"),
        )
        .group_by(ASRUsage.user_id)
        .order_by(func.sum(ASRUsage.estimated_cost).desc())
    )
    if start is not None:
        stmt = stmt.where(ASRUsage.created_at >= start)
    if end is not None:
        stmt = stmt.where(ASRUsage.created_at <= end)
    return stmt


async def asr_cost_by_user(
    db: AsyncSession,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, dict[str, float | int]]:
    """{user_id: {estimated_cny, paid_cny, calls}}。"""
    rows = (await db.execute(build_asr_cost_by_user_statement(start, end))).all()
    return {
        str(row.user_id): {
            "estimated_cny": float(row.estimated_cny or 0.0),
            "paid_cny": float(row.paid_cny or 0.0),
            "calls": int(row.call_count or 0),
        }
        for row in rows
    }


def build_image_rows_statement(
    start: datetime | None = None,
    end: datetime | None = None,
) -> Select:
    """取所有带图的 active summary 及其 task 的 user_id(配图计数原料)。

    SQLite(测试)无 jsonb 函数,故只在 SQL 侧做「有无图」的粗过滤 + 拉回 JSONB,
    具体 ready 计数在 image_cost_by_user 用 Python 完成(便于跨方言测试)。
    """
    stmt = (
        select(
            Task.user_id,
            Summary.images,
            Summary.image_key,
            Summary.image_model_used,
        )
        .join(Task, Summary.task_id == Task.id)
        .where(Summary.is_active.is_(True))
        .where(or_(Summary.images.isnot(None), Summary.image_key.isnot(None)))
    )
    if start is not None:
        stmt = stmt.where(Summary.created_at >= start)
    if end is not None:
        stmt = stmt.where(Summary.created_at <= end)
    return stmt


async def image_cost_by_user(
    db: AsyncSession,
    price_fn: Callable[[str | None], float],
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, float]:
    """{user_id: 配图成本(¥)}。

    优先按 images JSONB 的 ready 项逐张计价(每张取其 model_id,缺失回落 summary 的
    image_model_used);仅有 legacy 单图(image_key)时计 1 张。pending/failed 不计费
    (从未产出真实图)。
    """
    rows = (await db.execute(build_image_rows_statement(start, end))).all()
    cost: dict[str, float] = defaultdict(float)
    for row in rows:
        uid = str(row.user_id)
        image_model = row.image_model_used
        counted_from_band = False
        if row.images:
            for img in _iter_images(row.images):
                if img.get("status") == "ready":
                    cost[uid] += price_fn(img.get("model_id") or image_model)
                    counted_from_band = True
        if not counted_from_band and row.image_key:
            cost[uid] += price_fn(image_model)
    return dict(cost)


def _iter_images(images: Any) -> list[dict[str, Any]]:
    """images 列只接受 list[dict];脏数据(非列表/非字典项)安全跳过。"""
    if not isinstance(images, list):
        return []
    return [img for img in images if isinstance(img, dict)]
