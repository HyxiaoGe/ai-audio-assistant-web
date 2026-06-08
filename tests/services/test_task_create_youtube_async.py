"""创建 YouTube 任务必须「解耦/异步」：不在创建请求路径上做同步 yt-dlp 校验。

回归守卫：旧实现 await extract_info、给 20s 总超时阻塞请求——国内直连抖动 >20s 即误杀，
任务还没入库就抛 51300，前端只见「卡顿/失败」且 DB 查不到（不可见失败）。现改为立即入库
queued 并派发 process_youtube，由 worker 的 RESOLVE_YOUTUBE 阶段真正解析、回填标题、
失败也记成可见可重试的 failed 任务。本测试锁定：创建路径只入库 + 派发，绝不触网。
"""

from __future__ import annotations

import types
from uuid import uuid4

import pytest

from app.schemas.task import TaskCreateRequest, TaskOptions
from app.services.task_service import TaskService


class _FakeResult:
    def scalar_one_or_none(self) -> None:
        return None  # 无同内容历史任务 → 不触发去重分支


class _FakeAsyncSession:
    """最小异步会话桩：满足 execute/add/commit/refresh，不做真实 DB 落库。"""

    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0

    async def execute(self, *args: object, **kwargs: object) -> _FakeResult:
        return _FakeResult()

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, obj: object) -> None:
        # 模拟 DB 回填主键（真实库在 flush 时赋值）。
        if getattr(obj, "id", None) is None:
            obj.id = str(uuid4())


@pytest.mark.asyncio
async def test_create_youtube_task_inserts_queued_and_dispatches_without_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 跳过配额预检（管理员路径）与阶段初始化；捕获 celery 派发。
    monkeypatch.setattr("app.services.task_service.is_admin_user", lambda _user: True)

    async def _noop_init_stages(_db: object, _task: object) -> None:
        return None

    monkeypatch.setattr(
        "app.services.task_stage_service.TaskStageService.initialize_stages",
        staticmethod(_noop_init_stages),
    )

    sent: list[dict[str, object]] = []

    def _capture_send_task(name: str, args: list[object] | None = None, kwargs: dict[str, object] | None = None) -> None:
        sent.append({"name": name, "args": args, "kwargs": kwargs})

    monkeypatch.setattr("worker.celery_app.celery_app.send_task", _capture_send_task)

    db = _FakeAsyncSession()
    user = types.SimpleNamespace(id=uuid4(), email="t@example.com")
    data = TaskCreateRequest(
        source_type="youtube",
        source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        options=TaskOptions(),
    )

    task = await TaskService.create_task(db, user, data, trace_id="trace-1")

    # 立即入库为 queued（不阻塞、不预先解析标题）。
    assert task.status == "queued"
    assert task.source_type == "youtube"
    assert task.source_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert task.title is None  # 不再在创建期预填标题，交给 worker resolve 回填
    assert task.content_hash  # content_hash 仅靠正则取 video_id（不触网）仍生成，去重照常
    assert db.added and db.added[0] is task

    # 已派发 process_youtube（worker 负责解析/下载/可见失败）。
    assert len(sent) == 1
    assert sent[0]["name"] == "worker.tasks.process_youtube"
    assert sent[0]["args"] == [task.id]


def test_blocking_validation_method_is_removed() -> None:
    # 防止有人把同步阻塞校验重新加回创建路径（这正是「卡顿 + 不可见失败」的根因）。
    assert not hasattr(TaskService, "_validate_youtube_video")
    assert not hasattr(TaskService, "_validate_youtube_video_sync")
