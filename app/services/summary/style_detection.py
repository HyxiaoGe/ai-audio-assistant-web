"""Generic, transcript-based summary-style detection.

Used by the summary pipeline when ``summary_style`` is ``auto`` / empty and no
pre-warmed YouTube metadata recommendation is available. Mirrors the YouTube
recommender's ``{style, confidence, reason}`` LLM contract but judges from the
transcript content (local uploads / direct links have no metadata).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from app.config import settings
from app.core.exceptions import BusinessError
from app.core.smart_factory import SmartFactory
from app.services.llm.base import LLMService
from app.services.summary.style_catalog import CANONICAL_STYLES, STYLE_CATALOG
from app.services.summary.style_parsing import parse_style_payload

logger = logging.getLogger(__name__)

# Transcript excerpt size used as the detection basis (chars). The style is a
# coarse, whole-content property; the opening is plenty and keeps token cost low.
_TRANSCRIPT_EXCERPT_LIMIT = 2000


@dataclass(frozen=True)
class StyleDetectionResult:
    style: str
    confidence: float
    reason: str


def _style_catalog_text(locale: str) -> str:
    lang = "zh" if locale.startswith("zh") else "en"
    return "\n".join(f"- {style}: {STYLE_CATALOG[style][lang]}" for style in CANONICAL_STYLES)


def _build_messages(*, transcript_excerpt: str, title: str | None, locale: str) -> list[dict[str, str]]:
    reason_language = "Chinese" if locale.startswith("zh") else "English"
    system = (
        "You classify the content style of a transcript so a downstream summary "
        "can use the right structure. Judge from the content itself; do not summarize "
        "the transcript. Choose exactly one allowed style id. Return valid JSON only. "
        "据内容判定其内容风格，不要总结转写内容。"
    )
    payload = {
        "allowed_styles": _style_catalog_text(locale),
        "title": (title or "").strip()[:300],
        "transcript_excerpt": transcript_excerpt,
        "output_schema": {
            "style": f"one of: {', '.join(CANONICAL_STYLES)}",
            "confidence": "number from 0 to 1",
            "reason": f"short reason in {reason_language}",
        },
    }
    user = (
        "Classify the content style from the title (if any) and the transcript excerpt below. "
        "Return exactly one JSON object matching the schema, with no markdown or extra text.\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def _get_detection_llm_service(user_id: str) -> LLMService:
    return await SmartFactory.get_service(
        "llm",
        provider="proxy",
        model_id=settings.LITELLM_MODEL,
        user_id=user_id,
    )


async def detect_summary_style(
    *,
    transcript: str,
    title: str | None,
    locale: str,
    user_id: str,
    llm_service: LLMService | None = None,
) -> StyleDetectionResult:
    """Detect the canonical summary style for a transcript.

    Returns a result whose ``style`` is always one of the 7 canonical keys.
    Empty transcript, LLM failure, or unparseable output all degrade to
    ``general`` (confidence 0.0) rather than raising — style detection must never
    break the summary pipeline.
    """
    excerpt = (transcript or "").strip()[:_TRANSCRIPT_EXCERPT_LIMIT]
    if not excerpt:
        return StyleDetectionResult(style="general", confidence=0.0, reason="")

    llm = llm_service or await _get_detection_llm_service(user_id)
    messages = _build_messages(transcript_excerpt=excerpt, title=title, locale=locale)
    try:
        raw = await llm.chat(messages, max_tokens=300, temperature=0.2)
        style, confidence, reason = parse_style_payload(raw, locale)
        return StyleDetectionResult(style=style, confidence=confidence, reason=reason)
    except BusinessError as exc:
        logger.warning("Summary style detection failed to parse LLM output, defaulting to general: %s", exc.code)
        return StyleDetectionResult(style="general", confidence=0.0, reason="")
    except Exception:
        logger.exception("Summary style detection LLM call failed, defaulting to general")
        return StyleDetectionResult(style="general", confidence=0.0, reason="")
