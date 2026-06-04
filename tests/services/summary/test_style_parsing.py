from __future__ import annotations

import pytest

from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.services.summary.style_parsing import (
    extract_json_object,
    parse_confidence,
    parse_style_payload,
)


def test_extract_json_object_strips_markdown_fence() -> None:
    raw = 'noise before ```json\n{"style": "lecture", "confidence": 0.9}\n``` trailing'
    assert extract_json_object(raw) == {"style": "lecture", "confidence": 0.9}


def test_extract_json_object_raises_when_no_object() -> None:
    with pytest.raises(BusinessError) as exc_info:
        extract_json_object("no json here")
    assert exc_info.value.code == ErrorCode.AI_SUMMARY_GENERATION_FAILED


def test_extract_json_object_raises_on_invalid_json() -> None:
    with pytest.raises(BusinessError) as exc_info:
        extract_json_object("{not valid json}")
    assert exc_info.value.code == ErrorCode.AI_SUMMARY_GENERATION_FAILED


def test_parse_confidence_percentage_is_normalized() -> None:
    assert parse_confidence(88) == 0.88
    assert parse_confidence(70) == 0.7


def test_parse_confidence_clamps_and_rounds() -> None:
    # (1, 100] 一律视为百分比除以 100：1.7 -> 0.017 -> round 0.02（非夹到 1.0）
    assert parse_confidence(1.7) == 0.02
    assert parse_confidence(-0.3) == 0.0
    assert parse_confidence(0.876) == 0.88


def test_parse_confidence_rejects_non_numeric() -> None:
    with pytest.raises(BusinessError) as exc_info:
        parse_confidence("high")
    assert exc_info.value.code == ErrorCode.AI_SUMMARY_GENERATION_FAILED


def test_parse_style_payload_normalizes_deprecated_style() -> None:
    style, confidence, reason = parse_style_payload(
        '{"style": "podcast", "confidence": 0.7, "reason": "对话节目"}',
        locale="zh",
    )
    assert style == "conversation"
    assert confidence == 0.7
    assert reason == "对话节目"


def test_parse_style_payload_falls_back_to_general_for_unknown_style() -> None:
    style, _, _ = parse_style_payload(
        '{"style": "??", "confidence": 0.4, "reason": ""}',
        locale="en",
    )
    assert style == "general"


def test_parse_style_payload_supplies_default_reason() -> None:
    _, _, reason_zh = parse_style_payload('{"style": "lecture", "confidence": 0.9}', locale="zh")
    assert reason_zh == "AI 推荐该摘要风格。"
    _, _, reason_en = parse_style_payload('{"style": "lecture", "confidence": 0.9}', locale="en-US")
    assert reason_en == "AI recommended this summary style."
