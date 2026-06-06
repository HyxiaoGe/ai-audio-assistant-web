from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load():
    p = Path(__file__).resolve().parents[2] / "worker" / "tasks" / "image_generator.py"
    spec = importlib.util.spec_from_file_location("image_generator_specs_uut", p)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ig = _load()


def test_build_image_specs_from_explicit_placeholder() -> None:
    content = "正文一\n\n{{IMAGE: infographic | 小米战略 | 关键文字}}\n\n正文二"
    new_content, specs = ig.build_image_specs(content, "review")
    assert new_content == content  # 已有占位符，content 不变
    assert len(specs) == 1
    s = specs[0]
    assert s["placeholder"] == "{{IMAGE: infographic | 小米战略 | 关键文字}}"
    assert s["status"] == "pending"
    assert s["alt"] == "小米战略"
    assert s["url"] is None
    assert s["model_id"] is None
    assert s["error"] is None


def test_build_image_specs_plans_default_when_no_placeholder(monkeypatch) -> None:
    content = "## 实测对比\n\n这段比较了多个 AI 产品。"
    new_content, specs = ig.build_image_specs(content, "review")
    assert len(specs) == 1
    assert specs[0]["placeholder"] in new_content
    assert specs[0]["status"] == "pending"
    assert specs[0]["alt"]  # 非空 alt（取描述）


def test_build_image_specs_returns_empty_when_default_placeholder_is_none(monkeypatch) -> None:
    # _extract_article_image_topic("") 返回回退值 "内容核心概览" 而非空字符串，
    # 所以直接传空串不会触发 None 返回路径；用 monkeypatch 模拟 helper 返回 None。
    monkeypatch.setattr(ig, "_build_default_article_image_placeholder", lambda *_: None)
    new_content, specs = ig.build_image_specs("", "review")
    assert specs == []
    assert new_content == ""


def _content_with_n_placeholders(n: int) -> str:
    """构造含 n 个新格式 {{IMAGE: 类型 | 描述i | 关键i}} 占位符的正文。"""
    parts = ["正文开头"]
    for i in range(n):
        parts.append(f"{{{{IMAGE: infographic | 描述{i} | 关键{i}}}}}")
        parts.append(f"段落正文{i}")
    return "\n\n".join(parts)


def test_build_image_specs_caps_at_max_images_and_strips_surplus() -> None:
    # 8 个占位符、上限 6 → 只建 6 个 spec，多余 2 个锚点从 content 剥除，
    # 杜绝「建了 8 个 pending 槽但只生成 6 张」的孤儿「等待生成」（见 task 6fb1b394 / 3c641cf1）。
    content = _content_with_n_placeholders(8)
    new_content, specs = ig.build_image_specs(content, "review", max_images=6)
    assert len(specs) == 6
    assert specs[0]["placeholder"] == "{{IMAGE: infographic | 描述0 | 关键0}}"
    assert specs[5]["placeholder"] == "{{IMAGE: infographic | 描述5 | 关键5}}"
    # content 仅剩 6 个锚点，被丢弃的第 7、8 个连同其描述一并剥除
    assert new_content.count("{{IMAGE:") == 6
    assert "描述6" not in new_content
    assert "描述7" not in new_content
    # 保留的锚点仍在 content 中（前端可锚定）
    assert "{{IMAGE: infographic | 描述0 | 关键0}}" in new_content
    assert "{{IMAGE: infographic | 描述5 | 关键5}}" in new_content
    # 被保留的正文段落不应误删
    assert "段落正文6" in new_content


def test_build_image_specs_keeps_all_within_cap() -> None:
    content = _content_with_n_placeholders(3)
    new_content, specs = ig.build_image_specs(content, "review", max_images=6)
    assert len(specs) == 3
    assert new_content == content  # 未超限，content 原样不变


def test_build_image_specs_default_cap_from_config(monkeypatch) -> None:
    # 不显式传 max_images 时，按 auto_images 配置上限（此处 mock 为 6）截断。
    monkeypatch.setattr(ig, "get_auto_images_config", lambda: {"max_images": 6})
    content = _content_with_n_placeholders(7)
    new_content, specs = ig.build_image_specs(content, "review")
    assert len(specs) == 6
    assert new_content.count("{{IMAGE:") == 6


class _FakeSummary:
    def __init__(self, images):
        self.id = "sum-1"
        self.summary_type = "overview"
        self.images = images


class _FakeQuery:
    def __init__(self, row):
        self._row = row

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._row


class _FakeSession:
    def __init__(self, row):
        self._row = row
        self.committed = False

    def query(self, model):
        return _FakeQuery(self._row)

    def commit(self):
        self.committed = True


def test_apply_image_result_marks_ready_in_place(monkeypatch) -> None:
    monkeypatch.setattr(ig, "flag_modified", lambda obj, attr: None)
    images = [
        {
            "placeholder": "{{IMAGE: a | x | y}}",
            "status": "pending",
            "url": None,
            "alt": "x",
            "model_id": None,
            "error": None,
        }
    ]
    summary = _FakeSummary(images)
    session = _FakeSession(summary)
    result = {
        "placeholder": "{{IMAGE: a | x | y}}",
        "status": "success",
        "url": "/api/v1/summaries/images/u/t/h.png",
        "model_id": "gemini-3-pro-image-preview",
    }
    updated = ig.apply_image_result_to_summary(session, "sum-1", result)
    assert session.committed is True
    assert updated["status"] == "ready"
    assert updated["url"].endswith("h.png")
    assert updated["model_id"] == "gemini-3-pro-image-preview"
    assert summary.images[0]["status"] == "ready"


def test_apply_image_result_marks_failed_with_error(monkeypatch) -> None:
    monkeypatch.setattr(ig, "flag_modified", lambda obj, attr: None)
    images = [
        {
            "placeholder": "{{IMAGE: a | x | y}}",
            "status": "pending",
            "url": None,
            "alt": "x",
            "model_id": None,
            "error": None,
        }
    ]
    summary = _FakeSummary(images)
    session = _FakeSession(summary)
    result = {
        "placeholder": "{{IMAGE: a | x | y}}",
        "status": "failed",
        "url": None,
        "error": "timeout",
        "model_id": None,
    }
    updated = ig.apply_image_result_to_summary(session, "sum-1", result)
    assert updated["status"] == "failed"
    assert updated["error"] == "timeout"
    assert summary.images[0]["status"] == "failed"


def test_apply_image_result_returns_none_when_placeholder_absent(monkeypatch) -> None:
    monkeypatch.setattr(ig, "flag_modified", lambda obj, attr: None)
    summary = _FakeSummary([])
    session = _FakeSession(summary)
    result = {"placeholder": "{{IMAGE: missing}}", "status": "success", "url": "x"}
    assert ig.apply_image_result_to_summary(session, "sum-1", result) is None
    assert session.committed is False


def test_apply_image_result_returns_none_when_no_matching_placeholder(monkeypatch) -> None:
    # images is non-empty, but no entry matches the incoming placeholder (target-not-found branch)
    monkeypatch.setattr(ig, "flag_modified", lambda obj, attr: None)
    images = [
        {
            "placeholder": "{{IMAGE: a | x | y}}",
            "status": "pending",
            "url": None,
            "alt": "x",
            "model_id": None,
            "error": None,
        }
    ]
    summary = _FakeSummary(images)
    session = _FakeSession(summary)
    result = {"placeholder": "{{IMAGE: DIFFERENT}}", "status": "success", "url": "x"}
    assert ig.apply_image_result_to_summary(session, "sum-1", result) is None
    assert session.committed is False
    assert summary.images[0]["status"] == "pending"  # 原项未被改动


def test_apply_image_result_success_without_url_falls_to_failed(monkeypatch) -> None:
    monkeypatch.setattr(ig, "flag_modified", lambda obj, attr: None)
    images = [
        {
            "placeholder": "{{IMAGE: a | x | y}}",
            "status": "pending",
            "url": None,
            "alt": "x",
            "model_id": None,
            "error": None,
        }
    ]
    summary = _FakeSummary(images)
    session = _FakeSession(summary)
    # status 是 "success" 但没有 url -> 按归一化契约落到 failed（不写出 ready 破图）
    result = {"placeholder": "{{IMAGE: a | x | y}}", "status": "success", "url": None, "model_id": "m", "error": None}
    updated = ig.apply_image_result_to_summary(session, "sum-1", result)
    assert updated["status"] == "failed"
    assert updated["url"] is None
    assert summary.images[0]["status"] == "failed"


def test_publish_image_ready_global_envelope(monkeypatch) -> None:
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        ig,
        "publish_user_notification_sync",
        lambda user_id, message: captured.append((user_id, message)),
    )
    ig.publish_image_ready_global(
        user_id="user-1",
        task_id="task-1",
        summary_id="sum-1",
        placeholder="{{IMAGE: a | x | y}}",
        status="ready",
        url="/api/v1/summaries/images/u/t/h.png",
        model_id="gemini-3-pro-image-preview",
    )
    assert len(captured) == 1
    uid, raw = captured[0]
    assert uid == "user-1"
    env = json.loads(raw)
    assert env["kind"] == "image_ready"
    assert env["task_id"] == "task-1"
    assert env["summary_id"] == "sum-1"
    assert env["summary_type"] == "overview"
    assert env["placeholder"] == "{{IMAGE: a | x | y}}"
    assert env["status"] == "ready"
    assert env["url"].endswith("h.png")
    assert env["model_id"] == "gemini-3-pro-image-preview"
