"""Tests for _build_prompt_slug_candidates fallback behavior (footgun fix)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch


def _create_manager_with_mock() -> Any:
    from app.prompts.manager import PromptManager

    PromptManager._instance = None
    if hasattr(PromptManager, "_initialized"):
        delattr(PromptManager, "_initialized")

    with patch("app.prompts.manager.PromptHubClient") as MockClient:
        MockClient.return_value = MagicMock()
        with patch("app.config.settings") as mock_settings:
            mock_settings.PROMPTHUB_BASE_URL = "http://fake:8000"
            mock_settings.PROMPTHUB_API_KEY = "ph-fake-key"
            mock_settings.PROMPTHUB_CACHE_TTL = 60

            manager = PromptManager.__new__(PromptManager)
            if hasattr(manager, "_initialized"):
                delattr(manager, "_initialized")
            manager.__init__()  # type: ignore[misc]
    return manager


class TestSlugCandidates:
    def test_overview_returns_styled_then_generic(self) -> None:
        manager = _create_manager_with_mock()
        candidates = manager._build_prompt_slug_candidates(
            "summary", "overview", "zh-CN", "meeting"
        )
        assert candidates == ["summary-overview-meeting-zh", "summary-overview-zh"]

    def test_keypoints_returns_styled_then_generic(self) -> None:
        manager = _create_manager_with_mock()
        candidates = manager._build_prompt_slug_candidates(
            "summary", "key_points", "en-US", "conversation"
        )
        assert candidates == [
            "summary-keypoints-conversation-en",
            "summary-keypoints-en",
        ]

    def test_general_overview_still_has_generic_fallback(self) -> None:
        manager = _create_manager_with_mock()
        candidates = manager._build_prompt_slug_candidates(
            "summary", "overview", "zh-CN", "general"
        )
        assert candidates == ["summary-overview-general-zh", "summary-overview-zh"]

    def test_action_items_non_style_specific_returns_generic_only(self) -> None:
        manager = _create_manager_with_mock()
        candidates = manager._build_prompt_slug_candidates(
            "summary", "action_items", "zh-CN", "meeting"
        )
        assert candidates == ["summary-actionitems-zh"]

    def test_action_items_review_returns_styled_then_generic(self) -> None:
        manager = _create_manager_with_mock()
        candidates = manager._build_prompt_slug_candidates(
            "summary", "action_items", "zh-CN", "review"
        )
        assert candidates == [
            "summary-actionitems-review-zh",
            "summary-actionitems-zh",
        ]

    def test_segmentation_segment_has_generic_fallback(self) -> None:
        manager = _create_manager_with_mock()
        candidates = manager._build_prompt_slug_candidates(
            "segmentation", "segment", "zh-CN", "lecture"
        )
        assert candidates == [
            "segmentation-segment-lecture-zh",
            "segmentation-segment-zh",
        ]
