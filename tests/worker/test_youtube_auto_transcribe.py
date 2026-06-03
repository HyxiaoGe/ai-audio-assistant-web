from __future__ import annotations

import inspect

from worker.tasks import youtube_auto_transcribe


def test_process_single_video_no_longer_creates_auto_transcribe_started_notification() -> None:
    """规格 §5.1：去掉 auto_transcribe_started；不再手搓 Notification、不再推送该事件。"""
    src = inspect.getsource(youtube_auto_transcribe._process_single_video)
    # 不再写库 Notification 行
    assert "Notification(" not in src
    # 不再发 auto_transcribe_started WS 事件
    assert "auto_transcribe_started" not in src


def test_module_no_longer_imports_notification_model() -> None:
    """模块级 Notification 模型 import 应随死代码一并移除。"""
    assert not hasattr(youtube_auto_transcribe, "Notification")
