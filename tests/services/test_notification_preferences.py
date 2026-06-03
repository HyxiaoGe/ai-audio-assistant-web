"""通知偏好矩阵 schema 形状 + resolve_enabled_channels 真值表锁定。

矩阵语义（§5.2）：(type, channel) 启用 ⟺ 渠道总开关开 AND 该 type 未显式关该渠道。
安全默认：in_app 全开、feishu 全关。
"""

from __future__ import annotations

from app.schemas.user import (
    NotificationChannelToggles,
    NotificationPreferences,
    NotificationTypeToggles,
)


def test_channel_toggles_safe_defaults() -> None:
    t = NotificationChannelToggles()
    assert t.in_app is True
    assert t.feishu is False


def test_type_toggles_default_none_means_inherit() -> None:
    t = NotificationTypeToggles()
    assert t.in_app is None  # None = 不覆盖，继承渠道总开关
    assert t.feishu is None


def test_preferences_defaults_empty_types_and_default_channels() -> None:
    p = NotificationPreferences()
    assert p.channels.in_app is True
    assert p.channels.feishu is False
    assert p.types == {}


def test_preferences_parses_matrix_payload() -> None:
    p = NotificationPreferences.model_validate(
        {
            "channels": {"in_app": True, "feishu": True},
            "types": {
                "task_failed": {"in_app": True, "feishu": True},
                "task_completed": {"feishu": False},
            },
        }
    )
    assert p.channels.feishu is True
    assert p.types["task_failed"].feishu is True
    assert p.types["task_completed"].feishu is False
    assert p.types["task_completed"].in_app is None  # 未给则继承
