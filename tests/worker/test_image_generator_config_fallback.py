from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_image_generator_module():
    module_path = Path(__file__).resolve().parents[2] / "worker" / "tasks" / "image_generator.py"
    spec = importlib.util.spec_from_file_location("image_generator_fallback_under_test", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load image_generator module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


image_generator = _load_image_generator_module()


def test_get_auto_images_config_exception_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Boom:
        def _load_config(self, _category: str) -> dict:
            raise RuntimeError("boom")

    monkeypatch.setattr(image_generator, "get_prompt_manager", lambda: _Boom())

    cfg = image_generator.get_auto_images_config()

    assert cfg["enabled"] is False
    assert cfg["max_images"] == 6
    assert cfg["supported_summary_types"] == ["overview"]
    assert "supported_content_styles" not in cfg
