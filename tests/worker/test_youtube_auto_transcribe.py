from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace

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


def test_process_single_video_skips_blocked_channel(monkeypatch) -> None:
    """路径C: 订阅频道在黑名单内 → 返回 channel_blocked skip, 不建 Task, 写 log。"""
    from app.models.task import Task
    from app.models.youtube_auto_transcribe_log import YouTubeAutoTranscribeLog
    from app.services.youtube import blocklist_service
    from app.services.youtube.blocklist_service import Blocklist
    from worker.tasks.youtube_auto_transcribe import _process_single_video

    BLOCKED_CHANNEL_ID = "UCblockedXXXXXXXXXXXXXXXX"
    VIDEO_ID = "vid_blocked_001"

    fake_bl = Blocklist(
        terms=frozenset(),
        channel_ids=frozenset({BLOCKED_CHANNEL_ID}),
        channel_names=frozenset(),
        channel_handles=frozenset(),
    )

    class FakeResult:
        def __init__(self, value):
            self._value = value

        def scalar_one_or_none(self):
            return self._value

    class FakeSession:
        def __init__(self):
            self.added: list = []

        def execute(self, stmt):
            # existing_log / existing_task 查询均返回 None,放行到黑名单检查
            return FakeResult(None)

        def add(self, obj):
            self.added.append(obj)

        def commit(self):
            pass

        def flush(self):
            pass

    session = FakeSession()
    subscription = SimpleNamespace(
        id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        channel_id=BLOCKED_CHANNEL_ID,
        channel_title="Blocked Channel",
    )
    video = SimpleNamespace(
        video_id=VIDEO_ID,
        duration_seconds=None,
        title="Test Blocked Video",
    )

    # patch 模块属性:被测代码以 blocklist_service.get_blocklist_sync(session) 调用
    monkeypatch.setattr(blocklist_service, "get_blocklist_sync", lambda s: fake_bl)

    result = _process_single_video(session, "user-001", subscription, video, 7200, None, None)

    # 1) 返回 {"status":"skipped","video_id":VIDEO_ID,"reason":"channel_blocked"}
    assert result == {"status": "skipped", "video_id": VIDEO_ID, "reason": "channel_blocked"}

    # 2) 全程未建 Task
    task_objects = [obj for obj in session.added if isinstance(obj, Task)]
    assert task_objects == [], "不应构造 Task 对象"

    # 3) 写入了一条 YouTubeAutoTranscribeLog 且 skip_reason == "channel_blocked"
    logs = [obj for obj in session.added if isinstance(obj, YouTubeAutoTranscribeLog)]
    assert len(logs) == 1, f"应写入 1 条 log,实际 {len(logs)} 条"
    assert logs[0].skip_reason == "channel_blocked"
