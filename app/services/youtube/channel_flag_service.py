from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import ColumnElement, case, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.exceptions import BusinessError
from app.db import async_session_factory
from app.i18n.codes import ErrorCode
from app.models.flagged_channel import FlaggedChannel
from app.services.youtube import blocklist_service
from app.services.youtube.search_cache import normalize_query

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.services.youtube.search_service import VideoHit

logger = logging.getLogger(__name__)


def _flag_identity(hit: VideoHit) -> tuple[str, str] | None:
    """被 block 的频道去重身份,镜像 blocklist_service.is_channel_blocked 三级优先。

    channel_id 原样 > normalize_handle(handle) > normalize_query(channel);三无 → None(无法归因)。
    """
    if hit.channel_id:
        return ("channel_id", hit.channel_id)
    if hit.handle:
        return ("channel_handle", blocklist_service.normalize_handle(hit.handle))
    if hit.channel:
        return ("channel_name", normalize_query(hit.channel))
    return None


def _conflict_last_title(title: str) -> ColumnElement[str | None]:
    """on_conflict 时 last_title 的更新值:仅 pending 行更新为新标题,
    已 resolved(dismissed/blocked)行保留现值——保住 resolve 置的 NULL,防再次标记把政治明文回填。"""
    return case((FlaggedChannel.status == "pending", title), else_=FlaggedChannel.last_title)


async def record_flags(blocked: list[VideoHit]) -> None:
    """把本批被 CMS block 的频道累积进 flagged_channels(原子 upsert,status 不动)。

    best-effort:自管 session、整体吞异常——标记失败绝不能影响搜索主流程。
    """
    rows = [(ident, hit) for hit in blocked if (ident := _flag_identity(hit)) is not None]
    if not rows:
        return
    try:
        async with async_session_factory() as session:
            now = datetime.now(UTC)
            for (match_field, match_value), hit in rows:
                # 截断到各列宽,防超长第三方文本触发 insert 报错(best-effort 会吞掉→静默丢标记)
                match_value = match_value[:256]
                title = (hit.title or "")[:256]
                channel_handle = hit.handle[:128] if hit.handle else None
                channel_name = hit.channel[:256] if hit.channel else None
                stmt = (
                    pg_insert(FlaggedChannel)
                    .values(
                        match_field=match_field,
                        match_value=match_value,
                        channel_id=hit.channel_id,
                        channel_handle=channel_handle,
                        channel_name=channel_name,
                        block_count=1,
                        last_video_id=hit.video_id,
                        last_title=title,
                        last_flagged_at=now,
                        status="pending",
                    )
                    .on_conflict_do_update(
                        index_elements=["match_field", "match_value"],
                        set_={
                            "block_count": FlaggedChannel.block_count + 1,
                            "last_video_id": hit.video_id,
                            "last_title": _conflict_last_title(title),
                            "last_flagged_at": now,
                            # status 故意不动:dismissed/blocked 行仍累积 count 但不回 pending 队列
                        },
                    )
                )
                await session.execute(stmt)
            await session.commit()
    except Exception:
        logger.warning("record_flags failed", exc_info=True)


async def list_pending(db: AsyncSession) -> list[FlaggedChannel]:
    """复核队列:status='pending',按累计次数降序(并列时最近标记优先)。"""
    rows = (
        (
            await db.execute(
                select(FlaggedChannel)
                .where(FlaggedChannel.status == "pending")
                .order_by(FlaggedChannel.block_count.desc(), FlaggedChannel.last_flagged_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def resolve(
    db: AsyncSession,
    *,
    flag_id: str,
    action: str,
    admin_id: str,
    note: str | None = None,
) -> FlaggedChannel:
    """复核处置:block→提升频道黑名单(复用 add_entry)+加白;dismiss→永久加白。仅作用于 pending。"""
    flag = await db.get(FlaggedChannel, flag_id)
    if flag is None:
        raise BusinessError(ErrorCode.RESOURCE_NOT_FOUND)
    if flag.status != "pending":
        raise BusinessError(ErrorCode.FLAG_ALREADY_RESOLVED)

    if action == "block":
        value = flag.channel_id or (f"@{flag.channel_handle}" if flag.channel_handle else flag.channel_name)
        if not value:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="flag")
        try:
            await blocklist_service.add_entry(db, kind="channel", value=value, note=note, created_by=admin_id)
        except Exception:
            await db.rollback()
            raise
        flag.status = "blocked"
    elif action == "dismiss":
        flag.status = "dismissed"
    else:
        raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="action")

    flag.resolved_by = admin_id
    flag.resolved_at = datetime.now(UTC)
    flag.last_title = None  # 脱敏:复核处置后样本标题已无用,清掉政治敏感明文
    await db.commit()
    if action == "block":
        # 缓存失效放在 commit 之后:确保 flag 行与黑名单条目都已落库,不依赖 add_entry 的内部 commit 时序
        blocklist_service.invalidate_cache()
    return flag


async def scrub_resolved_titles(db: AsyncSession) -> int:
    """把已处置(非 pending)行的 last_title 置 NULL,返影响条数。

    幂等:既一次性回填既有已 blocked/dismissed 行(脱敏历史明文),又是组件① 的长期安全网。
    pending 行不动(复核面板要靠 last_title 展示样本)。
    """
    stmt = (
        update(FlaggedChannel)
        .where(FlaggedChannel.status != "pending", FlaggedChannel.last_title.is_not(None))
        .values(last_title=None)
    )
    result = await db.execute(stmt)
    await db.commit()
    return result.rowcount or 0
