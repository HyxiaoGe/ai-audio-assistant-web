"""Tests for _build_image_template_vars: color names, mood, verbatim quoting."""

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


def _full_config() -> dict[str, Any]:
    return {
        "visual_styles": {
            "flat_vector": {
                "prompt_zh": "扁平矢量风格 zh",
                "prompt_en": "flat vector en",
                "mood_zh": "整体观感：干净、亲和、现代。",
                "mood_en": "Overall mood: clean, approachable, modern.",
            }
        },
        "layout_templates": {
            "zh": {"flexible": "灵活布局 zh", "hierarchical": "层级布局 zh"},
            "en": {"flexible": "flexible en", "hierarchical": "hierarchical en"},
        },
        "image_type_names": {
            "zh": {"infographic": "信息图"},
            "en": {"infographic": "Infographic"},
        },
        "content_style_names": {
            "zh": {"lecture": "讲解"},
            "en": {"lecture": "Lecture"},
        },
    }


def _full_style_config() -> dict[str, Any]:
    return {
        "default_type": "infographic",
        "visual_style": "flat_vector",
        "aspect_ratio": "3:4",
        "layout": "hierarchical",
        "color_scheme": {
            "primary": "#3B82F6",
            "primary_name": "明亮天蓝",
            "primary_name_en": "bright sky blue",
            "secondary": "#10B981",
            "secondary_name": "翠绿",
            "secondary_name_en": "emerald green",
            "background": "#F8FAFC",
            "background_name": "近白浅灰",
            "background_name_en": "near-white light gray",
        },
    }


class TestColorNames:
    def test_zh_color_names_hit(self) -> None:
        manager = _create_manager_with_mock()
        out = manager._build_image_template_vars(
            _full_config(), _full_style_config(), "zh", "infographic",
            "lecture", "desc", ["标题"],
        )
        assert out["primary_color_name"] == "明亮天蓝"
        assert out["secondary_color_name"] == "翠绿"
        assert out["background_color_name"] == "近白浅灰"
        assert out["primary_color"] == "#3B82F6"
        assert out["secondary_color"] == "#10B981"
        assert out["background_color"] == "#F8FAFC"

    def test_en_color_names_hit(self) -> None:
        manager = _create_manager_with_mock()
        out = manager._build_image_template_vars(
            _full_config(), _full_style_config(), "en", "infographic",
            "lecture", "desc", ["Title"],
        )
        assert out["primary_color_name"] == "bright sky blue"
        assert out["secondary_color_name"] == "emerald green"
        assert out["background_color_name"] == "near-white light gray"

    def test_color_name_fallback_to_hex_when_missing(self) -> None:
        manager = _create_manager_with_mock()
        style_config = {
            "visual_style": "flat_vector",
            "layout": "flexible",
            "color_scheme": {
                "primary": "#111111",
                "secondary": "#222222",
                "background": "#333333",
            },
        }
        out = manager._build_image_template_vars(
            _full_config(), style_config, "zh", "infographic", "lecture", "desc", ["X"],
        )
        assert out["primary_color_name"] == "#111111"
        assert out["secondary_color_name"] == "#222222"
        assert out["background_color_name"] == "#333333"


class TestMood:
    def test_mood_zh_hit(self) -> None:
        manager = _create_manager_with_mock()
        out = manager._build_image_template_vars(
            _full_config(), _full_style_config(), "zh", "infographic",
            "lecture", "desc", ["X"],
        )
        assert out["style_mood"] == "整体观感：干净、亲和、现代。"

    def test_mood_en_hit(self) -> None:
        manager = _create_manager_with_mock()
        out = manager._build_image_template_vars(
            _full_config(), _full_style_config(), "en", "infographic",
            "lecture", "desc", ["X"],
        )
        assert out["style_mood"] == "Overall mood: clean, approachable, modern."

    def test_mood_fallback_zh(self) -> None:
        manager = _create_manager_with_mock()
        config = _full_config()
        del config["visual_styles"]["flat_vector"]["mood_zh"]
        del config["visual_styles"]["flat_vector"]["mood_en"]
        out = manager._build_image_template_vars(
            config, _full_style_config(), "zh", "infographic", "lecture", "desc", ["X"],
        )
        assert out["style_mood"] == "整体观感：清晰、专业。"

    def test_mood_fallback_en(self) -> None:
        manager = _create_manager_with_mock()
        config = _full_config()
        del config["visual_styles"]["flat_vector"]["mood_zh"]
        del config["visual_styles"]["flat_vector"]["mood_en"]
        out = manager._build_image_template_vars(
            config, _full_style_config(), "en", "infographic", "lecture", "desc", ["X"],
        )
        assert out["style_mood"] == "Overall mood: clear and professional."


class TestKeyTextsVerbatim:
    def test_key_texts_each_quoted(self) -> None:
        manager = _create_manager_with_mock()
        out = manager._build_image_template_vars(
            _full_config(), _full_style_config(), "zh", "infographic",
            "lecture", "desc", ["关键词A", "关键词B", "数字 42"],
        )
        assert out["key_texts_formatted"] == '- "关键词A"\n- "关键词B"\n- "数字 42"'

    def test_no_key_texts_keeps_zh_fallback(self) -> None:
        manager = _create_manager_with_mock()
        out = manager._build_image_template_vars(
            _full_config(), _full_style_config(), "zh", "infographic",
            "lecture", "desc", [],
        )
        assert out["key_texts_formatted"] == "- (根据主题自动生成合适的标签)"

    def test_no_key_texts_keeps_en_fallback(self) -> None:
        manager = _create_manager_with_mock()
        out = manager._build_image_template_vars(
            _full_config(), _full_style_config(), "en", "infographic",
            "lecture", "desc", [],
        )
        assert (
            out["key_texts_formatted"]
            == "- (Auto-generate appropriate labels based on topic)"
        )
