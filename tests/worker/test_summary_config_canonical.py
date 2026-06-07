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


def test_version_current_and_changelog_history(cfg: dict) -> None:
    assert cfg["version"] == "1.9.0"
    assert cfg["version"] in cfg["changelog"]
    # 历史条目保留
    assert "1.5.0" in cfg["changelog"]


def test_default_image_model_is_seedream(cfg: dict) -> None:
    image_model = cfg["features"]["auto_images"]["image_model"]
    assert image_model["provider"] == "image_service"
    # 必须与 image-service SUPPORTED_MODELS 的裸别名严格一致
    assert image_model["model_id"] == "doubao-seedream-4-5"


def test_max_tokens_enlarged_for_reasoning_models(cfg: dict) -> None:
    # deepseek-chat 经代理会产出 reasoning_content，max_tokens 需为正文留足预算，
    # 否则推理链挤占额度导致正文截断或为空（详见 1.6.0/1.7.0 changelog）。
    # 1.7.0 起统一放大到 12000（max_tokens 是上限非预付，按实际生成计费）。
    mp = cfg["model_params"]
    assert mp["overview"]["max_tokens"] == 12000
    assert mp["key_points"]["max_tokens"] == 12000
    assert mp["action_items"]["max_tokens"] == 12000
