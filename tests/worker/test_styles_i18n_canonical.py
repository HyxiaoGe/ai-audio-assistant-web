from __future__ import annotations

import json
from pathlib import Path

import pytest

CONFIG_PATH = Path(__file__).resolve().parents[2] / "app" / "prompts" / "templates" / "summary" / "styles_i18n.json"

CANONICAL = {"meeting", "conversation", "lecture", "tutorial", "review", "news", "general"}
DEPRECATED = {"podcast", "interview", "explainer", "documentary", "video"}
REAL_VISUAL_TYPES = {"infographic", "mindmap", "timeline", "comparison", "concept"}
DEAD_MERMAID = {"flowchart"}


@pytest.fixture(scope="module")
def cfg() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_styles_are_canonical_seven(cfg: dict) -> None:
    keys = set(cfg["styles"])
    assert keys == CANONICAL
    assert not (keys & DEPRECATED)


def test_each_style_has_full_i18n(cfg: dict) -> None:
    for key, spec in cfg["styles"].items():
        assert spec["icon"], f"{key} icon missing"
        for lang in ("zh", "en"):
            blk = spec["i18n"][lang]
            assert blk["name"] and blk["description"] and blk["focus"], f"{key}.{lang} incomplete"


def test_recommended_visual_types_are_real_not_dead_mermaid(cfg: dict) -> None:
    for key, spec in cfg["styles"].items():
        rec = set(spec["recommended_visual_types"])
        assert rec, f"{key} has no recommended_visual_types"
        assert rec <= REAL_VISUAL_TYPES, f"{key} has non-real types {rec - REAL_VISUAL_TYPES}"
        assert not (rec & DEAD_MERMAID), f"{key} still references dead mermaid types"
