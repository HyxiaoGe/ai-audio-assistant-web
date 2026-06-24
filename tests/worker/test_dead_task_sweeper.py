"""死任务兜底巡检:配图重派(1a) + 卡死任务标 failed(1b) + 总开关/独立容错。

worker 测试约定:importlib 加载模块,monkeypatch 其模块级 import(session/redis/celery_app/函数)。
session 用 fake——execute(stmt) 返回预置 rows 的 _FakeResult,验证逐行处理逻辑;WHERE 过滤
(陈旧/completed/is_active/非终态/limit)由 DB 强制,经审阅保证,不在单测内复现。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.i18n.codes import ErrorCode


def _load() -> Any:
    p = Path(__file__).resolve().parents[2] / "worker" / "tasks" / "dead_task_sweeper.py"
    spec = importlib.util.spec_from_file_location("dead_task_sweeper_uut", p)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


dts = _load()


class _FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._rows)


class _FakeSession:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows
        self.commits = 0

    def execute(self, *a: Any, **k: Any) -> _FakeResult:
        return _FakeResult(self._rows)

    def commit(self) -> None:
        self.commits += 1


class _FakeRedis:
    """SETNX cooldown:held 集合里的 key 视为已占用 → 返回 None(跳过)。"""

    def __init__(self, held: set[str] | None = None) -> None:
        self.held = held or set()
        self.set_keys: list[str] = []
        self.deleted: list[str] = []

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool | None:
        self.set_keys.append(key)
        if key in self.held:
            return None
        self.held.add(key)
        return True

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.held.discard(key)


class _SpyCelery:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send_task(self, name: str, **kwargs: Any) -> Any:
        self.sent.append({"name": name, **kwargs})
        return SimpleNamespace(id="x")


def _summary(images: list[dict[str, Any]] | None, sid: str = "s1") -> SimpleNamespace:
    return SimpleNamespace(id=sid, content="正文", images=images)


def _task(tid: str = "t1", options: dict[str, Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=tid, user_id="u1", options=options or {})


# ---------- 1a: 配图重派 ----------


def test_reconcile_dispatches_for_pending_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _SpyCelery()
    monkeypatch.setattr(dts, "celery_app", spy)
    monkeypatch.setattr(dts, "get_sync_redis_client", lambda: _FakeRedis())
    rows = [(_summary([{"placeholder": "P1", "status": "pending"}]), _task(options={"summary_style": "lecture"}))]
    n = dts._reconcile_stuck_image_slots(_FakeSession(rows))
    assert n == 1
    assert len(spy.sent) == 1
    sent = spy.sent[0]
    assert sent["name"] == "worker.tasks.generate_summary_images_async"
    assert sent["kwargs"]["summary_id"] == "s1"
    assert sent["kwargs"]["task_id"] == "t1"
    assert sent["kwargs"]["content_style"] == "lecture"  # 经 normalize_content_style(options.summary_style)


def test_reconcile_skips_when_no_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _SpyCelery()
    monkeypatch.setattr(dts, "celery_app", spy)
    monkeypatch.setattr(dts, "get_sync_redis_client", lambda: _FakeRedis())
    rows = [(_summary([{"placeholder": "P1", "status": "ready"}]), _task())]
    n = dts._reconcile_stuck_image_slots(_FakeSession(rows))
    assert n == 0
    assert spy.sent == []


def test_reconcile_respects_cooldown_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _SpyCelery()
    monkeypatch.setattr(dts, "celery_app", spy)
    cooldown_key = "summary:imgreconcile:lock:s1"
    monkeypatch.setattr(dts, "get_sync_redis_client", lambda: _FakeRedis(held={cooldown_key}))
    rows = [(_summary([{"placeholder": "P1", "status": "pending"}]), _task())]
    n = dts._reconcile_stuck_image_slots(_FakeSession(rows))
    assert n == 0  # 近期已派发,防抖
    assert spy.sent == []


class _BoomCelery:
    def send_task(self, name: str, **kwargs: Any) -> Any:
        raise RuntimeError("broker down")


def test_reconcile_releases_cooldown_when_dispatch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dts, "celery_app", _BoomCelery())
    redis = _FakeRedis()
    monkeypatch.setattr(dts, "get_sync_redis_client", lambda: redis)
    rows = [(_summary([{"placeholder": "P1", "status": "pending"}]), _task())]
    n = dts._reconcile_stuck_image_slots(_FakeSession(rows))
    assert n == 0  # 派发失败不计数
    assert "summary:imgreconcile:lock:s1" in redis.deleted  # 冷却锁已释放,下轮可重试


# ---------- 1b: 卡死任务标 failed ----------


def test_fail_stuck_marks_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    t = SimpleNamespace(id="t1", status="summarizing", progress=70, error_code=None, error_message=None)
    session = _FakeSession([t])
    n = dts._fail_stuck_tasks(session)
    assert n == 1
    assert t.status == "failed"
    assert t.progress == 0
    assert t.error_code == ErrorCode.TASK_STALLED.value
    assert t.error_message and "失败" in t.error_message
    assert session.commits == 1


def test_fail_stuck_no_rows_no_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession([])
    n = dts._fail_stuck_tasks(session)
    assert n == 0
    assert session.commits == 0


# ---------- 壳层:总开关 + 独立容错 ----------


def test_sweep_disabled_skips_both(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dts.settings, "DEAD_TASK_SWEEP_ENABLED", False)
    called = {"img": 0, "task": 0}
    monkeypatch.setattr(
        dts, "_reconcile_stuck_image_slots", lambda s: called.__setitem__("img", called["img"] + 1) or 0
    )
    monkeypatch.setattr(dts, "_fail_stuck_tasks", lambda s: called.__setitem__("task", called["task"] + 1) or 0)
    out = dts.run_dead_task_sweep()
    assert out.get("skipped") == 1
    assert called == {"img": 0, "task": 0}


def test_sweep_image_failure_does_not_block_task_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dts.settings, "DEAD_TASK_SWEEP_ENABLED", True)
    monkeypatch.setattr(dts, "get_sync_db_session", lambda: _CtxSession())
    ran = {"task": 0}

    def _boom(_s: Any) -> int:
        raise RuntimeError("image sweep boom")

    monkeypatch.setattr(dts, "_reconcile_stuck_image_slots", _boom)
    monkeypatch.setattr(dts, "_fail_stuck_tasks", lambda s: ran.__setitem__("task", 1) or 3)
    out = dts.run_dead_task_sweep()
    assert ran["task"] == 1  # 图巡检抛错不拖累任务巡检
    assert out["tasks_failed"] == 3
    assert out["images_reconciled"] == 0


class _CtxSession(_FakeSession):
    def __init__(self) -> None:
        super().__init__([])

    def __enter__(self) -> _CtxSession:
        return self

    def __exit__(self, *a: Any) -> bool:
        return False
