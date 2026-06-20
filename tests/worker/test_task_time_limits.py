"""守卫:金路径 Celery 任务必须有真正生效的硬超时 + worker 可靠性配置。

事故根因:`hard_time_limit` 不是合法的 Celery 任务选项(正确的是 `time_limit`),会被当未知
kwarg 静默忽略 —— process_audio / process_youtube / download_youtube 三个最重的金路径任务
因此长期**没有硬超时**。软超时(soft_time_limit=1800)抛出的 SoftTimeLimitExceeded 是普通
Exception,若落在摘要阶段「记日志、不 raise、继续收尾」的 except 分支(process_audio.py:1058/
1119/1217)会被吞掉,任务可无上限地跑下去、并把一笔已付费的 ~30min ASR/LLM 任务白白拖死。
只有进程级的硬 `time_limit`(到点强杀 worker 子进程、不可被任何 except 吞)才是真正的兜底。

summary_image_task.py:80-87 已经用 time_limit 修过同一个坑(其装饰器注释明确写了
「celery 的硬超时参数名是 time_limit,不是 hard_time_limit」),本守卫把该约定固化到三个金路径
任务,并防 inert-attribute 复发(改回 hard_time_limit 一眼看不出、极易回潮)。

同时固化 worker 可靠性配置:1g 内存上限下被 OOM-SIGKILL 的长任务,若无 acks_late/
reject_on_worker_lost 其消息永不重投(autoretry_for 抓不到 SIGKILL)→ 静默丢任务。
asr_idempotency 已让整任务重跑幂等,故重投安全。
"""

from __future__ import annotations

import pathlib

import pytest

MONEY_PATH_TASKS = [
    "worker.tasks.process_audio",
    "worker.tasks.process_youtube",
    "worker.tasks.download_youtube",
]

# 三个金路径任务装饰器源码路径(用于 inert-attribute 回潮守卫)
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MONEY_PATH_FILES = [
    "worker/tasks/process_audio.py",
    "worker/tasks/process_youtube.py",
    "worker/tasks/download_youtube.py",
]


def _task(name: str):
    from worker.celery_app import celery_app

    return celery_app.tasks[name]


@pytest.mark.parametrize("name", MONEY_PATH_TASKS)
def test_money_path_task_has_real_hard_time_limit(name: str) -> None:
    """time_limit 是 celery 真正生效的硬超时(进程级强杀,不可被 except 吞)。"""
    task = _task(name)
    assert task.time_limit is not None, (
        f"{name} 缺硬超时 time_limit —— 可能误用了被静默忽略的 hard_time_limit,任务实际无硬上限"
    )
    assert task.time_limit == 2000


@pytest.mark.parametrize("name", MONEY_PATH_TASKS)
def test_money_path_soft_below_hard(name: str) -> None:
    """软超时必须 < 硬超时,保证 SoftTimeLimitExceeded 先于强杀触发,给任务清理机会。"""
    task = _task(name)
    assert task.soft_time_limit is not None
    assert task.soft_time_limit < task.time_limit


@pytest.mark.parametrize("rel", _MONEY_PATH_FILES)
def test_no_inert_hard_time_limit_kwarg_in_source(rel: str) -> None:
    """守卫 inert 的 ``hard_time_limit=`` kwarg 不再出现在金路径任务装饰器里(防回潮)。

    只查 kwarg 赋值形式(``hard_time_limit=``),允许注释里以散文形式提及该词作警示,
    与 summary_image_task.py 的装饰器注释同风格。
    """
    text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
    assert "hard_time_limit=" not in text, f"{rel} 仍含被 celery 静默忽略的 inert hard_time_limit= kwarg"


def test_global_time_limit_backstop() -> None:
    """全局兜底:任何漏配硬超时的任务(regenerate_summary/cleanup_task/quota_alert 等)
    也有绝对上限。须高于最长 per-task soft(sync_all=3600)以免误杀合法长任务。"""
    from worker.celery_app import celery_app

    conf = celery_app.conf
    assert conf.task_time_limit is not None
    assert conf.task_time_limit >= 3600
    assert conf.task_soft_time_limit is not None
    assert conf.task_soft_time_limit < conf.task_time_limit


def test_worker_reliability_config() -> None:
    """worker 可靠性:长任务公平分发 + OOM-SIGKILL 后消息重投 + 子进程回收防内存蠕变。"""
    from worker.celery_app import celery_app

    conf = celery_app.conf
    # 单队列里长 ASR 与短 summary/image 混跑,prefetch=1 避免队头阻塞
    assert conf.worker_prefetch_multiplier == 1
    # acks_late + reject_on_worker_lost:被 SIGKILL 的任务消息重投(autoretry 抓不到 SIGKILL)
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    # 回收 prefork 子进程,约束 ffmpeg/transcript 内存蠕变(对抗 1g 上限)
    assert conf.worker_max_tasks_per_child is not None
    assert conf.worker_max_tasks_per_child > 0
