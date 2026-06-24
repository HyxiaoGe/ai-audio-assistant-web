"""溯源 PR5(摘要侧):摘要/要点/待办/章节把真实 input/output token 折进返回元数据。

承接 PR3 的 (content, metadata) 契约:_generate_single_summary / _generate_chapters 现额外
经 llm_service.generate_with_usage 取到真实用量,并把 input_tokens/output_tokens 折进 metadata,
供调用方写入 Summary.input_tokens/output_tokens 并修正 token_count(由字符数改为真实 output token)。
不起真实 DB(纯函数级,mock prompt manager 与带用量的 LLM)。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _load():
    # 按文件路径加载,绕开 worker/tasks/__init__.py(它在 import 期连库)。
    p = Path(__file__).resolve().parents[2] / "worker" / "tasks" / "summary_generator.py"
    spec = importlib.util.spec_from_file_location("summary_generator_usage_uut", p)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


sg = _load()


class _FakePM:
    def get_prompt(self, **kwargs):
        cat = kwargs.get("category")
        ptype = kwargs.get("prompt_type")
        return {
            "system": "sys",
            "user_prompt": "user",
            "model_params": {},
            "metadata": {"slug": f"{cat}-{ptype}-zh", "version": "1.9.0"},
        }


class _UsageLLM:
    provider = "proxy"
    model_name = "chat-default"

    async def generate_with_usage(self, prompt, system_message=None, temperature=0.7, max_tokens=1500):
        return "这是生成的摘要正文内容", {"input_tokens": 999, "output_tokens": 42, "total_tokens": 1041}


class _UsageLLMChapters(_UsageLLM):
    async def generate_with_usage(self, prompt, system_message=None, temperature=0.3, max_tokens=1500):
        return (
            '{"total_chapters": 2, "chapters": [{"title": "一"}, {"title": "二"}]}',
            {"input_tokens": 1500, "output_tokens": 88, "total_tokens": 1588},
        )


class _NoUsageLLM:
    """provider 不返回用量(usage=None)时,metadata 不应带 token 键。"""

    provider = "proxy"
    model_name = "chat-default"

    async def generate_with_usage(self, prompt, system_message=None, temperature=0.7, max_tokens=1500):
        return "正文", None


async def test_single_summary_folds_token_usage_into_metadata(monkeypatch) -> None:
    monkeypatch.setattr(sg, "get_prompt_manager", lambda: _FakePM())
    content, meta = await sg._generate_single_summary(
        text="转写文本",
        summary_type="overview",
        content_style="meeting",
        quality_notice="",
        llm_service=_UsageLLM(),
    )
    assert content  # 正文非空
    assert meta["slug"] == "summary-overview-zh"  # PR3 契约仍在
    assert meta["input_tokens"] == 999
    assert meta["output_tokens"] == 42


async def test_single_summary_omits_tokens_when_usage_absent(monkeypatch) -> None:
    monkeypatch.setattr(sg, "get_prompt_manager", lambda: _FakePM())
    _content, meta = await sg._generate_single_summary(
        text="转写文本",
        summary_type="overview",
        content_style="meeting",
        quality_notice="",
        llm_service=_NoUsageLLM(),
    )
    assert meta.get("input_tokens") is None
    assert meta.get("output_tokens") is None


async def test_chapters_folds_token_usage_into_metadata(monkeypatch) -> None:
    monkeypatch.setattr(sg, "get_prompt_manager", lambda: _FakePM())
    data, meta = await sg._generate_chapters(
        task_id="t1",
        text="转写文本",
        content_style="meeting",
        quality_notice="",
        llm_service=_UsageLLMChapters(),
    )
    assert data["total_chapters"] == 2
    assert meta["slug"] == "segmentation-segment-zh"
    assert meta["input_tokens"] == 1500
    assert meta["output_tokens"] == 88


class _FakeDbForSummaries:
    def add(self, item: object) -> None:
        return None

    def add_all(self, items: list[object]) -> None:
        return None


async def test_summaries_persist_quality_tier(monkeypatch) -> None:
    monkeypatch.setattr(sg, "get_prompt_manager", lambda: _FakePM())

    async def _fake_get_service(*args, **kwargs):
        return _UsageLLM()

    monkeypatch.setattr(sg.SmartFactory, "get_service", _fake_get_service)
    monkeypatch.setattr(
        sg.TranscriptProcessor,
        "assess_quality",
        lambda segments: SimpleNamespace(quality_score="high", avg_confidence=0.95),
    )
    # 短文本 → 跳过章节(只走 overview/key_points/action_items 一处 Summary 构造)
    monkeypatch.setattr(sg.TranscriptProcessor, "preprocess", lambda *a, **k: "短转写文本")
    monkeypatch.setattr(sg.TranscriptProcessor, "get_quality_notice", lambda q: "")

    segments = [SimpleNamespace(content="x", start_time=0.0, end_time=1.0, speaker_id=None, confidence=0.9, words=[])]
    summaries, meta = await sg.generate_summaries_with_quality_awareness(
        task_id="t1",
        segments=segments,
        content_style="meeting",
        session=_FakeDbForSummaries(),
        user_id="u1",
        provider="proxy",
        model_id="chat-default",
    )

    assert summaries  # 至少生成一条
    assert all(s.quality_tier == "high" for s in summaries)
    assert meta["quality_score"] == "high"
