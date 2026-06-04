from __future__ import annotations

from app.schemas.task import TaskCreateRequest, TaskDetailResponse, TaskOptions


def test_task_options_default_summary_style_is_auto() -> None:
    assert TaskOptions().summary_style == "auto"


def test_task_create_request_default_options_summary_style_is_auto() -> None:
    req = TaskCreateRequest(source_type="upload")
    assert req.options.summary_style == "auto"


def test_explicit_summary_style_preserved() -> None:
    assert TaskOptions(summary_style="review").summary_style == "review"


def test_detected_summary_style_present_only_when_auto_detected() -> None:
    # auto 经识别得到 -> 暴露具体风格
    assert (
        TaskDetailResponse.detected_summary_style_from_options(
            {"summary_style": "lecture", "summary_style_auto_detected": True}
        )
        == "lecture"
    )


def test_detected_summary_style_none_for_explicit_choice() -> None:
    # 用户显式选风格 -> 不暴露(None)
    assert (
        TaskDetailResponse.detected_summary_style_from_options(
            {"summary_style": "meeting", "summary_style_auto_detected": False}
        )
        is None
    )


def test_detected_summary_style_none_when_marker_absent() -> None:
    # 老任务无标记 -> None
    assert TaskDetailResponse.detected_summary_style_from_options({"summary_style": "meeting"}) is None
    assert TaskDetailResponse.detected_summary_style_from_options(None) is None
