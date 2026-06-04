from __future__ import annotations

import json
from pathlib import Path

import pytest

CONFIG_PATH = Path(__file__).resolve().parents[2] / "app" / "prompts" / "templates" / "summary" / "config.json"

CANONICAL = {"meeting", "conversation", "lecture", "tutorial", "review", "news", "general"}
DEPRECATED = {"podcast", "interview", "explainer", "documentary", "video"}


@pytest.fixture(scope="module")
def cfg() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_supported_content_styles_canonical_seven(cfg: dict) -> None:
    styles = set(cfg["supported_content_styles"])
    assert styles == CANONICAL
    assert not (styles & DEPRECATED)


def test_content_style_descriptions_canonical_seven(cfg: dict) -> None:
    assert set(cfg["content_style_descriptions"]) == CANONICAL


def test_max_images_is_six(cfg: dict) -> None:
    assert cfg["features"]["auto_images"]["max_images"] == 6


def test_style_specific_prompt_types_preserved(cfg: dict) -> None:
    assert cfg["style_specific_prompt_types"] == {"action_items": ["review"]}


def test_changelog_has_150(cfg: dict) -> None:
    assert "1.5.0" in cfg["changelog"]
    assert cfg["version"] == "1.5.0"
