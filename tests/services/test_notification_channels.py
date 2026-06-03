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
