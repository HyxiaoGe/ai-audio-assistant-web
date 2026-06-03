from __future__ import annotations

import contextlib
import importlib.util
from pathlib import Path
from typing import Any

import pytest


def _load():
    p = Path(__file__).resolve().parents[2] / "worker" / "tasks" / "summary_image_task.py"
    spec = importlib.util.spec_from_file_location("summary_image_task_uut", p)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


sit = _load()


@pytest.mark.asyncio
async def test_async_image_task_persists_each_and_publishes_global(monkeypatch) -> None:
    specs = [{"placeholder": "{{IMAGE: a | x | y}}", "type": "a",
              "description": "x", "key_texts": ["y"]}]

    persisted: list[dict[str, Any]] = []
    published: list[dict[str, Any]] = []

    async def _fake_parallel(placeholders, user_id, task_id, *, content_style, locale,
                             max_images, timeout, on_image_ready=None):
        for i, p in enumerate(placeholders, start=1):
            r = {"placeholder": p["placeholder"], "url": "/img.png",
                 "status": "success", "model_id": "m"}
            if on_image_ready:
                on_image_ready(r, i, len(placeholders))
        return [{"placeholder": p["placeholder"], "url": "/img.png",
                 "status": "success", "model_id": "m"} for p in placeholders]

    monkeypatch.setattr(sit, "generate_images_parallel", _fake_parallel)
    monkeypatch.setattr(sit, "extract_image_placeholders", lambda c: specs)
    monkeypatch.setattr(sit, "get_auto_images_config",
                        lambda: {"enabled": True, "max_images": 3, "timeout_seconds": 60,
                                 "supported_summary_types": ["overview"]})

    def _fake_apply(session, summary_id, result):
        persisted.append(result)
        return {"placeholder": result["placeholder"], "status": "ready",
                "url": result["url"], "model_id": result.get("model_id")}

    def _fake_publish(**kwargs):
        published.append(kwargs)

    monkeypatch.setattr(sit, "apply_image_result_to_summary", _fake_apply)
    monkeypatch.setattr(sit, "publish_image_ready_global", _fake_publish)

    monkeypatch.setattr(sit, "get_sync_db_session", lambda: contextlib.nullcontext(object()))

    await sit._run_summary_images(
        task_id="task-1",
        user_id="user-1",
        summary_id="sum-1",
        content="正文 {{IMAGE: a | x | y}}",
        content_style="review",
    )

    assert len(persisted) == 1
    assert len(published) == 1
    assert published[0]["status"] == "ready"
    assert published[0]["placeholder"] == "{{IMAGE: a | x | y}}"
    assert published[0]["task_id"] == "task-1"
    assert published[0]["summary_id"] == "sum-1"


@pytest.mark.asyncio
async def test_async_image_task_publishes_failed_on_image_failure(monkeypatch) -> None:
    specs = [{"placeholder": "{{IMAGE: a | x | y}}", "type": "a",
              "description": "x", "key_texts": ["y"]}]
    published: list[dict[str, Any]] = []

    async def _fake_parallel(placeholders, *a, on_image_ready=None, **k):
        # url is intentionally non-None here so the production guard (url=None on failed)
        # is exercised — if the guard broke, published[0]["url"] would be "/img.png"
        r = {"placeholder": placeholders[0]["placeholder"], "url": "/img.png",
             "status": "failed", "error": "timeout", "model_id": None}
        if on_image_ready:
            on_image_ready(r, 1, 1)
        return [r]

    monkeypatch.setattr(sit, "generate_images_parallel", _fake_parallel)
    monkeypatch.setattr(sit, "extract_image_placeholders", lambda c: specs)
    monkeypatch.setattr(sit, "get_auto_images_config",
                        lambda: {"enabled": True, "max_images": 3, "timeout_seconds": 60,
                                 "supported_summary_types": ["overview"]})
    monkeypatch.setattr(sit, "apply_image_result_to_summary",
                        lambda s, sid, r: {"placeholder": r["placeholder"], "status": "failed",
                                           "url": None, "model_id": None})
    monkeypatch.setattr(sit, "publish_image_ready_global",
                        lambda **kw: published.append(kw))
    monkeypatch.setattr(sit, "get_sync_db_session", lambda: contextlib.nullcontext(object()))

    await sit._run_summary_images(
        task_id="task-1", user_id="user-1", summary_id="sum-1",
        content="正文 {{IMAGE: a | x | y}}", content_style="review",
    )
    assert published[0]["status"] == "failed"
    assert published[0]["url"] is None
