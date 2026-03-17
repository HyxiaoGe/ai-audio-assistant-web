"""Tests for transcript polish service."""
from __future__ import annotations

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
    PolishResult,
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
async def test_polish_transcripts_llm_failure_graceful():
    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock(side_effect=RuntimeError("LLM down"))

    segs = [_seg(1, "hello", 0, 5)]
    results = await polish_transcripts(mock_llm, segs)
    # Should fall back to original
    assert len(results) == 1
    assert results[0].polished_content == "hello"
    assert results[0].changed is False
