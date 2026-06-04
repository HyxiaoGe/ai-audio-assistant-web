from __future__ import annotations

import json
from pathlib import Path

import pytest

CONFIG_PATH = Path(__file__).resolve().parents[2] / "app" / "prompts" / "templates" / "images" / "config.json"

CANONICAL = {"meeting", "conversation", "lecture", "tutorial", "review", "news", "general"}
DEPRECATED = {"podcast", "interview", "explainer", "documentary", "video"}


@pytest.fixture(scope="module")
def cfg() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_content_style_mapping_is_canonical_seven(cfg: dict) -> None:
    mapping = cfg["content_style_mapping"]
    assert set(mapping) == CANONICAL
    assert not (set(mapping) & DEPRECATED)


def test_each_mapping_has_color_names(cfg: dict) -> None:
    for key, spec in cfg["content_style_mapping"].items():
        cs = spec["color_scheme"]
        for slot in ("primary", "secondary", "background"):
            assert cs[slot].startswith("#"), f"{key}.{slot} hex missing"
            assert cs[f"{slot}_name"], f"{key}.{slot}_name missing"
            assert cs[f"{slot}_name_en"], f"{key}.{slot}_name_en missing"


def test_conversation_mapping_inherits_podcast_aesthetic(cfg: dict) -> None:
    conv = cfg["content_style_mapping"]["conversation"]
    assert conv["default_type"] == "mindmap"
    assert conv["visual_style"] == "hand_drawn"
    assert conv["layout"] == "radial"
    assert conv["color_scheme"]["primary"] == "#F59E0B"


def test_lecture_mapping_is_academic(cfg: dict) -> None:
    lec = cfg["content_style_mapping"]["lecture"]
    assert lec["default_type"] == "concept"
    assert lec["visual_style"] == "chalkboard"
    assert lec["layout"] == "hierarchical"


def test_visual_styles_are_thick_with_mood(cfg: dict) -> None:
    styles = cfg["visual_styles"]
    assert set(styles) == {"flat_vector", "isometric_3d", "hand_drawn", "infographic_modern", "chalkboard"}
    for key, spec in styles.items():
        assert len(spec["prompt_zh"]) >= 80, f"{key} prompt_zh too thin"
        assert len(spec["prompt_en"]) >= 80, f"{key} prompt_en too thin"
        assert spec["mood_zh"], f"{key} mood_zh missing"
        assert spec["mood_en"], f"{key} mood_en missing"


def test_image_types_recommended_for_is_canonical(cfg: dict) -> None:
    for itype, spec in cfg["image_types"].items():
        for rec in spec["recommended_for"]:
            assert rec in CANONICAL, f"{itype} recommends non-canonical {rec}"


def test_content_style_names_seven(cfg: dict) -> None:
    for lang in ("zh", "en"):
        assert set(cfg["content_style_names"][lang]) == CANONICAL
