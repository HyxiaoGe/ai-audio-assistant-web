"""死任务巡检已接入 beat,且不钉死队列(沿用 test_beat_queue_routing 的死信教训)。"""

from __future__ import annotations

from worker.celery_app import celery_app


def test_dead_task_sweep_in_beat_schedule() -> None:
    schedule = celery_app.conf.beat_schedule
    assert "run-dead-task-sweep" in schedule
    entry = schedule["run-dead-task-sweep"]
    assert entry["task"] == "worker.tasks.run_dead_task_sweep"
    # 绝不设 options.queue(worker 无 -Q,只消费默认 celery 队列)
    assert "queue" not in (entry.get("options") or {})


def test_dead_task_sweep_task_registered() -> None:
    assert "worker.tasks.run_dead_task_sweep" in celery_app.tasks
