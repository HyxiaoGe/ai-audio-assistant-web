"""Resolve the canonical content style for a summary run.

Single decision point shared by the upload (process_audio) and YouTube
(process_youtube) pipelines:

- explicit user style  -> normalize_content_style(user value)
- ``auto`` / empty     -> use a pre-recommended style if supplied (YouTube
  metadata pre-warm), else run transcript-based detection.

Always returns one of the 7 canonical keys.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.services.summary.style_catalog import normalize_content_style
from app.services.summary.style_detection import (
    StyleDetectionResult,
    detect_summary_style,
)

AUTO_STYLE = "auto"

# Signature of an injectable detector (defaults to detect_summary_style).
DetectorType = Callable[..., Awaitable[StyleDetectionResult]]


def is_auto_style(requested_style: str | None) -> bool:
    """True when the request asks for automatic detection (``auto`` / empty)."""
    return (requested_style or "").strip().lower() in ("", AUTO_STYLE)


async def resolve_content_style(
    *,
    requested_style: str | None,
    transcript: str,
    title: str | None,
    locale: str,
    user_id: str,
    detector: DetectorType | None = None,
    prerecommended_style: str | None = None,
) -> str:
    """Return the canonical content style for this run.

    ``prerecommended_style`` lets the YouTube pipeline pass an already-cached
    metadata recommendation; when present (and the request is auto) it is used
    in preference to re-running transcript detection.
    """
    if not is_auto_style(requested_style):
        return normalize_content_style(requested_style)

    if prerecommended_style:
        return normalize_content_style(prerecommended_style)

    detect = detector or detect_summary_style
    result = await detect(
        transcript=transcript,
        title=title,
        locale=locale,
        user_id=user_id,
    )
    return normalize_content_style(result.style)


def persist_detected_style(
    options: dict[str, Any] | None,
    style: str,
    *,
    auto_detected: bool,
) -> dict[str, Any]:
    """Return a copy of task options with the resolved style written back.

    The resolved style is stored under ``summary_style`` (the same key the rest of
    the pipeline reads), so regenerate / image generation reuse it. A boolean
    source marker ``summary_style_auto_detected`` records whether the value came
    from automatic detection (request was ``auto`` / empty) vs. an explicit user
    choice; the frontend uses it to show "AI detected: X" only for auto runs.

    Returns a NEW dict so callers reassign ``task.options`` and SQLAlchemy detects
    the JSONB change (mutating in place would not be tracked).
    """
    updated = dict(options or {})
    updated["summary_style"] = style
    updated["summary_style_auto_detected"] = auto_detected
    return updated
