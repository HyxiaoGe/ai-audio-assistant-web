from __future__ import annotations

import types
from uuid import uuid4

import pytest

from app.schemas.task import TaskCreateRequest, TaskOptions
from app.services.task_service import TaskService


class _FakeResult:
    def scalar_one_or_none(self) -> None:
        return None


class _FakeAsyncSession:
    def __init__(self) -> None:
        self.added: list[object] = []

    async def execute(self, *a: object, **k: object) -> _FakeResult:
        return _FakeResult()

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        return None

    async def refresh(self, obj: object) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = str(uuid4())


def _patch_common(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    monkeypatch.setattr("app.services.task_service.is_admin_user", lambda _u: True)

    async def _noop_init(_db: object, _t: object) -> None:
        return None

    monkeypatch.setattr(
        "app.services.task_stage_service.TaskStageService.initialize_stages",
        staticmethod(_noop_init),
    )
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(
        "worker.celery_app.celery_app.send_task",
        lambda name, args=None, kwargs=None: sent.append({"name": name, "args": args}),
    )
    return sent


def test_normalize_ingest_url_strips_fragment_and_trailing_slash() -> None:
    a = TaskService._normalize_ingest_url("HTTPS://WWW.Bilibili.com/video/BV1/#play")
    b = TaskService._normalize_ingest_url("https://www.bilibili.com/video/BV1")
    assert a == b == "https://www.bilibili.com/video/BV1"


def test_url_hash_prefix_differs_from_youtube_hash() -> None:
    url_hash = TaskService._generate_content_hash(
        f"url:{TaskService._normalize_ingest_url('https://youtu.be/dQw4w9WgXcQ')}"
    )
    yt_hash = TaskService._generate_content_hash("youtube:dQw4w9WgXcQ")
    assert url_hash != yt_hash


@pytest.mark.asyncio
async def test_create_url_task_dispatches_process_youtube(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _patch_common(monkeypatch)
    db = _FakeAsyncSession()
    user = types.SimpleNamespace(id=uuid4(), email="t@example.com")
    data = TaskCreateRequest(
        source_type="url",
        source_url="https://www.bilibili.com/video/BV1xx",
        options=TaskOptions(),
    )

    task = await TaskService.create_task(db, user, data, trace_id="trace-url")

    assert task.status == "queued"
    assert task.source_type == "url"
    assert task.source_url == "https://www.bilibili.com/video/BV1xx"
    assert task.content_hash  # 由规范化 URL 生成，去重照常
    assert len(sent) == 1
    assert sent[0]["name"] == "worker.tasks.process_youtube"
    assert sent[0]["args"] == [task.id]


@pytest.mark.asyncio
async def test_create_url_task_missing_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.exceptions import BusinessError

    _patch_common(monkeypatch)
    db = _FakeAsyncSession()
    user = types.SimpleNamespace(id=uuid4(), email="t@example.com")
    data = TaskCreateRequest(source_type="url", source_url=None, options=TaskOptions())

    with pytest.raises(BusinessError):
        await TaskService.create_task(db, user, data, trace_id="t")
