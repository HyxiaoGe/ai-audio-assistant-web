from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest


def _load():
    p = Path(__file__).resolve().parents[2] / "worker" / "tasks" / "regenerate_summary.py"
    spec = importlib.util.spec_from_file_location("regenerate_summary_uut", p)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rs = _load()


class _FakeSummary:
    def __init__(self, content):
        self.id = "sum-1"
        self.summary_type = "overview"
        self.content = content
        self.images = None


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

    def query(self, m):
        return _FakeQuery(self._row)

    def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_regenerate_writes_images_pending_and_keeps_content(monkeypatch) -> None:
    summary = _FakeSummary("正文 {{IMAGE: infographic | 主题 | 关键}}")
    session = _FakeSession(summary)
    import contextlib
    monkeypatch.setattr(rs, "get_sync_db_session", lambda: contextlib.nullcontext(session))
    monkeypatch.setattr(rs, "is_auto_images_enabled", lambda *a, **k: True)

    published: list[dict[str, Any]] = []
    monkeypatch.setattr(rs, "publish_image_ready_global", lambda **kw: published.append(kw))

    async def _fake_parallel(placeholders, user_id, task_id, *, content_style, locale,
                             max_images, timeout, on_image_ready=None):
        r = {"placeholder": placeholders[0]["placeholder"], "url": "/img.png",
             "status": "success", "model_id": "m"}
        if on_image_ready:
            on_image_ready(r, 1, 1)
        return [r]

    monkeypatch.setattr(rs, "generate_images_parallel", _fake_parallel)
    monkeypatch.setattr(rs, "apply_image_result_to_summary",
                        lambda s, sid, r: {"placeholder": r["placeholder"], "status": "ready",
                                           "url": r["url"], "model_id": r.get("model_id")})

    await rs._process_regenerated_images(
        task_id="t1", user_id="user-1", summary_id="sum-1",
        content="正文 {{IMAGE: infographic | 主题 | 关键}}", content_style="review",
    )

    # content 不被覆盖（保留占位符）
    assert "{{IMAGE: infographic | 主题 | 关键}}" in summary.content
    # 全局 WS image_ready 已发
    assert len(published) == 1
    assert published[0]["status"] == "ready"
    assert published[0]["summary_id"] == "sum-1"


@pytest.mark.asyncio
async def test_regenerate_inserts_default_placeholder_and_writes_pending(monkeypatch) -> None:
    summary = _FakeSummary("## 实测对比\n\n这段比较了多个 AI 产品。")
    session = _FakeSession(summary)
    import contextlib
    monkeypatch.setattr(rs, "get_sync_db_session", lambda: contextlib.nullcontext(session))
    monkeypatch.setattr(rs, "is_auto_images_enabled", lambda *a, **k: True)
    monkeypatch.setattr(rs, "publish_image_ready_global", lambda **kw: None)

    async def _fake_parallel(placeholders, *a, on_image_ready=None, **k):
        return []  # 不触发回调，仅验证 pending 初始化与 content 写回

    monkeypatch.setattr(rs, "generate_images_parallel", _fake_parallel)

    await rs._process_regenerated_images(
        task_id="t1", user_id="u1", summary_id="sum-1",
        content="## 实测对比\n\n这段比较了多个 AI 产品。", content_style="review",
    )
    # 默认占位符被插入 content（保留为锚点）
    assert "{{IMAGE:" in summary.content
    # images 初始化为 pending
    assert summary.images and summary.images[0]["status"] == "pending"
    assert session.committed is True


@pytest.mark.asyncio
async def test_regenerate_publishes_failed_on_image_failure(monkeypatch) -> None:
    summary = _FakeSummary("正文 {{IMAGE: a | x | y}}")
    session = _FakeSession(summary)
    import contextlib
    monkeypatch.setattr(rs, "get_sync_db_session", lambda: contextlib.nullcontext(session))
    monkeypatch.setattr(rs, "is_auto_images_enabled", lambda *a, **k: True)

    published: list[dict] = []
    monkeypatch.setattr(rs, "publish_image_ready_global", lambda **kw: published.append(kw))
    monkeypatch.setattr(rs, "apply_image_result_to_summary",
                        lambda s, sid, r: {"placeholder": r["placeholder"], "status": "failed",
                                           "url": None, "model_id": None})

    async def _fake_parallel(placeholders, *a, on_image_ready=None, **k):
        # url is intentionally non-None here so the production guard (url=None on failed)
        # is exercised — if the guard broke, published[0]["url"] would be "/img.png"
        r = {"placeholder": placeholders[0]["placeholder"], "url": "/img.png",
             "status": "failed", "error": "timeout", "model_id": None}
        if on_image_ready:
            on_image_ready(r, 1, 1)
        return [r]

    monkeypatch.setattr(rs, "generate_images_parallel", _fake_parallel)

    await rs._process_regenerated_images(
        task_id="t1", user_id="u1", summary_id="sum-1",
        content="正文 {{IMAGE: a | x | y}}", content_style="review",
    )
    assert len(published) == 1
    assert published[0]["status"] == "failed"
    assert published[0]["url"] is None
