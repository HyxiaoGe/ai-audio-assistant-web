"""app/i18n/notifications.py 通知文案目录 + render_notification 行为锁定。

后端渠道（飞书）与 InAppChannel 的 zh 兜底串都经此渲染：
- zh / en 双语按 i18n_key 渲染；
- task_failed 按 params["error_code"] 选友好文案；未映射的 code 回落通用友好语；
- 渲染绝不抛、绝不把原始内部错误透给用户（缺 param / 未知 key 都安全降级）。
"""

from __future__ import annotations

from app.i18n.notifications import NOTIFICATION_TEXT, render_notification


def test_render_task_completed_zh_interpolates_params() -> None:
    title, body = render_notification(
        "notif.task_completed", {"task_title": "周会录音"}, "zh"
    )
    assert "周会录音" in title or "周会录音" in body
    assert title  # 非空
    assert body


def test_render_task_completed_en_differs_from_zh() -> None:
    zh_title, _ = render_notification("notif.task_completed", {"task_title": "X"}, "zh")
    en_title, _ = render_notification("notif.task_completed", {"task_title": "X"}, "en")
    assert zh_title != en_title  # 两种语言确实不同


def test_render_task_failed_picks_friendly_body_by_error_code() -> None:
    # ASR_SERVICE_FAILED=51002 应有专门的友好文案
    _, body_asr = render_notification(
        "notif.task_failed", {"task_title": "T", "error_code": 51002}, "zh"
    )
    # AI_SUMMARY_GENERATION_FAILED=51102 应有不同的友好文案
    _, body_llm = render_notification(
        "notif.task_failed", {"task_title": "T", "error_code": 51102}, "zh"
    )
    assert body_asr != body_llm
    assert body_asr and body_llm


def test_render_task_failed_unmapped_error_code_falls_back_generic() -> None:
    _, body = render_notification(
        "notif.task_failed", {"task_title": "T", "error_code": 99999}, "zh"
    )
    assert body  # 有通用友好兜底
    assert "99999" not in body  # 不把原始 code 透给用户


def test_render_never_leaks_raw_error_text() -> None:
    # 即便 params 带了内部错误串，渲染也不得把它拼进用户可见文案
    _, body = render_notification(
        "notif.task_failed",
        {"task_title": "T", "error_code": 51002, "error": "Traceback secret KEY=abc"},
        "zh",
    )
    assert "Traceback" not in body
    assert "abc" not in body


def test_render_missing_param_does_not_raise() -> None:
    # 缺少模板需要的 param 时不得抛，安全返回模板本体（对齐 app/core/i18n.py 的容错风格）
    title, body = render_notification("notif.task_completed", {}, "zh")
    assert isinstance(title, str)
    assert isinstance(body, str)


def test_render_unknown_i18n_key_falls_back_safely() -> None:
    title, body = render_notification("notif.does_not_exist", {"x": 1}, "zh")
    assert isinstance(title, str)
    assert isinstance(body, str)
    assert title  # 通用兜底标题非空


def test_render_unknown_locale_falls_back_to_zh() -> None:
    title_fr, _ = render_notification("notif.task_completed", {"task_title": "Y"}, "fr")
    title_zh, _ = render_notification("notif.task_completed", {"task_title": "Y"}, "zh")
    assert title_fr == title_zh  # 未知 locale 回落到默认语言 zh


def test_catalog_covers_all_five_types() -> None:
    for key in (
        "notif.task_completed",
        "notif.task_failed",
        "notif.quota_alert",
        "notif.youtube_reauth_required",
        "notif.visual_failed",
    ):
        assert key in NOTIFICATION_TEXT
        assert "zh" in NOTIFICATION_TEXT[key]
        assert "en" in NOTIFICATION_TEXT[key]
