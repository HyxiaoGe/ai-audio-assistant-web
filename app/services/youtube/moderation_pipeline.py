from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.moderation import gate as moderation_gate
from app.services.youtube import allowlist_service, blocklist_service, channel_flag_service
from app.services.youtube.blocklist_service import Blocklist
from app.services.youtube.search_service import VideoHit


async def moderate_hits(
    db: AsyncSession,
    hits: list[VideoHit],
    bl: Blocklist,
    *,
    request_id: str | None = None,
) -> tuple[list[VideoHit], bool]:
    """搜索/推荐共用的展示态过审管道。与搜索端点 miss 路径完全一致:

    先剔已拉黑频道 → 放行表命中项分流(绕过 CMS 直接保留)→ 其余送 CMS filter_display →
    被 block 的频道累积进复核队列(best-effort)→ 按原相关性顺序重建。

    返回 (kept, sensitive):kept=保序保留项;sensitive=本批有 CMS block 项(供搜索端点标 trending 排除)。
    """
    hits = blocklist_service.filter_hits(hits, bl)
    al = await allowlist_service.get_allowlist(db)
    to_moderate = [h for h in hits if not allowlist_service.is_channel_allowed(h, al)]
    outcome = await moderation_gate.filter_display(to_moderate, request_id=request_id)
    await channel_flag_service.record_flags(outcome.blocked)
    kept_ids = {id(h) for h in outcome.kept}
    kept = [h for h in hits if allowlist_service.is_channel_allowed(h, al) or id(h) in kept_ids]
    return kept, len(outcome.blocked) > 0
