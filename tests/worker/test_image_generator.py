from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_image_generator_module():
    module_path = Path(__file__).resolve().parents[2] / "worker" / "tasks" / "image_generator.py"
    spec = importlib.util.spec_from_file_location("image_generator_under_test", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load image_generator module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


image_generator = _load_image_generator_module()


def test_auto_images_enabled_for_overview_does_not_depend_on_content_style(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        image_generator,
        "get_auto_images_config",
        lambda: {
            "enabled": True,
            "supported_summary_types": ["overview"],
            "supported_content_styles": ["meeting"],
        },
    )

    assert image_generator.is_auto_images_enabled("overview", "review") is True
    assert image_generator.is_auto_images_enabled("key_points", "review") is False


@pytest.mark.asyncio
async def test_process_summary_images_plans_default_article_image_when_summary_has_no_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        image_generator,
        "get_auto_images_config",
        lambda: {
            "enabled": True,
            "max_images": 3,
            "timeout_seconds": 60,
            "supported_summary_types": ["overview"],
        },
    )

    generated_placeholders: list[list[dict]] = []

    async def fake_generate_images_parallel(
        placeholders: list[dict],
        *_args: object,
        **_kwargs: object,
    ) -> list[dict]:
        generated_placeholders.append(placeholders)
        return [
            {
                "placeholder": placeholders[0]["placeholder"],
                "url": "/api/v1/summaries/images/user/task/image.png",
                "status": "success",
                "model_id": "image-model",
            }
        ]

    monkeypatch.setattr(image_generator, "generate_images_parallel", fake_generate_images_parallel)

    content = (
        "## 实测 Gemini 3.5 Flash 与 Omni Flash\n\n"
        "这段内容比较了多个 AI 产品的体验差异、优势和不足。\n\n"
        "整体结论是新模型在速度和多模态能力上更进一步。"
    )

    final_content, image_results = await image_generator.process_summary_images(
        content=content,
        task_id="task-1",
        user_id="user-1",
        summary_type="overview",
        content_style="review",
    )

    assert generated_placeholders
    placeholder = generated_placeholders[0][0]
    assert placeholder["type"] == "comparison"
    assert "Gemini 3.5 Flash 与 Omni Flash" in placeholder["description"]
    assert image_results[0]["status"] == "success"
    assert "![实测 Gemini 3.5 Flash 与 Omni Flash]" in final_content
    assert "这段内容比较了多个 AI 产品" in final_content
