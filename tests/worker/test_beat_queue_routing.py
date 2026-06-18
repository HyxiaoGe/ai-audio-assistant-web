"""守卫:Celery Beat 定时任务必须发到 worker 真正消费的队列。

事故根因:worker(docker-compose)以 `celery ... worker --autoscale=8,1` 启动,没有 `-Q`,
因此只消费默认队列(task_default_queue,未设则为 celery 内建的 'celery')。而 beat_schedule
里三个定时任务都把 options.queue 钉成 'default' —— 这个队列没有任何消费者,定时触发的消息
全部堆积成死信(实测 redis LLEN default ≈ 4978,celery = 0),导致 YouTube 自动同步、配额
预警等所有定时任务从未真正执行过。

本守卫从两端取真相并交叉校验,以免单看一侧再次跑偏:
- worker 端:解析 docker-compose.yml 中 worker.command 的 `-Q`,得出实际消费的队列集合;
- beat 端:遍历 celery_app.conf.beat_schedule,取每个任务 options.queue(未设=默认队列)。
任一 beat 任务的目标队列不在 worker 消费集合内 → 失败。
"""

from __future__ import annotations

import shlex
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"


def _consumed_queues_from_compose(default_queue: str) -> set[str]:
    """从 docker-compose 的 worker 命令解析 `-Q`/`--queues` 队列集合;无则消费默认队列。"""
    compose = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    cmd = compose["services"]["worker"]["command"]
    tokens = shlex.split(cmd if isinstance(cmd, str) else " ".join(cmd))
    queues: set[str] = set()
    for i, tok in enumerate(tokens):
        if tok in ("-Q", "--queues") and i + 1 < len(tokens):
            queues.update(tokens[i + 1].split(","))
        elif tok.startswith("-Q") and len(tok) > 2:
            queues.update(tok[2:].split(","))
        elif tok.startswith("--queues="):
            queues.update(tok.split("=", 1)[1].split(","))
    return queues or {default_queue}


def test_every_beat_task_routes_to_a_consumed_queue() -> None:
    from worker.celery_app import celery_app

    default_queue = celery_app.conf.task_default_queue or "celery"
    consumed = _consumed_queues_from_compose(default_queue)

    offenders: dict[str, str] = {}
    for name, entry in celery_app.conf.beat_schedule.items():
        target = (entry.get("options") or {}).get("queue") or default_queue
        if target not in consumed:
            offenders[name] = target

    assert not offenders, (
        f"以下 Beat 定时任务发往无消费者的队列(worker 实际消费 {sorted(consumed)}),"
        f"消息会堆积成死信、定时任务永不执行:{offenders}"
    )


def test_daily_full_sync_is_not_scheduled() -> None:
    """每日全量同步(sync_all_subscriptions_videos)不再进 Beat 调度。

    它忽略 next_sync_at、无批量上限,会在固定时刻(UTC 3:00)把所有频道一次性扇出(惊群),
    与自适应的 check_scheduled_syncs 完全重复。后者每小时按 `next_sync_at IS NULL OR <= now`
    分批(batch_size)选取,已覆盖「从未同步」的频道,是唯一的调度入口。任务函数本身保留,
    供管理员/手动按需触发全量回填。
    """
    from worker.celery_app import celery_app

    scheduled = {entry["task"] for entry in celery_app.conf.beat_schedule.values()}
    assert "worker.tasks.sync_youtube_videos.sync_all_subscriptions_videos" not in scheduled, (
        "每日全量同步应已下线,改由自适应的 check_scheduled_syncs 单一入口承担"
    )
    assert "worker.tasks.sync_youtube_videos.check_scheduled_syncs" in scheduled, "自适应小时检查必须在调度中"
