from __future__ import annotations

import inspect

from worker.tasks import sync_youtube_videos


def test_reauth_path_uses_notification_service_with_dedup_key() -> None:
    """規格 §5.1：reauth 升为持久化通知，走 notify(YOUTUBE_REAUTH_REQUIRED, dedup_key=reauth:{user}:{utc_date})。"""
    src = inspect.getsource(sync_youtube_videos.sync_channel_videos)
    assert "NotificationType.YOUTUBE_REAUTH_REQUIRED" in src
    # dedup_key 形状：reauth:{user_id}:{utc_date}
    assert 'f"reauth:{user_id}:' in src
    # 不再用裸 WS payload 发 youtube_reauth_required（已被 notify 取代）
    assert '"type": "youtube_reauth_required"' not in src


def test_module_exposes_notification_service() -> None:
    assert hasattr(sync_youtube_videos, "NotificationService")
    assert hasattr(sync_youtube_videos, "NotificationType")
