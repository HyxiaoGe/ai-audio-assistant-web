"""转写润色服务 - 利用 LLM 纠正 ASR 转写错误

设计要点：
- 按时间窗口分组（~180秒），让 LLM 看到上下文处理跨段错误
- 严格要求 LLM 逐段返回，段数不变
- 解析失败时回退到原文，绝不丢数据
- 使用 chat() 方法，兼容所有已集成的 LLM provider
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List

from loguru import logger

from app.services.llm.base import LLMService

# ============================================================
# 数据结构
# ============================================================


@dataclass
class PolishResult:
    """单个片段的润色结果"""

    sequence: int
    original_content: str
    polished_content: str
    changed: bool


# ============================================================
# System Prompt（硬编码，内容稳定不需要 PromptHub）
# ============================================================

POLISH_SYSTEM_PROMPT = """你是一个专业的语音转写校对助手。你的任务是修正 ASR（语音识别）转写文本中的错误。

修正范围：
1. 错别字和同音字错误：如"论魂"→"论文"，"战场边"→"沾点边"
2. 英文术语识别错误：如"open、I"→"OpenAI"，"LL wl"→根据上下文判断正确内容
3. 中英混杂识别错误：如"A震"→"AI的"
4. 明显的多余字或漏字
5. 冗余语气词：如果整段只有"嗯"、"呃"、"那个"等语气词且无实质内容，将内容替换为空字符串""

严格规则：
- 保持原文的语言和字体：简体中文保持简体，繁体保持繁体，不要做简繁转换
- 输出段数必须与输入完全一致，一一对应
- 不要合并段落，不要拆分段落，不要调换顺序
- 不要改变原意，不要添加原文没有的内容
- 保持说话风格（口语保持口语）
- 如果某段没有错误，原样输出
- 每行格式：[序号] 修正后的内容
- 只输出修正结果，不要输出任何解释或说明"""


# ============================================================
# 分组策略
# ============================================================


def group_segments_by_time(
    segments: List[Dict[str, Any]],
    window_seconds: float = 180.0,
    max_per_group: int = 50,
) -> List[List[Dict[str, Any]]]:
    """按时间窗口分组，让 LLM 看到上下文。

    Args:
        segments: 片段列表，每项需包含 sequence, content, start_time, end_time
        window_seconds: 时间窗口大小（秒），默认 3 分钟
        max_per_group: 每组最大片段数

    Returns:
        分组后的片段列表
    """
    if not segments:
        return []

    groups: List[List[Dict[str, Any]]] = []
    current_group: List[Dict[str, Any]] = []
    group_start: float = segments[0]["start_time"]

    for seg in segments:
        elapsed = seg["start_time"] - group_start
        if current_group and (elapsed >= window_seconds or len(current_group) >= max_per_group):
            groups.append(current_group)
            current_group = [seg]
            group_start = seg["start_time"]
        else:
            current_group.append(seg)

    if current_group:
        groups.append(current_group)

    return groups


# ============================================================
# Prompt 构建
# ============================================================


def build_polish_user_prompt(segments: List[Dict[str, Any]]) -> str:
    """构建 user prompt，逐段编号。"""
    lines = [f"[{seg['sequence']}] {seg['content']}" for seg in segments]
    return "请校对以下 ASR 转写文本，逐段修正错误：\n\n" + "\n".join(lines)


# ============================================================
# 结果解析
# ============================================================

_RESULT_PATTERN = re.compile(r"\[(\d+)\]\s*(.*)")


def parse_polish_response(
    response: str,
    original_segments: List[Dict[str, Any]],
) -> List[PolishResult]:
    """解析 LLM 返回的润色结果。

    期望格式：每行 [序号] 内容。
    解析失败的段回退到原文。
    """
    # 解析 LLM 输出
    parsed: Dict[int, str] = {}
    for line in response.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        match = _RESULT_PATTERN.match(line)
        if match:
            seq = int(match.group(1))
            content = match.group(2).strip()
            parsed[seq] = content

    # 逐段匹配
    results: List[PolishResult] = []
    for seg in original_segments:
        seq = seg["sequence"]
        original = seg["content"]

        if seq in parsed:
            polished = parsed[seq]
            # 空字符串表示纯语气词段，保留原文避免数据丢失
            if not polished:
                polished = original
            changed = polished != original
        else:
            # LLM 没返回这一段，保持原文
            polished = original
            changed = False

        results.append(
            PolishResult(
                sequence=seq,
                original_content=original,
                polished_content=polished,
                changed=changed,
            )
        )

    return results


# ============================================================
# 主入口
# ============================================================


async def polish_transcripts(
    llm_service: LLMService,
    segments: List[Dict[str, Any]],
    window_seconds: float = 180.0,
) -> List[PolishResult]:
    """对转写片段进行 LLM 润色。

    Args:
        llm_service: LLM 服务实例（需实现 chat 方法）
        segments: 转写数据列表，每项需包含:
            - sequence (int): 序号
            - content (str): 文本内容
            - start_time (float): 开始时间
            - end_time (float): 结束时间
        window_seconds: 分组时间窗口（秒）

    Returns:
        所有片段的润色结果列表
    """
    if not segments:
        return []

    groups = group_segments_by_time(segments, window_seconds)
    all_results: List[PolishResult] = []

    logger.info(
        "Polish: %d segments split into %d groups (window=%ds)",
        len(segments),
        len(groups),
        int(window_seconds),
    )

    for group_idx, group in enumerate(groups, start=1):
        user_prompt = build_polish_user_prompt(group)

        messages = [
            {"role": "system", "content": POLISH_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        try:
            response: str = await llm_service.chat(
                messages,
                temperature=0.3,
                max_tokens=len(user_prompt) * 2,
            )

            group_results = parse_polish_response(response, group)
            all_results.extend(group_results)

            changed_in_group = sum(1 for r in group_results if r.changed)
            logger.info(
                "Polish group %d/%d: %d/%d segments changed",
                group_idx,
                len(groups),
                changed_in_group,
                len(group),
            )

        except Exception as exc:
            # 单组失败不影响其他组，该组全部回退到原文
            logger.warning(
                "Polish group %d/%d failed, falling back to original: %s",
                group_idx,
                len(groups),
                exc,
            )
            for seg in group:
                all_results.append(
                    PolishResult(
                        sequence=seg["sequence"],
                        original_content=seg["content"],
                        polished_content=seg["content"],
                        changed=False,
                    )
                )

    return all_results
