"""Tests for transcript polish service."""

from __future__ import annotations

import asyncio
import re
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

# Mock the 'auth' package
if "auth" not in sys.modules:
    _auth_mock = ModuleType("auth")
    _auth_mock.AuthenticatedUser = MagicMock  # type: ignore[attr-defined]
    _auth_mock.JWTValidator = MagicMock  # type: ignore[attr-defined]
    sys.modules["auth"] = _auth_mock

import pytest  # noqa: E402

from app.services.transcript_polish import (  # noqa: E402
    build_polish_user_prompt,
    group_segments_by_time,
    parse_polish_response,
    polish_transcripts,
)


def _seg(seq: int, content: str, start: float, end: float) -> dict:
    return {"sequence": seq, "content": content, "start_time": start, "end_time": end}


class TestGroupSegmentsByTime:
    def test_empty(self):
        assert group_segments_by_time([]) == []

    def test_single_group(self):
        segs = [_seg(1, "a", 0, 5), _seg(2, "b", 5, 10), _seg(3, "c", 10, 15)]
        groups = group_segments_by_time(segs, window_seconds=180)
        assert len(groups) == 1
        assert len(groups[0]) == 3

    def test_splits_by_time(self):
        segs = [_seg(1, "a", 0, 5), _seg(2, "b", 100, 105), _seg(3, "c", 200, 205)]
        groups = group_segments_by_time(segs, window_seconds=90)
        assert len(groups) == 3

    def test_splits_by_max_per_group(self):
        segs = [_seg(i, f"s{i}", i * 2.0, i * 2.0 + 1) for i in range(1, 8)]
        groups = group_segments_by_time(segs, window_seconds=9999, max_per_group=3)
        assert len(groups) == 3
        assert len(groups[0]) == 3
        assert len(groups[1]) == 3
        assert len(groups[2]) == 1


class TestBuildPolishUserPrompt:
    def test_format(self):
        segs = [_seg(1, "你好", 0, 1), _seg(2, "世界", 1, 2)]
        prompt = build_polish_user_prompt(segs)
        assert "[1] 你好" in prompt
        assert "[2] 世界" in prompt


class TestParsePolishResponse:
    def test_exact_match(self):
        segs = [_seg(1, "论魂", 0, 1), _seg(2, "ok", 1, 2)]
        response = "[1] 论文\n[2] ok"
        results = parse_polish_response(response, segs)
        assert len(results) == 2
        assert results[0].changed is True
        assert results[0].polished_content == "论文"
        assert results[0].original_content == "论魂"
        assert results[1].changed is False

    def test_missing_segment_falls_back(self):
        segs = [_seg(1, "a", 0, 1), _seg(2, "b", 1, 2)]
        response = "[1] a"
        results = parse_polish_response(response, segs)
        assert results[1].changed is False
        assert results[1].polished_content == "b"

    def test_empty_response_preserves_original(self):
        segs = [_seg(1, "嗯", 0, 1)]
        response = "[1] "
        results = parse_polish_response(response, segs)
        # Empty polished content should preserve original
        assert results[0].polished_content == "嗯"
        assert results[0].changed is False

    def test_handles_malformed_lines(self):
        segs = [_seg(1, "hello", 0, 1)]
        response = "some garbage\n[1] hello fixed\nmore garbage"
        results = parse_polish_response(response, segs)
        assert results[0].polished_content == "hello fixed"
        assert results[0].changed is True


@pytest.mark.asyncio
async def test_polish_transcripts_success():
    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock(return_value="[1] 论文\n[2] OpenAI\n[3] ok")

    segs = [
        _seg(1, "论魂", 0, 5),
        _seg(2, "open、I", 5, 10),
        _seg(3, "ok", 10, 15),
    ]

    results = await polish_transcripts(mock_llm, segs)
    assert len(results) == 3
    assert results[0].polished_content == "论文"
    assert results[1].polished_content == "OpenAI"
    assert results[2].changed is False


@pytest.mark.asyncio
async def test_polish_transcripts_max_tokens_reserves_reasoning_headroom():
    """max_tokens 必须给 reasoning_content 留足余量。

    deepseek-chat 经代理会先产出 reasoning_content（推理链），与正文共享同一
    max_tokens 预算。原先用 len(user_prompt)*2，小分组会贴边给值 → 推理吃满 →
    返回空 → 整组回退原文丢润色。现固定为「内容预算(下限 2048) + 2000 推理余量，
    上限 12000」：小分组应得 2048 + 2000 = 4048。
    """

    class _CaptureLLM:
        def __init__(self) -> None:
            self.kwargs: dict = {}

        async def chat(self, messages: list[dict], **kwargs) -> str:
            self.kwargs = kwargs
            return "[1] 短\n[2] 文\n[3] 本"

    llm = _CaptureLLM()
    segs = [_seg(1, "短", 0, 5), _seg(2, "文", 5, 10), _seg(3, "本", 10, 15)]
    await polish_transcripts(llm, segs)

    assert llm.kwargs["max_tokens"] == 4048
    assert llm.kwargs["temperature"] == 0.3


@pytest.mark.asyncio
async def test_polish_transcripts_llm_failure_graceful():
    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock(side_effect=RuntimeError("LLM down"))

    segs = [_seg(1, "hello", 0, 5)]
    results = await polish_transcripts(mock_llm, segs)
    # Should fall back to original
    assert len(results) == 1
    assert results[0].polished_content == "hello"
    assert results[0].changed is False


# ============================================================
# 并发润色（有界并发 + gather）相关
# ============================================================


def _seqs_in_prompt(messages: list[dict]) -> list[int]:
    """从 user prompt 里抽出本组涉及的段序号（构造的响应据此逐段回填）。"""
    user = messages[-1]["content"]
    return [int(m) for m in re.findall(r"\[(\d+)\]", user)]


def _five_groups_one_seg_each() -> list[dict]:
    """start_time 间隔 200s、默认窗口 180s → 切成 5 组、每组 1 段（seq 1..5）。"""
    return [_seg(i, f"seg{i}", (i - 1) * 200.0, (i - 1) * 200.0 + 5) for i in range(1, 6)]


@pytest.mark.asyncio
async def test_polish_transcripts_preserves_order_despite_completion_order():
    """并发完成顺序与组顺序相反，最终结果仍须严格按输入顺序（gather 保序）。"""

    class _ReorderingLLM:
        async def chat(self, messages: list[dict], **kwargs) -> str:
            seqs = _seqs_in_prompt(messages)
            # 越靠前（seq 越小）睡越久 → 越晚完成，制造与组序相反的完成顺序。
            await asyncio.sleep(0.02 * (6 - seqs[0]))
            return "\n".join(f"[{s}] polished{s}" for s in seqs)

    segs = _five_groups_one_seg_each()
    results = await polish_transcripts(_ReorderingLLM(), segs, max_concurrency=3)

    assert [r.sequence for r in results] == [1, 2, 3, 4, 5]
    assert [r.polished_content for r in results] == [f"polished{i}" for i in range(1, 6)]
    assert all(r.changed for r in results)


@pytest.mark.asyncio
async def test_polish_transcripts_one_group_failure_does_not_affect_others():
    """单组失败（模拟 51102 空返回抛错）只回退该组原文，不连累其它组、不抛出。"""

    class _OneGroupFailsLLM:
        async def chat(self, messages: list[dict], **kwargs) -> str:
            seqs = _seqs_in_prompt(messages)
            if 2 in seqs:
                raise RuntimeError("simulated empty return (51102)")
            return "\n".join(f"[{s}] polished{s}" for s in seqs)

    segs = [_seg(1, "a", 0, 5), _seg(2, "b", 200, 205), _seg(3, "c", 400, 405)]
    results = await polish_transcripts(_OneGroupFailsLLM(), segs, max_concurrency=3)

    by_seq = {r.sequence: r for r in results}
    assert len(results) == 3
    # 失败组回退原文
    assert by_seq[2].polished_content == "b"
    assert by_seq[2].changed is False
    # 其它组正常润色
    assert by_seq[1].polished_content == "polished1"
    assert by_seq[1].changed is True
    assert by_seq[3].polished_content == "polished3"
    assert by_seq[3].changed is True
    # 顺序仍严格按输入
    assert [r.sequence for r in results] == [1, 2, 3]


@pytest.mark.asyncio
async def test_polish_transcripts_respects_concurrency_limit():
    """在途并发的 chat 调用数峰值不得超过 max_concurrency（证明 Semaphore 真生效）。"""

    class _ConcurrencyTrackingLLM:
        def __init__(self) -> None:
            self.current = 0
            self.peak = 0

        async def chat(self, messages: list[dict], **kwargs) -> str:
            self.current += 1
            self.peak = max(self.peak, self.current)
            try:
                # 持槽一会儿，让多组有机会重叠，从而暴露真实峰值。
                await asyncio.sleep(0.02)
            finally:
                self.current -= 1
            seqs = _seqs_in_prompt(messages)
            return "\n".join(f"[{s}] polished{s}" for s in seqs)

    llm = _ConcurrencyTrackingLLM()
    segs = _five_groups_one_seg_each()  # 5 组
    results = await polish_transcripts(llm, segs, max_concurrency=2)

    assert len(results) == 5
    assert llm.peak <= 2  # 5 组但峰值被信号量压在 2
    assert llm.peak >= 2  # 且确实并发起来了（非退化为串行）


@pytest.mark.asyncio
async def test_polish_transcripts_all_groups_fail_all_fallback():
    """所有组都失败时，全量回退原文、段数不变、不向上抛异常（整段 polish 不挂）。"""
    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock(side_effect=RuntimeError("LLM down"))

    segs = [_seg(1, "a", 0, 5), _seg(2, "b", 200, 205), _seg(3, "c", 400, 405)]
    results = await polish_transcripts(mock_llm, segs, max_concurrency=3)

    assert len(results) == 3
    assert [r.sequence for r in results] == [1, 2, 3]
    assert [r.polished_content for r in results] == ["a", "b", "c"]
    assert all(r.changed is False for r in results)
