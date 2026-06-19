"""溯源 PR3:摘要/要点/待办/章节捕获 PromptHub slug + 真实版本。

主生成路径(summary_generator)经 get_prompt 拿到 prompt_config["metadata"]
(含 slug=实际命中的 PromptHub slug、version=真实配置版本),此前被丢弃、Summary 只存
硬编码的 prompt_version="v1.2.0"。本组测试钉住「生成函数把 metadata 一并返回」这一契约,
使调用方能落库 prompt_slug + 真版本。不起真实 DB(纯函数级,mock prompt manager 与 LLM)。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load():
    # 按文件路径加载,绕开 worker/tasks/__init__.py(它会导入全部任务模块,其一在 import 期连库)。
    p = Path(__file__).resolve().parents[2] / "worker" / "tasks" / "summary_generator.py"
    spec = importlib.util.spec_from_file_location("summary_generator_provenance_uut", p)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


sg = _load()


class _FakePM:
    """按 (category, prompt_type) 返回可区分的 slug,模拟 PromptHub 命中结果。"""

    def get_prompt(self, **kwargs):
        cat = kwargs.get("category")
        ptype = kwargs.get("prompt_type")
        return {
            "system": "sys",
            "user_prompt": "user",
            "model_params": {},
            "metadata": {"slug": f"{cat}-{ptype}-zh", "version": "1.9.0", "source": "prompthub-sdk"},
        }


class _FakeLLM:
    provider = "proxy"
    model_name = "chat-default"

    async def generate(self, prompt, system_message=None, temperature=0.7, max_tokens=1500):
        return "这是生成的摘要正文内容"


class _FakeLLMChapters(_FakeLLM):
    async def generate(self, prompt, system_message=None, temperature=0.3, max_tokens=1500):
        return '{"total_chapters": 2, "chapters": [{"title": "一"}, {"title": "二"}]}'


async def test_generate_single_summary_returns_prompt_metadata(monkeypatch) -> None:
    monkeypatch.setattr(sg, "get_prompt_manager", lambda: _FakePM())
    content, meta = await sg._generate_single_summary(
        text="转写文本",
        summary_type="overview",
        content_style="meeting",
        quality_notice="",
        llm_service=_FakeLLM(),
    )
    assert content  # 正文非空(且经过剥围栏/开场白)
    assert meta["slug"] == "summary-overview-zh"
    assert meta["version"] == "1.9.0"


async def test_generate_single_summary_metadata_varies_by_type(monkeypatch) -> None:
    monkeypatch.setattr(sg, "get_prompt_manager", lambda: _FakePM())
    _content, meta = await sg._generate_single_summary(
        text="转写文本",
        summary_type="action_items",
        content_style="meeting",
        quality_notice="",
        llm_service=_FakeLLM(),
    )
    assert meta["slug"] == "summary-action_items-zh"


async def test_generate_chapters_returns_prompt_metadata(monkeypatch) -> None:
    monkeypatch.setattr(sg, "get_prompt_manager", lambda: _FakePM())
    data, meta = await sg._generate_chapters(
        task_id="t1",
        text="转写文本",
        content_style="meeting",
        quality_notice="",
        llm_service=_FakeLLMChapters(),
    )
    assert data["total_chapters"] == 2
    assert meta["slug"] == "segmentation-segment-zh"
    assert meta["version"] == "1.9.0"
