from __future__ import annotations

import asyncio

from celery import Celery
from celery.schedules import crontab
from celery.signals import after_setup_logger, worker_process_init

from app.config import settings
from app.core.config_manager import ConfigManager
from app.core.logging_config import configure_logging
from worker.db import worker_async_session_factory


@after_setup_logger.connect
def _setup_trace_logging(*args, **kwargs) -> None:
    """给 Celery 配好的 logger 补挂 TraceIdFilter + trace_id Formatter(幂等)。"""
    configure_logging()


@worker_process_init.connect
def _setup_trace_logging_per_child(*args, **kwargs) -> None:
    """prefork 子进程启动时再配一次(每个子进程独立的 logging 状态);configure_logging 幂等。"""
    configure_logging()


def _get_redis_url() -> str:
    redis_url = settings.REDIS_URL
    if not redis_url:
        raise RuntimeError("REDIS_URL is not set")
    return redis_url


celery_app = Celery(
    "ai_audio_assistant",
    broker=_get_redis_url(),
    backend=_get_redis_url(),
)

celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.accept_content = ["json"]
celery_app.conf.timezone = "UTC"

# --- 任务硬超时全局兜底 ---
# per-task 的 time_limit/soft_time_limit 会覆盖这里;本兜底只为漏配硬超时的任务
# (quota_alert / cleanup_task / regenerate_summary 等当前都没有任何 time_limit)兜一个绝对上限,
# 防止某个任务卡死后无限占用 worker。须高于最长的 per-task soft(sync_all_subscriptions_videos
# 的 soft=3600),否则会把合法的长任务提前杀掉。
celery_app.conf.task_soft_time_limit = 3900
celery_app.conf.task_time_limit = 4200

# --- worker 可靠性 ---
# 单队列里长 ASR(~30min)与短 summary/image 任务混跑:
#   prefetch=1            —— 公平分发,避免一个长任务把预取的短任务全堵在队头;
#   acks_late + reject_on_worker_lost —— 被 OOM-SIGKILL 的任务消息会重投(autoretry_for 抓不到
#                          SIGKILL,否则任务静默消失);asr_idempotency 已让整任务重跑幂等,重投安全;
#   max_tasks_per_child   —— 回收 prefork 子进程,约束 ffmpeg/transcript 的内存蠕变(对抗 1g 上限)。
celery_app.conf.worker_prefetch_multiplier = 1
celery_app.conf.task_acks_late = True
celery_app.conf.task_reject_on_worker_lost = True
celery_app.conf.worker_max_tasks_per_child = 100

# Celery Beat 定时任务配置
#
# 切勿给任务加 options.queue —— worker 启动无 -Q,只消费默认队列(task_default_queue 未设
# → celery 内建的 'celery')。历史上这里把 queue 钉成 'default',定时消息全发往一个没有
# 消费者的队列、堆积成死信(实测 default 积压 ~4978),所有定时任务从未真正执行过。
# 守卫见 tests/worker/test_beat_queue_routing.py(从 docker-compose worker 命令反推消费队列)。
celery_app.conf.beat_schedule = {
    # ASR 配额预警检查 - 每小时执行一次
    "check-asr-quota-alerts": {
        "task": "worker.tasks.quota_alert.check_asr_quota_alerts",
        "schedule": crontab(minute=0),  # 每小时整点执行
    },
    # YouTube 智能同步检查 - 每小时 :30 执行(按各频道 next_sync_at 自适应触发,分批选取)。
    # 这是 YouTube 视频同步的唯一定时入口:它会选中 next_sync_at 为 NULL(从未同步)或已到期的
    # 频道,因此无需再叠加「每日全量同步」(后者忽略 next_sync_at、无批量上限,会在固定时刻把所有
    # 频道一次性扇出造成惊群)。全量回填如有需要,可手动触发 sync_all_subscriptions_videos 任务。
    "check-youtube-scheduled-syncs": {
        "task": "worker.tasks.sync_youtube_videos.check_scheduled_syncs",
        "schedule": crontab(minute=30),  # 每小时 30 分执行
    },
    # 死任务兜底巡检 - 每 15min:重派卡 pending 的配图 + 把卡非终态超时的任务标 failed。
    # 不设 options.queue(worker 无 -Q,只消费默认 celery 队列;历史死信教训)。
    "run-dead-task-sweep": {
        "task": "worker.tasks.run_dead_task_sweep",
        "schedule": crontab(minute="*/15"),
    },
    # 审核卫生巡检 - 每小时 :05:缓存 GC + flagged last_title 脱敏 backfill。
    # 不设 options.queue(worker 无 -Q,只消费默认 celery 队列;历史死信教训)。
    "run-moderation-hygiene": {
        "task": "worker.tasks.run_moderation_hygiene",
        "schedule": crontab(minute=5),
    },
}

ConfigManager.configure_db(worker_async_session_factory, cache_ttl_seconds=settings.CONFIG_CENTER_CACHE_TTL)
if settings.CONFIG_CENTER_DB_ENABLED:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(ConfigManager.refresh_from_db())
    else:
        loop.create_task(ConfigManager.refresh_from_db())

from app.services.asr import (  # noqa: F401, E402
    aliyun,  # noqa: F401, E402
    tencent,
    volcengine,
)
from app.services.asr import configs as asr_configs  # noqa: F401, E402

# Import all service modules to trigger @register_service decorators
# This ensures services are registered in the ServiceRegistry
from app.services.llm import configs as llm_configs  # noqa: F401, E402
from app.services.llm import image_service as _llm_image_service  # noqa: F401, E402
from app.services.llm import proxy as _llm_proxy  # noqa: F401, E402
from app.services.storage import configs as storage_configs  # noqa: F401, E402
from app.services.storage import cos, minio, oss, tos  # noqa: F401, E402

# Import tasks to register them with Celery
# Must import after celery_app is created to avoid circular imports
from worker.tasks import (
    cleanup_task,  # noqa: F401, E402
    dead_task_sweeper,  # noqa: F401, E402
    download_youtube,  # noqa: F401, E402
    moderation_hygiene,  # noqa: F401, E402
    process_audio,  # noqa: F401, E402
    process_youtube,  # noqa: F401, E402
    quota_alert,  # noqa: F401, E402
    regenerate_summary,  # noqa: F401, E402
    summary_image_task,  # noqa: F401, E402
    sync_youtube_subscriptions,  # noqa: F401, E402
    sync_youtube_videos,  # noqa: F401, E402
    youtube_auto_transcribe,  # noqa: F401, E402
    youtube_summary_style_recommendation,  # noqa: F401, E402
)
