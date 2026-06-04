"""Summary style recommendation for cached YouTube videos."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import BusinessError
from app.core.smart_factory import SmartFactory
from app.i18n.codes import ErrorCode
from app.models.youtube_subscription import YouTubeSubscription
from app.models.youtube_summary_style_recommendation import YouTubeSummaryStyleRecommendation
from app.models.youtube_video import YouTubeVideo
from app.services.llm.base import LLMService
from app.services.summary.style_catalog import CANONICAL_STYLES, STYLE_CATALOG
from app.services.summary.style_parsing import parse_style_payload

logger = logging.getLogger(__name__)

ALGORITHM_VERSION = "summary-style-llm-v1"

ALLOWED_STYLES = CANONICAL_STYLES


@dataclass(frozen=True)
class SummaryStyleRecommendationResult:
    style: str
    confidence: float
    reason: str
    cached: bool = False


def build_video_metadata_hash(
    *,
    title: str,
    description: str | None,
    channel_title: str | None,
    duration_seconds: int | None,
) -> str:
    payload = {
        "title": title,
        "description": description or "",
        "channel_title": channel_title or "",
        "duration_seconds": duration_seconds,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _trim_text(value: str | None, limit: int) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _duration_label(duration_seconds: int | None) -> str | None:
    if duration_seconds is None:
        return None
    hours, remainder = divmod(duration_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _style_catalog_text(locale: str) -> str:
    lang = "zh" if locale.startswith("zh") else "en"
    return "\n".join(f"- {style}: {details[lang]}" for style, details in STYLE_CATALOG.items())


def _build_llm_messages(
    *,
    title: str,
    description: str | None,
    channel_title: str | None,
    duration_seconds: int | None,
    locale: str = "zh",
) -> list[dict[str, str]]:
    reason_language = "Chinese" if locale.startswith("zh") else "English"
    system = (
        "You recommend the best summary style for a YouTube video before transcription. "
        "Use only the provided metadata; do not summarize the video. "
        "Choose exactly one allowed style id. Return valid JSON only."
    )
    payload = {
        "allowed_styles": _style_catalog_text(locale),
        "video_metadata": {
            "title": _trim_text(title, 300),
            "description": _trim_text(description, 1500),
            "channel_title": _trim_text(channel_title, 120),
            "duration_seconds": duration_seconds,
            "duration_label": _duration_label(duration_seconds),
        },
        "output_schema": {
            "style": f"one of: {', '.join(ALLOWED_STYLES)}",
            "confidence": "number from 0 to 1",
            "reason": f"short reason in {reason_language}",
        },
    }
    user = (
        "Recommend the most appropriate summary style for this video metadata. "
        "Return exactly one JSON object matching the schema, with no markdown or extra text.\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _parse_llm_recommendation(raw: str, locale: str) -> SummaryStyleRecommendationResult:
    style, confidence, reason = parse_style_payload(raw, locale)
    return SummaryStyleRecommendationResult(
        style=style,
        confidence=confidence,
        reason=reason,
        cached=False,
    )


async def _get_summary_style_llm_service(user_id: str) -> LLMService:
    return await SmartFactory.get_service(
        "llm",
        provider="proxy",
        model_id=settings.LITELLM_MODEL,
        user_id=user_id,
    )


async def _recommend_style_with_llm(
    *,
    llm_service: LLMService,
    title: str,
    description: str | None,
    channel_title: str | None,
    duration_seconds: int | None,
    locale: str,
) -> SummaryStyleRecommendationResult:
    messages = _build_llm_messages(
        title=title,
        description=description,
        channel_title=channel_title,
        duration_seconds=duration_seconds,
        locale=locale,
    )
    # 与 style_detection 同源：deepseek-chat 经代理会先产出 reasoning_content，与正文共享
    # max_tokens；300 偏薄时推理链偶发吃满额度挤掉正文 → 空返回 → 误兜底。分类输出本身很小，
    # 放大到 2048 只为给推理链留余量（上限非预付，按实际生成计费）。
    raw = await llm_service.chat(messages, max_tokens=2048, temperature=0.2)
    return _parse_llm_recommendation(raw, locale)


async def _get_video(db: AsyncSession, user_id: str, video_id: str) -> YouTubeVideo | None:
    result = await db.execute(
        select(YouTubeVideo).where(
            YouTubeVideo.user_id == user_id,
            YouTubeVideo.video_id == video_id,
        )
    )
    return result.scalar_one_or_none()


async def _get_subscription(db: AsyncSession, video: YouTubeVideo) -> YouTubeSubscription | None:
    result = await db.execute(
        select(YouTubeSubscription).where(
            YouTubeSubscription.id == video.subscription_id,
            YouTubeSubscription.user_id == video.user_id,
        )
    )
    return result.scalar_one_or_none()


async def _get_cached_recommendation(
    db: AsyncSession,
    *,
    user_id: str,
    video_id: str,
    metadata_hash: str,
) -> YouTubeSummaryStyleRecommendation | None:
    result = await db.execute(
        select(YouTubeSummaryStyleRecommendation).where(
            YouTubeSummaryStyleRecommendation.user_id == user_id,
            YouTubeSummaryStyleRecommendation.video_id == video_id,
            YouTubeSummaryStyleRecommendation.metadata_hash == metadata_hash,
            YouTubeSummaryStyleRecommendation.algorithm_version == ALGORITHM_VERSION,
        )
    )
    return result.scalar_one_or_none()


async def recommend_summary_style_for_video(
    db: AsyncSession,
    user_id: str,
    video_id: str,
    *,
    locale: str = "zh",
    llm_service: LLMService | None = None,
) -> SummaryStyleRecommendationResult:
    video = await _get_video(db, user_id, video_id)
    if not video:
        raise BusinessError(ErrorCode.YOUTUBE_VIDEO_NOT_FOUND, reason=f"Video {video_id} not found in cache")

    subscription = await _get_subscription(db, video)
    channel_title = subscription.channel_title if subscription else None
    metadata_hash = build_video_metadata_hash(
        title=video.title,
        description=video.description,
        channel_title=channel_title,
        duration_seconds=video.duration_seconds,
    )

    cached = await _get_cached_recommendation(
        db,
        user_id=user_id,
        video_id=video_id,
        metadata_hash=metadata_hash,
    )
    if cached:
        return SummaryStyleRecommendationResult(
            style=cached.style,
            confidence=cached.confidence,
            reason=cached.reason,
            cached=True,
        )

    llm = llm_service or await _get_summary_style_llm_service(user_id)
    recommendation = await _recommend_style_with_llm(
        llm_service=llm,
        title=video.title,
        description=video.description,
        channel_title=channel_title,
        duration_seconds=video.duration_seconds,
        locale=locale,
    )
    db.add(
        YouTubeSummaryStyleRecommendation(
            user_id=user_id,
            video_id=video_id,
            metadata_hash=metadata_hash,
            algorithm_version=ALGORITHM_VERSION,
            style=recommendation.style,
            confidence=recommendation.confidence,
            reason=recommendation.reason,
        )
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        cached = await _get_cached_recommendation(
            db,
            user_id=user_id,
            video_id=video_id,
            metadata_hash=metadata_hash,
        )
        if cached:
            return SummaryStyleRecommendationResult(
                style=cached.style,
                confidence=cached.confidence,
                reason=cached.reason,
                cached=True,
            )
        raise
    return recommendation


async def prewarm_summary_styles_for_videos(
    db: AsyncSession,
    user_id: str,
    video_ids: list[str],
    *,
    locale: str = "zh",
    limit: int = 20,
    llm_service: LLMService | None = None,
) -> dict[str, int]:
    """Prewarm summary style recommendations for a bounded set of cached videos."""
    unique_video_ids: list[str] = []
    seen: set[str] = set()
    for video_id in video_ids:
        clean_video_id = str(video_id or "").strip()
        if not clean_video_id or clean_video_id in seen:
            continue
        seen.add(clean_video_id)
        unique_video_ids.append(clean_video_id)
        if len(unique_video_ids) >= limit:
            break

    stats = {
        "requested_count": len(video_ids),
        "queued_count": len(unique_video_ids),
        "generated_count": 0,
        "cached_count": 0,
        "failed_count": 0,
    }
    if not unique_video_ids:
        return stats

    llm = llm_service or await _get_summary_style_llm_service(user_id)
    for video_id in unique_video_ids:
        try:
            recommendation = await recommend_summary_style_for_video(
                db,
                user_id,
                video_id,
                locale=locale,
                llm_service=llm,
            )
            if recommendation.cached:
                stats["cached_count"] += 1
            else:
                stats["generated_count"] += 1
        except BusinessError as exc:
            stats["failed_count"] += 1
            logger.warning(
                "Failed to prewarm summary style recommendation for video %s: %s",
                video_id,
                exc.code,
            )
        except Exception:
            stats["failed_count"] += 1
            logger.exception("Unexpected error prewarming summary style recommendation for video %s", video_id)

    return stats
