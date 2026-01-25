"""Summary Styles API - List available summary styles with i18n support."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Request

from app.core.response import success
from app.schemas.summary_style import SummaryStyleItem, SummaryStyleListResponse

logger = logging.getLogger("app.api.summary_styles")

router = APIRouter(prefix="/summary-styles", tags=["summary-styles"])

# Cache for styles configuration
_styles_cache: Optional[dict[str, Any]] = None


def _load_styles_config() -> dict[str, Any]:
    """Load styles i18n configuration with caching.

    Returns:
        Dictionary containing styles configuration.
    """
    global _styles_cache
    if _styles_cache is not None:
        return _styles_cache

    config_path = (
        Path(__file__).parent.parent.parent
        / "prompts"
        / "templates"
        / "summary"
        / "styles_i18n.json"
    )

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            _styles_cache = json.load(f)
    except FileNotFoundError:
        logger.error(f"Styles config not found: {config_path}")
        _styles_cache = {"version": "0.0.0", "styles": {}}
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse styles config: {e}")
        _styles_cache = {"version": "0.0.0", "styles": {}}

    return _styles_cache


# Predefined style order for consistent display
STYLE_ORDER = [
    "meeting",
    "lecture",
    "podcast",
    "interview",
    "tutorial",
    "review",
    "news",
    "explainer",
    "documentary",
    "video",
    "general",
]


@router.get("")
async def get_summary_styles(request: Request) -> dict[str, Any]:
    """Get all supported summary styles with i18n support.

    Returns localized style information based on Accept-Language header.

    Returns:
        List of summary styles with:
        - id: Style identifier
        - name: Display name (localized)
        - description: Style description (localized)
        - focus: Summary focus points (localized)
        - icon: Icon identifier (optional)
        - recommended_visual_types: Recommended visual summary types
    """
    # Get locale from middleware (zh or en)
    lang = getattr(request.state, "locale", "zh")

    config = _load_styles_config()

    styles: list[SummaryStyleItem] = []

    # Build styles list in predefined order
    for style_id in STYLE_ORDER:
        if style_id not in config.get("styles", {}):
            continue

        style_data = config["styles"][style_id]
        i18n = style_data.get("i18n", {})

        # Try requested language, fallback to Chinese
        locale_data = i18n.get(lang, i18n.get("zh", {}))

        styles.append(
            SummaryStyleItem(
                id=style_id,
                name=locale_data.get("name", style_id),
                description=locale_data.get("description", ""),
                focus=locale_data.get("focus", ""),
                icon=style_data.get("icon"),
                recommended_visual_types=style_data.get("recommended_visual_types", []),
            )
        )

    # Add any styles not in the predefined order (future-proofing)
    for style_id, style_data in config.get("styles", {}).items():
        if style_id in STYLE_ORDER:
            continue

        i18n = style_data.get("i18n", {})
        locale_data = i18n.get(lang, i18n.get("zh", {}))

        styles.append(
            SummaryStyleItem(
                id=style_id,
                name=locale_data.get("name", style_id),
                description=locale_data.get("description", ""),
                focus=locale_data.get("focus", ""),
                icon=style_data.get("icon"),
                recommended_visual_types=style_data.get("recommended_visual_types", []),
            )
        )

    response = SummaryStyleListResponse(
        version=config.get("version", "1.0.0"),
        styles=styles,
    )

    return success(data=response.model_dump())
