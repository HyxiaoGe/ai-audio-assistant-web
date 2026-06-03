from __future__ import annotations

from typing import Any

import pytest

from app.models.asr_usage import ASRUsage
from app.models.task import Task
from app.models.transcript import Transcript
from worker.tasks import process_youtube


class _FakeSyncQuery:
    """Minimal chainable stand-in for a SQLAlchemy ``Session.query(...)`` chain."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def filter(self, *args: Any, **kwargs: Any) -> _FakeSyncQuery:
        return self

    def order_by(self, *args: Any, **kwargs: Any) -> _FakeSyncQuery:
        return self

    def all(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeSyncSession:
    """Returns preloaded transcripts / claim rows by queried model, no database."""

    def __init__(self, transcripts: list[Transcript], claim: ASRUsage | None) -> None:
        self._transcripts = transcripts
        self._claim = claim

    def query(self, model: Any) -> _FakeSyncQuery:
        name = getattr(model, "__name__", str(model))
        if name == "Transcript":
            return _FakeSyncQuery(self._transcripts)
        if name == "ASRUsage":
            return _FakeSyncQuery([self._claim] if self._claim is not None else [])
        return _FakeSyncQuery([])


def _task() -> Task:
    return Task(
        user_id="user-1",
        content_hash="hash-1",
        title="demo",
        source_type="youtube",
        source_url="https://example.com/v",
        duration_seconds=120.0,
    )


def _transcript(task: Task) -> Transcript:
    return Transcript(
        task_id=task.id,
        speaker_id="1",
        content="hello",
        start_time=0.0,
        end_time=2.0,
        confidence=0.9,
        sequence=1,
    )


def test_finalize_existing_cost_sync_tolerates_provider_lookup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FINALIZE_COST must record cost even when the ASR service can't be built.

    The provider lookup only feeds the optional ``estimate_cost``. If it raises
    (registry / credential / instantiation failure, force_new=True), the cost
    must still be recorded atomically from the claim's provider/variant -- not
    propagate and autoretry the youtube task into a stuck, never-charged state.
    """
    task = _task()
    claim = ASRUsage(
        user_id=str(task.user_id),
        task_id=str(task.id),
        provider="volcengine",
        variant="file",
        duration_seconds=0.0,
        status="processing",
    )
    session = _FakeSyncSession([_transcript(task)], claim)

    async def _raising_get_service(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("asr service construction failed")

    finalize_calls: list[dict[str, Any]] = []

    def _spy_finalize(*args: Any, **kwargs: Any) -> None:
        finalize_calls.append(kwargs)

    monkeypatch.setattr(process_youtube.SmartFactory, "get_service", _raising_get_service)
    monkeypatch.setattr(process_youtube, "_finalize_asr_cost_sync", _spy_finalize)

    # Must NOT raise even though the provider lookup blows up.
    process_youtube._finalize_existing_transcript_cost_sync(session, task, str(task.id))

    assert len(finalize_calls) == 1
    assert finalize_calls[0]["asr_service"] is None  # fell back; lookup failure swallowed
    assert finalize_calls[0]["provider_name"] == "volcengine"  # cost keyed off the claim
    assert finalize_calls[0]["claim_row"] is claim  # finalized in place, no duplicate row


# --------------------------------------------------------------------------- #
# Phase 2: kind="task_progress" 信封标签断言
# --------------------------------------------------------------------------- #
class _CaptureSyncPublish:
    """捕获 process_youtube.publish_task_update_sync 的 message 以断言信封 kind。"""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def __call__(self, task_id: str, user_id: str, message: str) -> None:
        self.messages.append(message)


class _FakeCommitSession:
    """最小同步 Session 替身：commit/add 均为空操作，不依赖真实 DB。"""

    def commit(self) -> None:
        pass

    def add(self, item: Any) -> None:
        pass


def test_process_youtube_progress_envelope_has_task_progress_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    capture = _CaptureSyncPublish()
    monkeypatch.setattr(process_youtube, "publish_task_update_sync", capture)

    task = _task()
    session = _FakeCommitSession()

    # _update_task 是同步函数，可直接调用（无需 asyncio）
    process_youtube._update_task(session, task, "transcribing", 50, "transcribing", None)

    assert capture.messages, "expected at least one published progress message"
    for raw in capture.messages:
        assert json.loads(raw)["kind"] == "task_progress"


def test_process_youtube_failure_envelope_has_task_progress_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    import app.models.notification as notif_module
    from app.core.exceptions import BusinessError
    from app.i18n.codes import ErrorCode

    capture = _CaptureSyncPublish()
    monkeypatch.setattr(process_youtube, "publish_task_update_sync", capture)

    # Phase 4 会把 _mark_failed 改调 NotificationService。
    # 当前 _mark_failed 仍以 Notification(action="failed", ...) 构造——action 列已在
    # Phase 1 删除，SQLAlchemy 2.x 会因未知 kwarg 抛 TypeError，被 _mark_failed 的 except
    # 吞掉而到不了 publish。这里把 Notification 打桩为空操作，让本用例能验到发布信封带
    # kind（与 process_audio 那几个已知红测同源，Phase 4 修复 producer 后移除本桩）。
    class _FakeNotification:
        def __init__(self, **kwargs: Any) -> None:
            pass

    monkeypatch.setattr(notif_module, "Notification", _FakeNotification)

    task = _task()
    session = _FakeCommitSession()
    error = BusinessError(ErrorCode.ASR_SERVICE_FAILED)

    process_youtube._mark_failed(session, task, error, None)

    assert task.status == "failed"
    assert capture.messages, "expected a failure progress message"
    assert json.loads(capture.messages[-1])["kind"] == "task_progress"


def test_process_youtube_completed_calls_notify_task_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """YouTube 任务完成改调 NotificationService.notify(TASK_COMPLETED)。"""
    from app.services.notifications.types import NotificationType

    task = _task()  # duration_seconds=120.0, title="demo"
    calls: list[dict[str, Any]] = []

    def _spy_notify(sess: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(process_youtube.NotificationService, "notify", staticmethod(_spy_notify))
    monkeypatch.setattr(process_youtube, "publish_task_update_sync", lambda *a, **k: None)

    class _Sess:
        def commit(self) -> None:
            return None

    process_youtube._update_task(_Sess(), task, "completed", 100, "completed", "req-1")

    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["type"] == NotificationType.TASK_COMPLETED
    assert kwargs["user_id"] == str(task.user_id)
    assert kwargs["task_id"] == str(task.id)
    assert kwargs["params"]["task_title"] == "demo"
    assert kwargs["params"]["duration"] == 120.0
