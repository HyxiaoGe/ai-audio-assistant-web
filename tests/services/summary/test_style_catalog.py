"""Tests for the canonical summary style catalog and normalization."""

from __future__ import annotations

import pytest

from app.services.summary.style_catalog import (
    ALLOWED_STYLES,
    CANONICAL_STYLES,
    DEPRECATED_STYLE_MAP,
    STYLE_CATALOG,
    normalize_content_style,
)


class TestCanonicalStyles:
    def test_exactly_seven_canonical_styles_in_order(self) -> None:
        assert CANONICAL_STYLES == (
            "meeting",
            "conversation",
            "lecture",
            "tutorial",
            "review",
            "news",
            "general",
        )

    def test_allowed_styles_alias_matches_canonical(self) -> None:
        assert ALLOWED_STYLES == CANONICAL_STYLES

    def test_catalog_covers_every_canonical_style_with_zh_en(self) -> None:
        assert tuple(STYLE_CATALOG) == CANONICAL_STYLES
        for key, entry in STYLE_CATALOG.items():
            assert set(entry) == {"zh", "en"}, key
            assert entry["zh"].strip(), key
            assert entry["en"].strip(), key


class TestNormalizeContentStyle:
    @pytest.mark.parametrize(
        ("deprecated", "expected"),
        [
            ("podcast", "conversation"),
            ("interview", "conversation"),
            ("explainer", "lecture"),
            ("documentary", "news"),
            ("video", "general"),
        ],
    )
    def test_deprecated_maps_to_canonical(self, deprecated: str, expected: str) -> None:
        assert normalize_content_style(deprecated) == expected

    @pytest.mark.parametrize("canonical", CANONICAL_STYLES)
    def test_canonical_value_passes_through(self, canonical: str) -> None:
        assert normalize_content_style(canonical) == canonical

    def test_unknown_falls_back_to_general(self) -> None:
        assert normalize_content_style("totally-bogus") == "general"

    def test_none_falls_back_to_general(self) -> None:
        assert normalize_content_style(None) == "general"

    def test_empty_string_falls_back_to_general(self) -> None:
        assert normalize_content_style("") == "general"

    def test_whitespace_and_case_are_normalized(self) -> None:
        assert normalize_content_style("  Podcast  ") == "conversation"
        assert normalize_content_style("MEETING") == "meeting"

    def test_deprecated_map_keys_are_exactly_the_five_retired(self) -> None:
        assert set(DEPRECATED_STYLE_MAP) == {
            "podcast",
            "interview",
            "explainer",
            "documentary",
            "video",
        }
        for target in DEPRECATED_STYLE_MAP.values():
            assert target in CANONICAL_STYLES
