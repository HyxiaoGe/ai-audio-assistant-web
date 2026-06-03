"""NotificationService.notify 收口行为锁定。

- 按 type 取模板（category/priority/i18n_key/允许渠道）。
- action_url 默认 = /tasks/{task_id}（无显式 action_url 且有 task_id 时）。
- 命中渠道 = 用户偏好 ∩ 模板允许渠道，逐个 deliver。
- best-effort：任一渠道 deliver 抛错绝不向 producer 冒泡；整个 notify 永不抛。
"""

from __future__ import annotations

from app.services.notifications import service as service_mod
from app.services.notifications.service import NotificationService
from app.services.notifications.types import NotificationType


class _RecordingChannel:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[object] = []

    def deliver(self, session: object, event: object) -> None:
        self.calls.append(event)


class _RaisingChannel:
    name = "boom"

    def deliver(self, session: object, event: object) -> None:
        raise RuntimeError("channel exploded")


def _patch_channels(monkeypatch, mapping: dict) -> None:
    monkeypatch.setattr(service_mod, "get_channel", lambda name: mapping[name])


def _patch_prefs_all_in_app(monkeypatch) -> None:
    # 用户偏好：默认 in_app 开、feishu 关
    from app.schemas.user import NotificationPreferences

    monkeypatch.setattr(service_mod, "_load_preferences", lambda session, user_id: NotificationPreferences())


def test_notify_delivers_to_in_app_and_builds_event(monkeypatch) -> None:
    in_app = _RecordingChannel("in_app")
    _patch_channels(monkeypatch, {"in_app": in_app})
    _patch_prefs_all_in_app(monkeypatch)

    NotificationService.notify(
        object(),
        type=NotificationType.TASK_COMPLETED,
        user_id="user-1",
        params={"task_title": "周会"},
        task_id="task-42",
    )

    assert len(in_app.calls) == 1
    event = in_app.calls[0]
    assert event.type == NotificationType.TASK_COMPLETED
    assert event.user_id == "user-1"
    assert event.params == {"task_title": "周会"}
    # action_url 默认回填 /tasks/{task_id}
    assert event.action_url == "/tasks/task-42"
    # 模板元数据被带上
    assert str(event.category) == "task"
    assert str(event.priority) == "normal"


def test_notify_respects_explicit_action_url(monkeypatch) -> None:
    in_app = _RecordingChannel("in_app")
    _patch_channels(monkeypatch, {"in_app": in_app})
    _patch_prefs_all_in_app(monkeypatch)

    NotificationService.notify(
        object(),
        type=NotificationType.TASK_COMPLETED,
        user_id="user-1",
        params={},
        task_id="task-42",
        action_url="/custom/url",
    )
    assert in_app.calls[0].action_url == "/custom/url"


def test_notify_skips_disabled_feishu_channel(monkeypatch) -> None:
    in_app = _RecordingChannel("in_app")
    feishu = _RecordingChannel("feishu")
    _patch_channels(monkeypatch, {"in_app": in_app, "feishu": feishu})
    _patch_prefs_all_in_app(monkeypatch)  # feishu 默认关

    # task_failed 模板允许 in_app+feishu，但偏好 feishu 关 -> 只调 in_app
    NotificationService.notify(
        object(),
        type=NotificationType.TASK_FAILED,
        user_id="user-1",
        params={"task_title": "X", "error_code": 51002},
        task_id="t1",
    )
    assert len(in_app.calls) == 1
    assert feishu.calls == []


def test_notify_best_effort_channel_error_does_not_propagate(monkeypatch) -> None:
    # in_app deliver 抛错 -> notify 不得向 producer 冒泡
    monkeypatch.setattr(service_mod, "get_channel", lambda name: _RaisingChannel())
    _patch_prefs_all_in_app(monkeypatch)

    # 不抛即通过
    NotificationService.notify(
        object(),
        type=NotificationType.TASK_COMPLETED,
        user_id="user-1",
        params={"task_title": "X"},
        task_id="t1",
    )


def test_notify_best_effort_one_channel_failure_isolates_others(monkeypatch) -> None:
    good = _RecordingChannel("in_app")
    bad = _RaisingChannel()
    _patch_channels(monkeypatch, {"in_app": good, "feishu": bad})

    # 偏好打开 feishu，使两渠道都命中 task_failed（允许 in_app+feishu）
    from app.schemas.user import NotificationPreferences

    prefs = NotificationPreferences.model_validate({"channels": {"in_app": True, "feishu": True}})
    monkeypatch.setattr(service_mod, "_load_preferences", lambda session, user_id: prefs)

    NotificationService.notify(
        object(),
        type=NotificationType.TASK_FAILED,
        user_id="user-1",
        params={"task_title": "X", "error_code": 51002},
        task_id="t1",
    )
    # feishu(bad) 抛错被隔离，in_app(good) 仍收到投递
    assert len(good.calls) == 1


def test_notify_swallows_template_lookup_failure(monkeypatch) -> None:
    # 即便偏好加载层抛错，notify 也吞掉不冒泡（best-effort 整体兜底）
    def _boom(session: object, user_id: str) -> object:
        raise RuntimeError("prefs load failed")

    monkeypatch.setattr(service_mod, "_load_preferences", _boom)

    NotificationService.notify(
        object(),
        type=NotificationType.TASK_COMPLETED,
        user_id="user-1",
        params={},
        task_id="t1",
    )  # 不抛即通过
