"""审核决策 gate:唯一真值表 + 结构化影子日志。

两个调用点(搜索 / 发布)各调一个场景函数。off 短路不调 CMS;shadow 恒放行只记日志;
enforce 按云判决拦截(block / degraded 一律 fail-closed)。review 恒放行(无人工复审队列)。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from app.config import settings
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.services.moderation import config
from app.services.moderation.client import ModerationClient, ModerationResult

if TYPE_CHECKING:
    from app.services.youtube.search_service import VideoHit

logger = logging.getLogger(__name__)


async def _moderate(text: str, scene: str, request_id: str | None) -> ModerationResult:
    """调 CMS 的测试 seam。gate 单测 monkeypatch 此函数以隔离 HTTP。"""
    return await ModerationClient().moderate(text, scene=scene, request_id=request_id)


def _log(scene: str, mode: str, text: str, result: ModerationResult) -> None:
    """每次非 off 判定打一行结构化日志,供影子阶段量误杀率。不打文本明文(只打长度)。"""
    would_block = result.action in ("block", "degraded")
    logger.info(
        "moderation scene=%s mode=%s decision=%s would_block=%s text_len=%d cloud=%s cms_trace_id=%s latency_ms=%s",
        scene,
        mode,
        result.action,
        would_block,
        len(text),
        result.cloud_label,
        result.cms_trace_id,
        result.latency_ms,
    )


async def _decide(*, scene: str, mode: str, text: str, block_code: ErrorCode, request_id: str | None) -> None:
    """唯一决策真值表。off/放行 → return;enforce 命中 block 或 degraded → raise BusinessError。"""
    if mode == "off":
        return
    if not text or not text.strip():
        return  # 无可审文本,直接放行
    result = await _moderate(text, scene, request_id)
    _log(scene, mode, text, result)
    if mode == "shadow":
        return  # 影子:恒放行(would_block 已记日志)
    # enforce:
    if result.action == "block":
        raise BusinessError(block_code)
    if result.action == "degraded":
        raise BusinessError(ErrorCode.MODERATION_SERVICE_UNAVAILABLE)  # 全局 fail-closed
    # pass / review → 放行


async def search_query(text: str, *, request_id: str | None) -> None:
    """搜索输入审核(scene=search_query)。命中 → YOUTUBE_SEARCH_QUERY_BLOCKED。"""
    await _decide(
        scene="search_query",
        mode=config.search_mode(),
        text=text,
        block_code=ErrorCode.YOUTUBE_SEARCH_QUERY_BLOCKED,
        request_id=request_id,
    )


async def publish(text: str, *, request_id: str | None) -> None:
    """公开发布审核(scene=ugc_publish)。命中 → PUBLISH_CONTENT_BLOCKED。"""
    await _decide(
        scene="ugc_publish",
        mode=config.publish_mode(),
        text=text,
        block_code=ErrorCode.PUBLISH_CONTENT_BLOCKED,
        request_id=request_id,
    )


async def filter_display(hits: list[VideoHit], *, request_id: str | None) -> list[VideoHit]:
    """搜索结果展示态审核(scene=ugc_display)。审 title+频道名 拼接文本,逐项并发。

    off / 空列表 → 原样返回(零 CMS 调用)。
    enforce:block 项剔除并打频道归因日志;任一 degraded → 抛 51400(整批 fail-closed,
            调用方不应缓存)。pass / review → 保留。
    shadow:恒保留;block 项打 would_block 频道归因日志(量误杀 + 预热 Spec#2);degraded 不拦。
    空/纯空白文本项不调 CMS、直接保留。
    """
    mode = config.display_mode()
    if mode == "off" or not hits:
        return hits

    sem = asyncio.Semaphore(settings.MODERATION_DISPLAY_CONCURRENCY)

    async def _judge(hit: VideoHit) -> tuple[VideoHit, ModerationResult | None]:
        text = f"{hit.title or ''} {hit.channel or ''}".strip()
        if not text:
            return hit, None  # 无可审文本 → 保留
        async with sem:
            return hit, await _moderate(text, "ugc_display", request_id)

    judged = await asyncio.gather(*(_judge(h) for h in hits))

    kept: list[VideoHit] = []
    counts = {"pass": 0, "review": 0, "block": 0, "degraded": 0, "skip": 0}
    for hit, result in judged:
        if result is None:
            counts["skip"] += 1
            kept.append(hit)
            continue
        counts[result.action] = counts.get(result.action, 0) + 1
        if result.action == "block":
            _log_display_block(mode, hit, result, request_id)
            if mode == "enforce":
                continue  # 剔除
            kept.append(hit)  # shadow:保留但已记 would_block
        elif result.action == "degraded":
            if mode == "enforce":
                raise BusinessError(ErrorCode.MODERATION_SERVICE_UNAVAILABLE)  # 全局 fail-closed
            kept.append(hit)  # shadow:不拦
        else:
            kept.append(hit)  # pass / review → 保留
    _log_display_summary(mode, len(hits), counts, request_id)
    return kept


def _log_display_block(mode: str, hit: VideoHit, result: ModerationResult, request_id: str | None) -> None:
    """频道归因日志:CMS 判 block 的展示项。Spec#1=量误杀;同时是 Spec#2 频道标记复核队列的接缝/回填源。

    标题是公开 YouTube 视频标题(非用户隐私文本),故记明文(截断 120 字)供人工辨识;
    三维频道身份(channel_id/handle/channel)全打,对齐人工频道黑名单匹配维度。
    """
    logger.warning(
        "moderation_display_block mode=%s decision=block channel_id=%s handle=%s channel=%s "
        "video_id=%s title=%r cloud=%s cms_trace_id=%s request_id=%s",
        mode,
        hit.channel_id,
        hit.handle,
        hit.channel,
        hit.video_id,
        (hit.title or "")[:120],
        result.cloud_label,
        result.cms_trace_id,
        request_id,
    )


def _log_display_summary(mode: str, total: int, counts: dict[str, int], request_id: str | None) -> None:
    """每批一行汇总:供一眼看误杀率(block/total)与决策分布。"""
    logger.info(
        "moderation_display_batch mode=%s total=%d pass=%d review=%d block=%d degraded=%d skip=%d request_id=%s",
        mode,
        total,
        counts.get("pass", 0),
        counts.get("review", 0),
        counts.get("block", 0),
        counts.get("degraded", 0),
        counts.get("skip", 0),
        request_id,
    )
