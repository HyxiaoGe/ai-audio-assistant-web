"""Summary style recommendation for cached YouTube videos."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.models.youtube_subscription import YouTubeSubscription
from app.models.youtube_summary_style_recommendation import YouTubeSummaryStyleRecommendation
from app.models.youtube_video import YouTubeVideo

ALGORITHM_VERSION = "summary-style-recommender-v1"

TITLE_WEIGHT = 3
DESCRIPTION_WEIGHT = 1
CHANNEL_WEIGHT = 2


@dataclass(frozen=True)
class SummaryStyleRecommendationResult:
    style: str
    confidence: float
    reason: str
    cached: bool = False


@dataclass(frozen=True)
class _StyleRule:
    keywords: tuple[str, ...]
    zh_reason: str
    en_reason: str


STYLE_RULES: dict[str, _StyleRule] = {
    "tutorial": _StyleRule(
        keywords=(
            "how to",
            "tutorial",
            "guide",
            "step by step",
            "walkthrough",
            "setup",
            "tips",
            "教程",
            "指南",
            "入门",
            "实战",
            "步骤",
            "上手",
            "配置",
        ),
        zh_reason="标题或描述呈现教程/步骤特征，适合按操作流程提炼。",
        en_reason="The title or description has tutorial and step-by-step signals.",
    ),
    "review": _StyleRule(
        keywords=(
            "review",
            "vs",
            "versus",
            "comparison",
            "benchmark",
            "hands-on",
            "pros",
            "cons",
            "评测",
            "测评",
            "对比",
            "体验",
            "开箱",
            "优缺点",
        ),
        zh_reason="内容带有评测/对比特征，适合突出优缺点和结论。",
        en_reason="The metadata looks like a review or comparison.",
    ),
    "news": _StyleRule(
        keywords=(
            "news",
            "breaking",
            "announced",
            "release",
            "update",
            "latest",
            "today",
            "新闻",
            "时事",
            "发布",
            "更新",
            "最新",
            "宣布",
            "报道",
        ),
        zh_reason="内容带有新闻/更新特征，适合聚焦事件、数据和各方观点。",
        en_reason="The metadata suggests news, releases, or current updates.",
    ),
    "interview": _StyleRule(
        keywords=(
            "interview",
            "q&a",
            "qa",
            "conversation",
            "guest",
            "访谈",
            "采访",
            "对谈",
            "问答",
            "嘉宾",
        ),
        zh_reason="内容呈现访谈/问答特征，适合提炼嘉宾观点和关键问答。",
        en_reason="The metadata suggests an interview or Q&A format.",
    ),
    "podcast": _StyleRule(
        keywords=(
            "podcast",
            "episode",
            "ep.",
            "talk show",
            "roundtable",
            "播客",
            "节目",
            "圆桌",
            "闲聊",
        ),
        zh_reason="内容呈现播客/对话节目特征，适合提炼观点和讨论亮点。",
        en_reason="The metadata suggests a podcast or discussion episode.",
    ),
    "lecture": _StyleRule(
        keywords=(
            "lecture",
            "course",
            "lesson",
            "class",
            "seminar",
            "training",
            "课程",
            "讲座",
            "公开课",
            "培训",
            "课堂",
        ),
        zh_reason="内容呈现课程/讲座特征，适合提炼知识点和学习要点。",
        en_reason="The metadata suggests lecture or course content.",
    ),
    "explainer": _StyleRule(
        keywords=(
            "explained",
            "explain",
            "why",
            "what is",
            "deep dive",
            "解析",
            "解读",
            "科普",
            "原理",
            "为什么",
            "是什么",
        ),
        zh_reason="内容呈现知识解读/科普特征，适合突出概念、原理和应用。",
        en_reason="The metadata suggests an explainer or educational analysis.",
    ),
    "documentary": _StyleRule(
        keywords=(
            "documentary",
            "history",
            "story of",
            "full story",
            "纪录片",
            "专题",
            "深度报道",
            "历史",
            "故事",
        ),
        zh_reason="内容呈现纪录片/专题特征，适合梳理背景、事件和人物线索。",
        en_reason="The metadata suggests documentary or feature content.",
    ),
}


def _normalize_text(value: str | None) -> str:
    return (value or "").lower()


def _score_keywords(text: str, keywords: tuple[str, ...], weight: int) -> int:
    return sum(weight for keyword in keywords if keyword in text)


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


def recommend_style_from_metadata(
    *,
    title: str,
    description: str | None,
    channel_title: str | None,
    duration_seconds: int | None,
    locale: str = "zh",
) -> SummaryStyleRecommendationResult:
    title_text = _normalize_text(title)
    description_text = _normalize_text(description)
    channel_text = _normalize_text(channel_title)

    scores: dict[str, int] = {}
    for style, rule in STYLE_RULES.items():
        score = 0
        score += _score_keywords(title_text, rule.keywords, TITLE_WEIGHT)
        score += _score_keywords(description_text, rule.keywords, DESCRIPTION_WEIGHT)
        score += _score_keywords(channel_text, rule.keywords, CHANNEL_WEIGHT)
        scores[style] = score

    if duration_seconds and duration_seconds >= 2400:
        scores["podcast"] += 1
        scores["documentary"] += 1

    best_style, best_score = max(scores.items(), key=lambda item: item[1])
    if best_score <= 0:
        reason = (
            "未识别到明显内容类型，使用通用摘要结构。"
            if locale.startswith("zh")
            else "No strong content-type signal was found, so general summary is recommended."
        )
        return SummaryStyleRecommendationResult(style="general", confidence=0.4, reason=reason)

    confidence = min(0.95, round(0.5 + best_score * 0.08, 2))
    rule = STYLE_RULES[best_style]
    reason = rule.zh_reason if locale.startswith("zh") else rule.en_reason
    return SummaryStyleRecommendationResult(style=best_style, confidence=confidence, reason=reason)


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

    recommendation = recommend_style_from_metadata(
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
