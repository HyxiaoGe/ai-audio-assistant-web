"""守卫：后台频道同步不得对每条新同步视频投机预热摘要风格推荐(Tier 2-lite 收敛)。

预热已收敛到「用户真看到的集合」:
  - 前端在加载订阅/视频 feed 时,对它实际渲染的那批 video_id 调
    POST /youtube/videos/summary-style-recommendations/prewarm(限流、用户驱动);
  - 单个视频打开走 GET /youtube/videos/{id}/summary-style-recommendation 按需计算。
后台 sync_channel_videos 再对每条新同步视频扇出预热 = 纯冗余浪费(用户多数永不打开、
前端真看时会幂等再触发命中缓存),且曾是 prewarm 突发导致 asyncpg 跨事件循环报错的源头。
故禁止后台同步任务里再出现投机预热扇出。

注:API 端点触发的预热(app/api/v1/youtube.py)是用户驱动的,不在此禁。
"""

from __future__ import annotations

import inspect

from worker.tasks import sync_youtube_videos


def test_channel_sync_does_not_speculatively_prewarm() -> None:
    src = inspect.getsource(sync_youtube_videos.sync_channel_videos)
    assert "prewarm_youtube_summary_style_recommendations" not in src, (
        "后台频道同步不得投机预热;预热由前端按真实展示集驱动(POST prewarm 端点)/用户打开走 GET 按需。"
    )
    # 自动转写仍保留(受 subscription.auto_transcribe 开关控制,默认 false),不受本次收敛影响。
    assert "process_auto_transcriptions" in src
