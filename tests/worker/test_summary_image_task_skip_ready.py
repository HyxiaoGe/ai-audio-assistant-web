"""Fix3:异步配图重跑只补 pending/failed,跳过已 ready 槽(worker-lost 重投 / 巡检补派发都走本任务)。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest


def _load() -> Any:
    p = Path(__file__).resolve().parents[2] / "worker" / "tasks" / "summary_image_task.py"
    spec = importlib.util.spec_from_file_location("summary_image_task_skip_uut", p)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


sit = _load()


class _FakeSummary:
    def __init__(self, images: list[dict[str, Any]]) -> None:
        self.images = images


class _FakeQuery:
    def __init__(self, summary: _FakeSummary) -> None:
        self._s = summary

    def filter(self, *a: Any, **k: Any) -> _FakeQuery:
        return self

    def first(self) -> _FakeSummary:
        return self._s


class _FakeSession:
    def __init__(self, summary: _FakeSummary) -> None:
        self._s = summary

    def query(self, *a: Any, **k: Any) -> _FakeQuery:
        return _FakeQuery(self._s)

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *a: Any) -> bool:
        return False


def _placeholder(tag: str) -> dict[str, Any]:
    return {"placeholder": tag, "type": "a", "description": "x", "key_texts": ["y"]}


def _patch_common(monkeypatch: pytest.MonkeyPatch, images: list[dict[str, Any]], captured: dict[str, Any]) -> None:
    summary = _FakeSummary(images)
    monkeypatch.setattr(sit, "get_sync_db_session", lambda: _FakeSession(summary))
    monkeypatch.setattr(
        sit, "extract_image_placeholders", lambda c: [_placeholder("P1"), _placeholder("P2"), _placeholder("P3")]
    )
    monkeypatch.setattr(
        sit,
        "get_auto_images_config",
        lambda: {"enabled": True, "max_images": 6, "timeout_seconds": 60, "supported_summary_types": ["overview"]},
    )

    async def _fake_parallel(placeholders, *a: Any, **k: Any):
        captured["placeholders"] = [p["placeholder"] for p in placeholders]
        return []

    monkeypatch.setattr(sit, "generate_images_parallel", _fake_parallel)


@pytest.mark.asyncio
async def test_skips_ready_keeps_pending_and_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    images = [
        {"placeholder": "P1", "status": "ready"},
        {"placeholder": "P2", "status": "pending"},
        {"placeholder": "P3", "status": "failed"},
    ]
    _patch_common(monkeypatch, images, captured)

    await sit._run_summary_images(
        task_id="t", user_id="u", summary_id="s", content="正文 P1 P2 P3", content_style="review"
    )

    assert captured["placeholders"] == ["P2", "P3"]  # ready 跳过,pending/failed 重跑


@pytest.mark.asyncio
async def test_all_ready_does_not_call_parallel(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    images = [
        {"placeholder": "P1", "status": "ready"},
        {"placeholder": "P2", "status": "ready"},
        {"placeholder": "P3", "status": "ready"},
    ]
    _patch_common(monkeypatch, images, captured)

    await sit._run_summary_images(
        task_id="t", user_id="u", summary_id="s", content="正文 P1 P2 P3", content_style="review"
    )

    assert "placeholders" not in captured  # 全 ready → 不调 generate_images_parallel,直接返回
