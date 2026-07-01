from __future__ import annotations

import logging

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task
from app.services.task_service import TaskService
from app.services.youtube.search_service import VideoHit

logger = logging.getLogger(__name__)


async def annotate_existing_tasks(
    db: AsyncSession, hits: list[VideoHit], viewer_id: str | None
) -> list[VideoHit]:
    """给搜索结果就地叠加「已有转写」信息(serve 时按 viewer 现算,绝不进缓存)。

    回填 existing_task_id / existing_is_owner:
    - viewer 自己的任务(任意状态)优先;否则别人的公开 completed;都无则保持 None/False。
    - 隐私:绝不暴露别人的私有任务。查询异常 → WARNING + 原样返回(不阻断搜索)。
    """
    if not hits:
        return hits

    # content_hash(唯一来源)→ 该 hash 对应的 hits(同一 video_id 可能重复出现)
    hash_by_video: dict[str, str] = {}
    for hit in hits:
        hash_by_video[hit.video_id] = TaskService._generate_content_hash(f"youtube:{hit.video_id}")
    hashes = list(set(hash_by_video.values()))

    public_cond = and_(Task.is_public.is_(True), Task.status == "completed")
    ownership = or_(Task.user_id == viewer_id, public_cond) if viewer_id is not None else public_cond

    try:
        result = await db.execute(
            select(Task.id, Task.user_id, Task.content_hash)
            .where(
                Task.content_hash.in_(hashes),
                Task.deleted_at.is_(None),
                ownership,
            )
            .order_by(Task.created_at.desc())
        )
        rows = result.all()
    except Exception as exc:  # fail-safe:既有任务查不到不阻断搜索
        logger.warning("annotate_existing_tasks query failed: %s", exc)
        return hits

    # 按 content_hash 归并;优先级 viewer 自己 > 别人公开(desc 排序内同类取最新)
    # 用 UUID 规范化比较(去横杠+小写),避免存储格式差异(PostgreSQL 带横杠 / SQLite 测试无横杠)。
    viewer_norm = viewer_id.replace("-", "").lower() if viewer_id is not None else None
    best: dict[str, tuple[str, bool]] = {}  # content_hash -> (task_id, is_owner)
    for row in rows:
        is_owner = viewer_norm is not None and str(row.user_id).replace("-", "").lower() == viewer_norm
        existing = best.get(row.content_hash)
        if existing is None or (is_owner and not existing[1]):
            best[row.content_hash] = (str(row.id), is_owner)

    if not best:
        return hits

    for hit in hits:
        match = best.get(hash_by_video[hit.video_id])
        if match is not None:
            hit.existing_task_id, hit.existing_is_owner = match
    return hits
