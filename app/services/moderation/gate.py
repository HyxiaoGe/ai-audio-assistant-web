"""审核决策 gate:唯一真值表 + 结构化影子日志。

两个调用点(搜索 / 发布)各调一个场景函数。off 短路不调 CMS;shadow 恒放行只记日志;
enforce 按云判决拦截(block / degraded 一律 fail-closed)。review 恒放行(无人工复审队列)。
"""

from __future__ import annotations

import logging

from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.services.moderation import config
from app.services.moderation.client import ModerationClient, ModerationResult

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
