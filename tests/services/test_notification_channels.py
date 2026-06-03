"""通知渠道注册表 + 渠道行为锁定（注册式、错误隔离、占位渠道）。

InAppChannel 的落库去重 + bus 推送测试也在本文件（Task 3.5 追加）。
"""

from __future__ import annotations

import pytest

from app.services.notifications.channels.base import (
    BaseNotificationChannel,
    get_channel,
    register_channel,
)


def test_register_and_get_channel_returns_singleton_instance() -> None:
    @register_channel("test_chan_singleton")
    class _Chan(BaseNotificationChannel):
        name = "test_chan_singleton"

        def deliver(self, session: object, event: object) -> None:
            return None

    inst1 = get_channel("test_chan_singleton")
    inst2 = get_channel("test_chan_singleton")
    assert isinstance(inst1, _Chan)
    assert inst1 is inst2  # 注册即实例化、缓存单例


def test_get_unknown_channel_raises_value_error() -> None:
    with pytest.raises(ValueError):
        get_channel("definitely_not_a_registered_channel")


def test_feishu_channel_is_registered_but_deliver_raises_not_implemented() -> None:
    # 导入即触发 @register_channel 注册
    import app.services.notifications.channels.feishu  # noqa: F401

    feishu = get_channel("feishu")
    assert feishu.name == "feishu"
    with pytest.raises(NotImplementedError):
        feishu.deliver(object(), object())


from datetime import UTC, datetime  # noqa: E402

from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.models.notification import Notification  # noqa: E402
from app.services.notifications import channels as channels_pkg  # noqa: E402,F401  确保 in_app 注册
from app.services.notifications.channels import in_app as in_app_mod  # noqa: E402
from app.services.notifications.events import NotificationEvent  # noqa: E402
from app.services.notifications.types import NotificationCategory, NotificationPriority, NotificationType  # noqa: E402


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    def publish(self, user_id: str, envelope: dict) -> None:
        self.published.append((user_id, envelope))


class _FakeSession:
    """落库成功路径的假 sync session。"""

    def __init__(self) -> None:
        self.added: list[Notification] = []
        self.committed = False
        self.rolled_back = False

    def add(self, item: object) -> None:
        if isinstance(item, Notification):
            # 模拟 DB 默认值，便于 NotificationResponse 序列化
            if item.created_at is None:
                item.created_at = datetime.now(UTC)
            if item.id is None:
                item.id = "00000000-0000-0000-0000-0000000000aa"
            self.added.append(item)

    def commit(self) -> None:
        self.committed = True

    def flush(self) -> None:
        return None

    def rollback(self) -> None:
        self.rolled_back = True


class _DupSession(_FakeSession):
    """commit 时抛 IntegrityError，模拟 dedup_key 部分唯一索引撞键。"""

    def commit(self) -> None:
        raise IntegrityError("duplicate dedup_key", params=None, orig=Exception("unique"))


def _event(dedup_key: str | None = None) -> NotificationEvent:
    return NotificationEvent(
        type=NotificationType.QUOTA_ALERT,
        user_id="user-1",
        params={"provider": "tencent", "threshold": 90},
        category=NotificationCategory.SYSTEM,
        priority=NotificationPriority.HIGH,
        action_url=None,
        task_id=None,
        dedup_key=dedup_key,
    )


def test_in_app_deliver_persists_row_and_publishes(monkeypatch) -> None:
    bus = _FakeBus()
    monkeypatch.setattr(in_app_mod, "get_event_bus", lambda: bus)
    session = _FakeSession()

    get_channel("in_app").deliver(session, _event(dedup_key="quota:tencent:file:90:2026-06-03"))

    assert len(session.added) == 1
    row = session.added[0]
    assert row.type == "quota_alert"
    assert row.dedup_key == "quota:tencent:file:90:2026-06-03"
    assert row.extra_data == {"provider": "tencent", "threshold": 90}
    # zh 兜底串被填入（过渡期/缺 key 安全网）
    assert row.title
    assert row.message
    # 推送一条 notification 信封
    assert len(bus.published) == 1
    user_id, envelope = bus.published[0]
    assert user_id == "user-1"
    assert envelope["kind"] == "notification"
    assert envelope["data"]["type"] == "quota_alert"


def test_in_app_deliver_swallows_integrity_error_no_publish(monkeypatch) -> None:
    bus = _FakeBus()
    monkeypatch.setattr(in_app_mod, "get_event_bus", lambda: bus)
    session = _DupSession()

    # 撞唯一索引 -> 吞为「已通知」，不抛、不推送
    get_channel("in_app").deliver(session, _event(dedup_key="quota:tencent:file:90:2026-06-03"))

    assert bus.published == []
    assert session.rolled_back is True
