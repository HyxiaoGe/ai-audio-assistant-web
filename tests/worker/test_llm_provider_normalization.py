"""worker 侧 LLM provider 归一化。

前端从 /llm/models 选模型时，options 里带的是「展示分组 provider」（如 deepseek/openai/
litellm），并非注册服务名。文本 LLM 已统一经 proxy 路由（真正的选择键是 model_id），
解析时必须把这类展示名归一到已注册的默认文本 LLM 服务（proxy）并保留 model_id，
否则 SmartFactory.get_service("llm", provider="deepseek", ...) 会因
"Service llm:deepseek not found in registry" 崩在 worker，触发无谓的 Celery 重试。
"""

from __future__ import annotations

import pytest

from app.models.task import Task
from worker.tasks import process_audio, process_youtube


@pytest.mark.parametrize("module", [process_youtube, process_audio])
def test_resolve_maps_catalog_provider_to_proxy(module: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module.ServiceRegistry, "list_services", classmethod(lambda cls, t: ["proxy"]))
    monkeypatch.setattr(module, "_select_default_llm_provider", lambda: "proxy")
    task = Task(options={"provider": "deepseek", "model_id": "chat-default"})

    provider, model_id = module._resolve_llm_selection(task, "user-1")

    assert provider == "proxy"  # 展示名 deepseek 归一到注册服务 proxy
    assert model_id == "chat-default"  # 用户选的模型必须保留


@pytest.mark.parametrize("module", [process_youtube, process_audio])
def test_resolve_keeps_registered_provider(module: object, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module.ServiceRegistry, "list_services", classmethod(lambda cls, t: ["proxy"]))
    task = Task(options={"provider": "proxy", "model_id": "chat-premium"})

    provider, model_id = module._resolve_llm_selection(task, "user-1")

    assert provider == "proxy"
    assert model_id == "chat-premium"
