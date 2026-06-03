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


from app.services.user_preferences import resolve_enabled_channels  # noqa: E402


def test_resolve_default_in_app_only() -> None:
    p = NotificationPreferences()
    # 默认：in_app 开、feishu 关；模板允许 in_app+feishu -> 只命中 in_app
    assert resolve_enabled_channels(p, "task_failed", ("in_app", "feishu")) == ["in_app"]


def test_resolve_respects_allowed_channels_intersection() -> None:
    # 即便偏好把 feishu 总开关打开，模板只允许 in_app 时也不发飞书
    p = NotificationPreferences.model_validate({"channels": {"in_app": True, "feishu": True}})
    assert resolve_enabled_channels(p, "task_completed", ("in_app",)) == ["in_app"]


def test_resolve_channel_master_off_disables_channel() -> None:
    p = NotificationPreferences.model_validate({"channels": {"in_app": False, "feishu": False}})
    assert resolve_enabled_channels(p, "task_failed", ("in_app", "feishu")) == []


def test_resolve_type_explicit_false_overrides_channel_on() -> None:
    # feishu 总开关开，但 task_completed 显式关 feishu -> 不发飞书
    p = NotificationPreferences.model_validate(
        {
            "channels": {"in_app": True, "feishu": True},
            "types": {"task_completed": {"feishu": False}},
        }
    )
    assert resolve_enabled_channels(p, "task_completed", ("in_app", "feishu")) == ["in_app"]


def test_resolve_type_none_inherits_channel_on() -> None:
    # task_failed 未覆盖 feishu（None）-> 继承渠道总开关（开）
    p = NotificationPreferences.model_validate(
        {
            "channels": {"in_app": True, "feishu": True},
            "types": {"task_failed": {"in_app": True}},
        }
    )
    assert resolve_enabled_channels(p, "task_failed", ("in_app", "feishu")) == ["in_app", "feishu"]


def test_resolve_unknown_type_uses_channel_defaults() -> None:
    # prefs.types 没有该 type 时按渠道总开关解析
    p = NotificationPreferences.model_validate({"channels": {"in_app": True, "feishu": True}})
    assert resolve_enabled_channels(p, "visual_failed", ("in_app", "feishu")) == ["in_app", "feishu"]


def test_resolve_preserves_allowed_order() -> None:
    p = NotificationPreferences.model_validate({"channels": {"in_app": True, "feishu": True}})
    assert resolve_enabled_channels(p, "task_failed", ("feishu", "in_app")) == ["feishu", "in_app"]
