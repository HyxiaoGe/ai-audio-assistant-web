"""守卫：YouTube 同步任务必须对 HttpError 做配额感知的软着陆（静态接线检查）。

worker 同步任务无法在单测里跑真实同步 DB 路径（sync_engine 在测试用的是 aiosqlite URL），
故沿用本仓既有约定（见 test_sync_youtube_reauth.py），用 inspect.getsource 校验接线：
  - 用 classify_youtube_http_error 分类，而非把所有 HttpError 一律就地咽掉；
  - 配额耗尽返回 status="quota_exceeded"，不再伪装成 success（避免污染发布频率统计/调度）。
分类器本身的行为由 tests/services/test_youtube_api_errors.py 做真 RED→GREEN 覆盖。
"""

from __future__ import annotations

import inspect

from worker.tasks import sync_youtube_subscriptions, sync_youtube_videos


def test_channel_sync_classifies_and_softlands_on_quota() -> None:
    src = inspect.getsource(sync_youtube_videos.sync_channel_videos)
    assert "classify_youtube_http_error" in src, "频道同步须用分类器区分配额/瞬态/其它"
    assert '"quota_exceeded"' in src, "配额耗尽须返回 quota_exceeded，不得伪装成 success 并推进调度"
    # update_publish_stats 仍保留（正常路径推进调度），但 quota 分支须在它之前直接返回
    assert "update_publish_stats" in src


def test_subscription_sync_classifies_quota() -> None:
    src = inspect.getsource(sync_youtube_subscriptions.sync_youtube_subscriptions)
    assert "classify_youtube_http_error" in src
    assert '"quota_exceeded"' in src
