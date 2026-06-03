from __future__ import annotations

import contextlib
import importlib.util
from pathlib import Path
from typing import Any

import pytest


def _load_image_generator_module():
    module_path = Path(__file__).resolve().parents[2] / "worker" / "tasks" / "image_generator.py"
    spec = importlib.util.spec_from_file_location("image_generator_visual_failed_uut", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load image_generator module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


image_generator = _load_image_generator_module()


def _enable_config(monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.setattr(
        image_generator, "extract_image_placeholders", lambda content: [{"placeholder": "{{IMAGE: x}}"}]
    )


@pytest.mark.asyncio
async def test_all_images_failed_emits_visual_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.notifications.types import NotificationType

    _enable_config(monkeypatch)

    async def _all_failed(*a: Any, **k: Any) -> list[dict[str, Any]]:
        return [{"placeholder": "{{IMAGE: x}}", "status": "failed"}]

    monkeypatch.setattr(image_generator, "generate_images_parallel", _all_failed)
    monkeypatch.setattr(image_generator, "replace_placeholders", lambda content, results: content)

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(image_generator.NotificationService, "notify", staticmethod(lambda s, **kw: calls.append(kw)))
    monkeypatch.setattr(
        image_generator,
        "get_sync_db_session",
        lambda: contextlib.nullcontext(object()),
    )

    await image_generator.process_summary_images(
        content="{{IMAGE: x}}", task_id="t1", user_id="user-1", summary_type="overview"
    )

    assert len(calls) == 1
    assert calls[0]["type"] == NotificationType.VISUAL_FAILED
    assert calls[0]["user_id"] == "user-1"
    assert calls[0]["task_id"] == "t1"


@pytest.mark.asyncio
async def test_some_images_succeed_does_not_emit(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_config(monkeypatch)

    async def _one_ok(*a: Any, **k: Any) -> list[dict[str, Any]]:
        return [
            {"placeholder": "{{IMAGE: x}}", "status": "success", "url": "http://x"},
            {"placeholder": "{{IMAGE: y}}", "status": "failed"},
        ]

    monkeypatch.setattr(image_generator, "generate_images_parallel", _one_ok)
    monkeypatch.setattr(image_generator, "replace_placeholders", lambda content, results: content)

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(image_generator.NotificationService, "notify", staticmethod(lambda s, **kw: calls.append(kw)))
    monkeypatch.setattr(
        image_generator,
        "get_sync_db_session",
        lambda: contextlib.nullcontext(object()),
    )

    await image_generator.process_summary_images(
        content="{{IMAGE: x}}", task_id="t1", user_id="user-1", summary_type="overview"
    )

    assert calls == []  # 有成功则不发失败通知
