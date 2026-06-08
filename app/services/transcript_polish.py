"""转写润色服务 - 利用 LLM 纠正 ASR 转写错误

设计要点：
- 按时间窗口分组（~180秒），让 LLM 看到上下文处理跨段错误
- 严格要求 LLM 逐段返回，段数不变
- 解析失败时回退到原文，绝不丢数据
- 使用 chat() 方法，兼容所有已集成的 LLM provider
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

from loguru import logger

from app.config import settings
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
    segments: list[dict[str, Any]],
    window_seconds: float = 180.0,
    max_per_group: int = 50,
) -> list[list[dict[str, Any]]]:
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

    groups: list[list[dict[str, Any]]] = []
    current_group: list[dict[str, Any]] = []
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


def build_polish_user_prompt(segments: list[dict[str, Any]]) -> str:
    """构建 user prompt，逐段编号。"""
    lines = [f"[{seg['sequence']}] {seg['content']}" for seg in segments]
    return "请校对以下 ASR 转写文本，逐段修正错误：\n\n" + "\n".join(lines)


# ============================================================
# 结果解析
# ============================================================

_RESULT_PATTERN = re.compile(r"\[(\d+)\]\s*(.*)")


def parse_polish_response(
    response: str,
    original_segments: list[dict[str, Any]],
) -> list[PolishResult]:
    """解析 LLM 返回的润色结果。

    期望格式：每行 [序号] 内容。
    解析失败的段回退到原文。
    """
    # 解析 LLM 输出
    parsed: dict[int, str] = {}
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
    results: list[PolishResult] = []
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


def _fallback_to_original(group: list[dict[str, Any]]) -> list[PolishResult]:
    """整组回退原文：该组每段产出 polished=original / changed=False，绝不丢数据。"""
    return [
        PolishResult(
            sequence=seg["sequence"],
            original_content=seg["content"],
            polished_content=seg["content"],
            changed=False,
        )
        for seg in group
    ]


async def _polish_one_group(
    llm_service: LLMService,
    group: list[dict[str, Any]],
    group_idx: int,
    total_groups: int,
    sem: asyncio.Semaphore,
) -> list[PolishResult]:
    """润色单个分组。协程内吞掉所有异常并回退原文，绝不向上抛——

    这样并发收集时任一组失败（51102 空返回 / 超时 / 熔断 OPEN）都不会取消同批其它组，
    与原串行实现「单组失败不影响其他组」语义 1:1 一致。
    """
    user_prompt = build_polish_user_prompt(group)

    messages = [
        {"role": "system", "content": POLISH_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    # 润色输出长度≈输入，按 user_prompt 长度估内容预算，再叠加 ~2000 token 给
    # deepseek-chat 经代理产出的 reasoning_content（推理链与正文共享同一 max_tokens
    # 预算）。原先仅 len*2、小分组会贴边给值 → 推理把额度吃满 → 返回空 → 整组回退
    # 原文丢润色。下限 2048 保底，上限 12000 锁在代理实测放行区间内。
    # 逐组按本组 prompt 长度独立计算，并发不改变单组预算。
    content_budget = max(len(user_prompt) * 2, 2048)
    max_tokens = min(content_budget + 2000, 12000)

    try:
        # 信号量只护住 IO（chat 调用），分组/拼 prompt 是纯 CPU 留在锁外，
        # 使有界并发尽快释放槽位给下一组。
        async with sem:
            response: str = await llm_service.chat(
                messages,
                temperature=0.3,
                max_tokens=max_tokens,
            )

        group_results = parse_polish_response(response, group)
        changed_in_group = sum(1 for r in group_results if r.changed)
        logger.info(
            "Polish group {}/{}: {}/{} segments changed",
            group_idx,
            total_groups,
            changed_in_group,
            len(group),
        )
        return group_results

    except Exception as exc:
        # 单组失败不影响其他组，该组全部回退到原文。
        # 用 opt(exception=exc) 把完整堆栈记进日志，便于定位是 51102 空返回 / 截断 / 超时 / 熔断。
        logger.opt(exception=exc).warning(
            "Polish group {}/{} failed, falling back to original",
            group_idx,
            total_groups,
        )
        return _fallback_to_original(group)


async def polish_transcripts(
    llm_service: LLMService,
    segments: list[dict[str, Any]],
    window_seconds: float = 180.0,
    *,
    max_concurrency: int | None = None,
) -> list[PolishResult]:
    """对转写片段进行 LLM 润色（有界并发）。

    各分组互相独立（独立 prompt、独立 max_tokens、独立失败回退），用 asyncio.Semaphore
    限制并发的 LLM 调用数，再用 gather 收集。相比原串行实现，长任务（如 23 组）耗时从
    ~8min 压到 ~1-2min；并发上限有意低于 proxy_llm 熔断阈值（failure_threshold=5），
    避免偶发空返回在同一窗口扎堆把熔断打 OPEN、连累紧随其后同走 proxy_llm 的摘要生成。

    Args:
        llm_service: LLM 服务实例（需实现 chat 方法）
        segments: 转写数据列表，每项需包含:
            - sequence (int): 序号
            - content (str): 文本内容
            - start_time (float): 开始时间
            - end_time (float): 结束时间
        window_seconds: 分组时间窗口（秒）
        max_concurrency: 并发的 LLM 调用上限；None 时取 settings.POLISH_CONCURRENCY。
            调用点（process_audio/process_youtube）走默认即可，仅测试需显式传入。

    Returns:
        所有片段的润色结果列表，顺序与输入 segments 严格一致。
    """
    if not segments:
        return []

    # 每组上限取自配置：下调段数让单组调用稳定落在 proxy 的 120s 读超时内，免去静默超时重试。
    max_per_group = max(1, settings.POLISH_MAX_SEGMENTS_PER_GROUP)
    groups = group_segments_by_time(segments, window_seconds, max_per_group=max_per_group)

    concurrency = max_concurrency if max_concurrency is not None else settings.POLISH_CONCURRENCY
    concurrency = max(1, concurrency)

    # loguru 用 {} 占位（非 %），此前写成 %d/%s → 参数被丢弃、异常根本没记进日志。
    logger.info(
        "Polish: {} segments split into {} groups (window={}s, concurrency={})",
        len(segments),
        len(groups),
        int(window_seconds),
        concurrency,
    )

    # Semaphore 必须在函数体内（即随本次调用所在的事件循环）创建：process_audio 经
    # await 在已运行的 loop 内调用，process_youtube 经 asyncio.run 每次新建 loop——
    # 模块级 Semaphore 会绑定到首次创建时的 loop，在 youtube 的新 loop 上会抛
    # "bound to a different event loop"。
    sem = asyncio.Semaphore(concurrency)

    # gather 严格按入参顺序返回结果（与完成先后无关），入参又按 groups 顺序排列，
    # 故 group_outputs[i] 必然对应 groups[i]，组顺序 1:1 保留；组内顺序由
    # parse_polish_response 对 original_segments 保序遍历保证。
    # 协程内部已吞净所有 Exception 不向上抛，return_exceptions=True 仅作极端漏网兜底。
    group_outputs = await asyncio.gather(
        *(_polish_one_group(llm_service, group, idx, len(groups), sem) for idx, group in enumerate(groups, start=1)),
        return_exceptions=True,
    )

    all_results: list[PolishResult] = []
    for group, output in zip(groups, group_outputs, strict=True):
        if isinstance(output, BaseException):
            # 协程理论上不会抛（已 except Exception 回退），此处仅兜极端漏网（如
            # CancelledError 等 BaseException 子类），仍回退原文不丢数据。
            logger.opt(exception=output if isinstance(output, Exception) else None).warning(
                "Polish group raised unexpectedly, falling back to original"
            )
            all_results.extend(_fallback_to_original(group))
        else:
            all_results.extend(output)

    return all_results
