"""Shared LLM JSON parsing utilities for summary-style detection.

Extracted from app/services/youtube/summary_style_recommendation.py so the
YouTube recommender and the generic transcript-based detector share one
parsing/normalization implementation.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.services.summary.style_catalog import normalize_content_style


def extract_json_object(raw: str) -> dict[str, Any]:
    """Extract the first top-level JSON object from a raw LLM response.

    Tolerant of markdown fences / leading-trailing prose by slicing between the
    first ``{`` and the last ``}``. Raises BusinessError on absence / invalid JSON.

    Limitation: if the response contains multiple sibling objects (e.g.
    ``{...} {...}``), the slice spans both and JSON parsing raises BusinessError.
    """
    text = (raw or "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise BusinessError(
            ErrorCode.AI_SUMMARY_GENERATION_FAILED,
            reason="LLM style response did not contain a JSON object",
        )
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise BusinessError(
            ErrorCode.AI_SUMMARY_GENERATION_FAILED,
            reason="LLM style response was not valid JSON",
        ) from exc
    if not isinstance(parsed, dict):
        raise BusinessError(
            ErrorCode.AI_SUMMARY_GENERATION_FAILED,
            reason="LLM style response JSON was not an object",
        )
    return parsed


def parse_confidence(value: Any) -> float:
    """Coerce a confidence value to a float in [0, 1], rounded to 2 dp.

    Values in (1, 100] are treated as percentages and divided by 100 (e.g. 70 ->
    0.7, 1.7 -> 0.017 -> 0.02); values already in [0, 1] pass through; out-of-range
    results are clamped to [0, 1]. Raises BusinessError if non-numeric.
    """
    try:
        confidence = float(value)
    except (TypeError, ValueError) as exc:
        raise BusinessError(
            ErrorCode.AI_SUMMARY_GENERATION_FAILED,
            reason="LLM style confidence was not numeric",
        ) from exc
    # Treat any value in (1, 100] as a percentage (LLM often returns 70 for 0.7).
    # NOTE: intentional — values like 1.7 are also divided (-> 0.017), not clamped to 1.0.
    if 1 < confidence <= 100:
        confidence = confidence / 100
    return round(min(1.0, max(0.0, confidence)), 2)


def parse_style_payload(raw: str, locale: str) -> tuple[str, float, str]:
    """Parse an LLM ``{style, confidence, reason}`` payload.

    Returns ``(canonical_style, confidence, reason)``. The style is always run
    through ``normalize_content_style`` (deprecated keys mapped, unknown -> general),
    so callers can never receive an out-of-catalog style. Confidence is clamped.
    A locale-appropriate default reason is supplied when the model omits one.
    """
    payload = extract_json_object(raw)
    style = normalize_content_style(str(payload.get("style") or ""))
    confidence = parse_confidence(payload.get("confidence"))
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        reason = "AI 推荐该摘要风格。" if locale.startswith("zh") else "AI recommended this summary style."
    return style, confidence, reason
