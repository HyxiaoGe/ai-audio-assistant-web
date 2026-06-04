"""Canonical summary style catalog and backward-compatible normalization.

Single source of truth for the 7 canonical content styles. Deprecated style
keys (from the old 11-style scheme) are mapped to their canonical target at the
boundary, so PromptHub slugs and config.json only need to maintain 7 keys and
old persisted tasks never 404.
"""

from __future__ import annotations

# Canonical 7-style set, in display order.
CANONICAL_STYLES: tuple[str, ...] = (
    "meeting",
    "conversation",
    "lecture",
    "tutorial",
    "review",
    "news",
    "general",
)

# Retired style keys -> canonical target.
DEPRECATED_STYLE_MAP: dict[str, str] = {
    "podcast": "conversation",
    "interview": "conversation",
    "explainer": "lecture",
    "documentary": "news",
    "video": "general",
}

# zh/en one-line definitions, consumed by the auto-detection prompt.
STYLE_CATALOG: dict[str, dict[str, str]] = {
    "meeting": {
        "zh": "会议、讨论、决策导向；多人对话，关注决策、依据、分歧与行动项。",
        "en": "Meetings and decision-oriented discussions; multi-party, focused on decisions, rationale, open issues, and action items.",
    },
    "conversation": {
        "zh": "对话、访谈、播客；观点导向，关注争论焦点、各方观点、洞见与启示。",
        "en": "Conversations, interviews, and podcasts; opinion-oriented, focused on what is debated, viewpoints, insights, and takeaways.",
    },
    "lecture": {
        "zh": "讲座、课程、知识科普；知识传递，关注核心概念、原理、实例与易混点。",
        "en": "Lectures, courses, and explainers; knowledge transfer, focused on core concepts, principles, examples, and common pitfalls.",
    },
    "tutorial": {
        "zh": "操作教程、使用指南、How-to；关注目标、有序步骤、技巧与常见坑。",
        "en": "Tutorials and how-to guides; focused on goals, ordered steps, techniques, and common pitfalls.",
    },
    "review": {
        "zh": "评测、体验、对比；关注评测对象、各维度表现与数据、优缺点与结论。",
        "en": "Reviews and comparisons; focused on the subject, per-dimension performance with data, pros/cons, and verdict.",
    },
    "news": {
        "zh": "新闻、资讯、纪实；事实导向，关注事件、时间线、关键数据与各方立场。",
        "en": "News, current affairs, and documentary; fact-oriented, focused on events, timeline, key data, and stances.",
    },
    "general": {
        "zh": "通用内容、以上都不属于的兜底；关注主题、核心要点、重要信息与结论。",
        "en": "General fallback for content that fits none of the above; focused on topic, core points, key information, and conclusions.",
    },
}

# Alias kept for callers that import ALLOWED_STYLES (e.g. style recommendation).
ALLOWED_STYLES: tuple[str, ...] = CANONICAL_STYLES


def normalize_content_style(style: str | None) -> str:
    """Normalize any content style to one of the 7 canonical keys.

    - Deprecated keys (podcast/interview/explainer/documentary/video) map to
      their canonical target.
    - Unknown/empty/None values fall back to ``general``.
    - Case and surrounding whitespace are ignored.
    """
    s = (style or "").strip().lower()
    s = DEPRECATED_STYLE_MAP.get(s, s)
    return s if s in CANONICAL_STYLES else "general"
