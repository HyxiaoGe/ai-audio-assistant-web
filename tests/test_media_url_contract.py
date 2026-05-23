from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.api.deps import CurrentUser
from app.api.v1 import summaries as summaries_api
from app.models.summary import Summary
from app.models.task import Task
from app.services import task_service
from app.services.task_service import TaskService


class _ScalarResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value

    def scalars(self) -> _ScalarResult:
        return self

    def all(self) -> list[Any]:
        return self._value if isinstance(self._value, list) else []


class _SequenceSession:
    def __init__(self, *results: Any) -> None:
        self._results = list(results)

    async def execute(self, query: object) -> _ScalarResult:
        if not self._results:
            raise AssertionError(f"Unexpected query: {query}")
        return _ScalarResult(self._results.pop(0))


def _task(**overrides: Any) -> Task:
    now = datetime(2026, 5, 23, tzinfo=UTC)
    task = Task(
        id=overrides.pop("id", "11111111-1111-1111-1111-111111111111"),
        user_id=overrides.pop("user_id", "22222222-2222-2222-2222-222222222222"),
        title=overrides.pop("title", "Demo"),
        source_type=overrides.pop("source_type", "upload"),
        source_key=overrides.pop("source_key", "upload/user/demo.wav"),
        source_url=overrides.pop("source_url", None),
        content_hash=overrides.pop("content_hash", "hash"),
        status=overrides.pop("status", "completed"),
        progress=overrides.pop("progress", 100),
        stage=overrides.pop("stage", None),
        duration_seconds=overrides.pop("duration_seconds", 42),
        detected_language=overrides.pop("detected_language", "zh"),
        error_message=overrides.pop("error_message", None),
        created_at=overrides.pop("created_at", now),
        updated_at=overrides.pop("updated_at", now),
        **overrides,
    )
    task.stages = []
    return task


def _summary(**overrides: Any) -> Summary:
    now = datetime(2026, 5, 23, tzinfo=UTC)
    return Summary(
        id=overrides.pop("id", "33333333-3333-3333-3333-333333333333"),
        task_id=overrides.pop("task_id", "11111111-1111-1111-1111-111111111111"),
        summary_type=overrides.pop("summary_type", "visual_mindmap"),
        version=overrides.pop("version", 1),
        is_active=overrides.pop("is_active", True),
        content=overrides.pop("content", "graph TD; A-->B"),
        visual_format=overrides.pop("visual_format", "mermaid"),
        image_key=overrides.pop("image_key", "visuals/user/task/mindmap.png"),
        created_at=overrides.pop("created_at", now),
        updated_at=overrides.pop("updated_at", now),
        **overrides,
    )


@pytest.mark.asyncio
async def test_task_detail_returns_backend_resolved_audio_url(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_media_url(object_key: str, user_id: str) -> str:
        return f"https://storage.local/{user_id}/{object_key}?signature=download"

    monkeypatch.setattr(task_service, "build_media_download_url", fake_media_url, raising=False)

    user = CurrentUser(id="22222222-2222-2222-2222-222222222222", email="user@example.com")
    result = await TaskService.get_task_detail(_SequenceSession(_task()), user, "11111111-1111-1111-1111-111111111111")

    assert result.audio_url == "https://storage.local/22222222-2222-2222-2222-222222222222/upload/user/demo.wav?signature=download"


@pytest.mark.asyncio
async def test_summary_list_returns_backend_resolved_image_url(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_media_url(object_key: str, user_id: str) -> str:
        return f"https://storage.local/{user_id}/{object_key}?signature=download"

    monkeypatch.setattr(summaries_api, "build_media_download_url", fake_media_url, raising=False)

    user = CurrentUser(id="22222222-2222-2222-2222-222222222222", email="user@example.com")
    response = await summaries_api.get_summaries(
        "11111111-1111-1111-1111-111111111111",
        _SequenceSession(_task(), [_summary()]),
        user,
    )

    assert response.body
    assert b"https://storage.local/22222222-2222-2222-2222-222222222222/visuals/user/task/mindmap.png?signature=download" in response.body
    assert b"/api/v1/media/" not in response.body


@pytest.mark.asyncio
async def test_visual_summary_returns_backend_resolved_image_url(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_media_url(object_key: str, user_id: str) -> str:
        return f"https://storage.local/{user_id}/{object_key}?signature=download"

    monkeypatch.setattr(summaries_api, "build_media_download_url", fake_media_url, raising=False)

    user = CurrentUser(id="22222222-2222-2222-2222-222222222222", email="user@example.com")
    response = await summaries_api.get_visual_summary(
        "11111111-1111-1111-1111-111111111111",
        "mindmap",
        _SequenceSession(_task(), _summary()),
        user,
    )

    assert response.body
    assert b"https://storage.local/22222222-2222-2222-2222-222222222222/visuals/user/task/mindmap.png?signature=download" in response.body
    assert b"/api/v1/media/" not in response.body
