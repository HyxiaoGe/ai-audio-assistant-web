"""成本可见 PR-1:主摘要路径(summary_generator)把 user_id 透传给 SmartFactory。

`generate_summaries_with_quality_awareness` 已收 user_id 形参,但此前它内部三处
`SmartFactory.get_service("llm", ...)`(正常质量 / 低质量 premium / premium 失败回退)
都没带 user_id —— 于是 ProxyLLMService 构造时拿不到 end-user,LiteLLM 无法把这条
audio 主摘要的花费拆到具体用户。youtube / regenerate / polish 路径早已显式带 user_id,
本组钉住摘要路径与之对齐(显式线程化,不依赖 contextvar 兜底)。

按文件路径加载 summary_generator,绕开 worker/tasks/__init__.py(它会 import 全部任务模块,
其一在 import 期就连库)。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from app.services.asr.base import TranscriptSegment


def _load():
    p = Path(__file__).resolve().parents[2] / "worker" / "tasks" / "summary_generator.py"
    spec = importlib.util.spec_from_file_location("summary_generator_user_tagging_uut", p)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


sg = _load()


class _FakePM:
    def get_prompt(self, **kwargs):
        return {
            "system": "sys",
            "user_prompt": "user",
            "model_params": {},
            "metadata": {"slug": "s", "version": "1.0.0"},
        }


class _FakeLLM:
    provider = "proxy"
    model_name = "chat-default"

    async def generate_with_usage(self, prompt, system_message=None, temperature=0.7, max_tokens=1500):
        return "这是生成的摘要正文内容", None


def _segments() -> list[TranscriptSegment]:
    # 短内容(<2000 字符)→ 跳过章节划分,聚焦摘要路径的 get_service 线程化。
    return [
        TranscriptSegment(
            speaker_id="speaker_0",
            start_time=0.0,
            end_time=3.0,
            content="今天我们讨论一下项目的进展和后续的安排。",
            confidence=0.95,
        ),
        TranscriptSegment(
            speaker_id="speaker_0",
            start_time=3.0,
            end_time=6.0,
            content="第一项任务已经完成,第二项正在推进中。",
            confidence=0.96,
        ),
    ]


def _patch_get_service(monkeypatch) -> list[dict]:
    """拦截 SmartFactory.get_service,记录每次调用的 kwargs,返回 _FakeLLM。"""
    captured: list[dict] = []

    async def fake_get_service(service_type, **kwargs):  # noqa: ANN003
        captured.append({"service_type": service_type, **kwargs})
        return _FakeLLM()

    monkeypatch.setattr(sg.SmartFactory, "get_service", fake_get_service)
    monkeypatch.setattr(sg, "get_prompt_manager", lambda: _FakePM())
    return captured


async def test_normal_quality_threads_user_id(monkeypatch) -> None:
    captured = _patch_get_service(monkeypatch)
    await sg.generate_summaries_with_quality_awareness(
        task_id="t1",
        segments=_segments(),
        content_style="meeting",
        session=object(),
        user_id="user-99",
        provider="proxy",
        model_id="chat",
    )
    llm_calls = [c for c in captured if c["service_type"] == "llm"]
    assert llm_calls, "应至少有一次 llm get_service 调用"
    assert all(c.get("user_id") == "user-99" for c in llm_calls)


async def test_low_quality_premium_threads_user_id(monkeypatch) -> None:
    captured = _patch_get_service(monkeypatch)

    class _LowQuality:
        quality_score = "low"
        avg_confidence = 0.2

    monkeypatch.setattr(sg.TranscriptProcessor, "assess_quality", staticmethod(lambda segs: _LowQuality()))
    await sg.generate_summaries_with_quality_awareness(
        task_id="t1",
        segments=_segments(),
        content_style="meeting",
        session=object(),
        user_id="user-low",
        provider="proxy",
        model_id="chat",
    )
    llm_calls = [c for c in captured if c["service_type"] == "llm"]
    assert llm_calls
    # premium 分支(provider="proxy", model_id="chat-premium")同样必须带 user_id。
    assert all(c.get("user_id") == "user-low" for c in llm_calls)
    assert any(c.get("model_id") == "chat-premium" for c in llm_calls)
