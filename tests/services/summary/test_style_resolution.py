from __future__ import annotations

import pytest

from app.services.summary.style_resolution import (
    is_auto_style,
    persist_detected_style,
    resolve_content_style,
)


class _FakeDetector:
    def __init__(self, style: str) -> None:
        self._style = style
        self.called_with: dict | None = None

    async def __call__(self, **kwargs):
        self.called_with = kwargs
        from app.services.summary.style_detection import StyleDetectionResult

        return StyleDetectionResult(style=self._style, confidence=0.9, reason="r")


def test_is_auto_style_true_cases() -> None:
    assert is_auto_style("auto") is True
    assert is_auto_style("AUTO") is True
    assert is_auto_style("") is True
    assert is_auto_style("  ") is True
    assert is_auto_style(None) is True


def test_is_auto_style_false_for_explicit() -> None:
    assert is_auto_style("meeting") is False
    assert is_auto_style("podcast") is False


@pytest.mark.asyncio
async def test_resolve_explicit_style_skips_detection_and_normalizes() -> None:
    detector = _FakeDetector("lecture")
    style = await resolve_content_style(
        requested_style="podcast",
        transcript="x",
        title="t",
        locale="zh",
        user_id="u-1",
        detector=detector,
    )
    assert style == "conversation"  # podcast normalized, no detection
    assert detector.called_with is None


@pytest.mark.asyncio
async def test_resolve_auto_style_runs_detection() -> None:
    detector = _FakeDetector("review")
    style = await resolve_content_style(
        requested_style="auto",
        transcript="评测：这款相机……",
        title=None,
        locale="zh",
        user_id="u-1",
        detector=detector,
    )
    assert style == "review"
    assert detector.called_with["transcript"] == "评测：这款相机……"
    assert detector.called_with["user_id"] == "u-1"


@pytest.mark.asyncio
async def test_resolve_auto_with_prerecommended_style_skips_detection() -> None:
    detector = _FakeDetector("meeting")
    style = await resolve_content_style(
        requested_style="auto",
        transcript="x",
        title=None,
        locale="zh",
        user_id="u-1",
        detector=detector,
        prerecommended_style="podcast",
    )
    assert style == "conversation"  # uses & normalizes pre-recommended, no detection
    assert detector.called_with is None


def test_persist_detected_style_marks_auto_detected_source() -> None:
    """auto 经识别得到 -> 写回 summary_style + summary_style_auto_detected=True，且为新 dict。"""
    options = {"language": "auto", "summary_style": "auto"}
    updated = persist_detected_style(options, "review", auto_detected=True)
    assert updated["summary_style"] == "review"
    assert updated["summary_style_auto_detected"] is True
    assert updated is not options  # 新 dict，触发 JSONB 变更检测
    assert options["summary_style"] == "auto"  # 原 dict 未被原地修改


def test_persist_detected_style_marks_explicit_choice() -> None:
    """用户显式选风格 -> summary_style_auto_detected=False。"""
    updated = persist_detected_style({"summary_style": "meeting"}, "meeting", auto_detected=False)
    assert updated["summary_style"] == "meeting"
    assert updated["summary_style_auto_detected"] is False


def test_persist_detected_style_handles_none_options() -> None:
    updated = persist_detected_style(None, "lecture", auto_detected=True)
    assert updated == {"summary_style": "lecture", "summary_style_auto_detected": True}
