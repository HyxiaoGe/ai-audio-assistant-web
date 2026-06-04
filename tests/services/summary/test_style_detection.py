from __future__ import annotations

from typing import Any

import pytest

from app.services.summary.style_detection import (
    StyleDetectionResult,
    detect_summary_style,
)


class _FakeLLM:
    provider = "proxy"
    model_name = "test-model"

    def __init__(self, *responses: str) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[list[dict[str, str]], dict[str, Any]]] = []

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        self.calls.append((messages, kwargs))
        if not self._responses:
            raise AssertionError("Unexpected LLM call")
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_detect_summary_style_parses_and_normalizes() -> None:
    llm = _FakeLLM('{"style":"podcast","confidence":0.82,"reason":"两位嘉宾对谈"}')
    result = await detect_summary_style(
        transcript="主持人：今天我们请到两位嘉宾聊聊播客创作……",
        title="深夜对谈",
        locale="zh",
        user_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
        llm_service=llm,
    )
    assert isinstance(result, StyleDetectionResult)
    assert result.style == "conversation"
    assert result.confidence == 0.82
    assert result.reason == "两位嘉宾对谈"


@pytest.mark.asyncio
async def test_detect_summary_style_uses_expected_llm_params_and_truncates_transcript() -> None:
    long_transcript = "讲师：" + ("概念A 关系B 推导C。" * 1000)
    llm = _FakeLLM('{"style":"lecture","confidence":0.9,"reason":"知识讲解"}')
    result = await detect_summary_style(
        transcript=long_transcript,
        title=None,
        locale="zh",
        user_id="u-1",
        llm_service=llm,
    )
    assert result.style == "lecture"
    messages, kwargs = llm.calls[0]
    # max_tokens 放大到 2048：给 deepseek-chat 经代理产出的 reasoning_content 留余量，
    # 避免推理链挤掉分类正文导致空返回(51102)误兜底成 general（详见 style_detection 注释）。
    assert kwargs == {"max_tokens": 2048, "temperature": 0.2}
    user_content = messages[-1]["content"]
    assert len(user_content) < len(long_transcript)
    assert "不要总结" in messages[0]["content"] or "do not summarize" in messages[0]["content"].lower()


@pytest.mark.asyncio
async def test_detect_summary_style_unparseable_falls_back_to_general() -> None:
    llm = _FakeLLM("the content seems to be a lecture about physics")
    result = await detect_summary_style(
        transcript="some transcript text",
        title="Physics 101",
        locale="en",
        user_id="u-1",
        llm_service=llm,
    )
    assert result.style == "general"
    assert result.confidence == 0.0


@pytest.mark.asyncio
async def test_detect_summary_style_empty_transcript_returns_general_without_llm() -> None:
    llm = _FakeLLM()  # no responses queued -> would raise if called
    result = await detect_summary_style(
        transcript="   ",
        title=None,
        locale="zh",
        user_id="u-1",
        llm_service=llm,
    )
    assert result.style == "general"
    assert llm.calls == []


@pytest.mark.asyncio
async def test_detect_summary_style_service_acquisition_failure_falls_back_to_general(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 服务**构造**失败(如缺 API key)也须降级 general,绝不打断摘要管线。"""

    async def _boom(user_id: str):
        raise RuntimeError("LITELLM_API_KEY is not set")

    monkeypatch.setattr(
        "app.services.summary.style_detection._get_detection_llm_service", _boom
    )
    result = await detect_summary_style(
        transcript="some transcript text",
        title=None,
        locale="zh",
        user_id="u-1",
        llm_service=None,
    )
    assert result.style == "general"
    assert result.confidence == 0.0
