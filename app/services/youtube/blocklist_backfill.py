from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.models.flagged_channel import FlaggedChannel
from app.models.youtube_blocklist import YouTubeBlocklistEntry
from app.models.youtube_search import YouTubeSearchQuery
from app.services.youtube import blocklist_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def _pending_name_entries(db: AsyncSession) -> list[YouTubeBlocklistEntry]:
    """活跃的 channel 型、尚无 display_name 的黑名单条目。"""
    rows = (
        (
            await db.execute(
                select(YouTubeBlocklistEntry).where(
                    YouTubeBlocklistEntry.kind == "channel",
                    YouTubeBlocklistEntry.display_name.is_(None),
                    YouTubeBlocklistEntry.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def _build_local_name_index(db: AsyncSession) -> tuple[dict[str, str], dict[str, str]]:
    """从 flagged_channels + 搜索缓存结果构建 {channel_id: name} 与 {归一化 handle: name} 两索引(零网络)。"""
    by_id: dict[str, str] = {}
    by_handle: dict[str, str] = {}

    # flagged_channels:promote 来源,channel_name 从不被脱敏清空
    flagged = (
        await db.execute(
            select(
                FlaggedChannel.channel_id,
                FlaggedChannel.channel_handle,
                FlaggedChannel.channel_name,
            ).where(FlaggedChannel.channel_name.is_not(None))
        )
    ).all()
    for cid, handle, name in flagged:
        if not name:
            continue
        if cid:
            by_id.setdefault(cid, name)
        if handle:
            by_handle.setdefault(blocklist_service.normalize_handle(handle), name)

    # 搜索缓存 results_json:每条 VideoHit dict 含 channel/channel_id/handle
    cached = (await db.execute(select(YouTubeSearchQuery.results_json))).all()
    for (results,) in cached:
        for d in results or []:
            if not isinstance(d, dict):
                continue
            name = d.get("channel")
            if not isinstance(name, str) or not name.strip():
                continue
            cid = d.get("channel_id")
            handle = d.get("handle")
            if isinstance(cid, str):
                by_id.setdefault(cid, name)
            if isinstance(handle, str):
                by_handle.setdefault(blocklist_service.normalize_handle(handle), name)

    return by_id, by_handle


async def backfill_display_names(db: AsyncSession, *, use_network: bool = True) -> dict[str, int]:
    """既有 channel 型黑名单条目回填 display_name。

    每条:channel_name 型用 raw_value;channel_id/channel_handle 型先查本地索引,
    再(use_network)yt-dlp 兜底。取不到留空(前端回落 raw_value),best-effort,绝不抛。
    """
    entries = await _pending_name_entries(db)
    by_id, by_handle = await _build_local_name_index(db)
    filled = 0
    for e in entries:
        name: str | None = None
        if e.match_field == "channel_name":
            name = e.raw_value
        elif e.match_field == "channel_id":
            name = by_id.get(e.normalized_value)
            if not name and use_network:
                try:
                    name = await blocklist_service.resolve_channel_name_by_id(e.normalized_value)
                except Exception:
                    logger.warning("resolve_channel_name_by_id failed for %s", e.normalized_value, exc_info=True)
                    name = None
        elif e.match_field == "channel_handle":
            name = by_handle.get(e.normalized_value)
            if not name and use_network:
                try:
                    _, name = await blocklist_service.resolve_channel_meta(e.normalized_value)
                except Exception:
                    logger.warning("resolve_channel_meta failed for %s", e.normalized_value, exc_info=True)
                    name = None
        if name and name.strip():
            e.display_name = name.strip()[:256]
            filled += 1
    await db.commit()
    return {"total": len(entries), "filled": filled, "unresolved": len(entries) - filled}
