from __future__ import annotations

from typing import Any

import pytest

from app.config import settings
from app.models.task import Task
from worker.tasks import process_audio, process_youtube, regenerate_summary


def _task(options: dict[str, Any] | None = None) -> Task:
    return Task(
        user_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
        content_hash="hash",
        title="demo",
        source_type="youtube",
        source_url="https://www.youtube.com/watch?v=demo",
        options=options or {},
    )


@pytest.mark.parametrize(
    "resolver,args",
    [
        (process_youtube._resolve_llm_selection, (_task(), "user-1")),
        (process_audio._resolve_llm_selection, (_task(), "user-1")),
    ],
)
def test_default_proxy_llm_selection_uses_litellm_model(
    monkeypatch: pytest.MonkeyPatch,
    resolver: Any,
    args: tuple[Any, ...],
) -> None:
    monkeypatch.setattr(process_youtube.ServiceRegistry, "list_services", lambda service_type: ["proxy"])
    monkeypatch.setattr(process_audio.ServiceRegistry, "list_services", lambda service_type: ["proxy"])
    monkeypatch.setattr(
        process_youtube.ServiceRegistry,
        "get_metadata",
        lambda service_type, name: type("Metadata", (), {"priority": 1})(),
    )
    monkeypatch.setattr(
        process_audio.ServiceRegistry,
        "get_metadata",
        lambda service_type, name: type("Metadata", (), {"priority": 1})(),
    )
    monkeypatch.setattr(
        process_youtube.ConfigManager, "get_config", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError())
    )
    monkeypatch.setattr(
        process_audio.ConfigManager, "get_config", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError())
    )

    provider, model_id = resolver(*args)

    assert provider == "proxy"
    assert model_id == settings.LITELLM_MODEL


def test_regenerate_summary_default_proxy_llm_selection_uses_litellm_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(regenerate_summary.ServiceRegistry, "list_services", lambda service_type: ["proxy"])
    monkeypatch.setattr(
        regenerate_summary.ServiceRegistry,
        "get_metadata",
        lambda service_type, name: type("Metadata", (), {"priority": 1})(),
    )
    monkeypatch.setattr(
        regenerate_summary.ConfigManager,
        "get_config",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError()),
    )

    provider, model_id = regenerate_summary._resolve_llm_selection(None, None, "user-1")

    assert provider == "proxy"
    assert model_id == settings.LITELLM_MODEL


def test_polish_model_default_is_deepseek_chat() -> None:
    # polish 默认内部钉死 deepseek-chat（机械纠错无需重思考模型）。
    assert settings.POLISH_MODEL_ID == "deepseek-chat"
    assert settings.POLISH_PROVIDER == "proxy"


def test_polish_selection_ignores_user_summary_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """polish 固定走 settings.POLISH_MODEL_ID，即使用户为「摘要」选了别的（重思考）模型。"""
    monkeypatch.setattr(process_youtube.ServiceRegistry, "list_services", lambda service_type: ["proxy"])
    monkeypatch.setattr(
        process_youtube.ServiceRegistry,
        "get_metadata",
        lambda service_type, name: type("Metadata", (), {"priority": 1})(),
    )
    monkeypatch.setattr(
        process_youtube.ConfigManager, "get_config", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError())
    )
    monkeypatch.setattr(process_youtube.settings, "POLISH_PROVIDER", "proxy")
    monkeypatch.setattr(process_youtube.settings, "POLISH_MODEL_ID", "deepseek-chat")

    user_task = _task({"llm_provider": "doubao", "llm_model_id": "doubao-seed-2-0-pro-260215"})
    # 摘要解析跟随用户选择
    _, summary_model = process_youtube._resolve_llm_selection(user_task, "user-1")
    # polish 解析无视用户选择，固定内部模型
    polish_provider, polish_model = process_youtube._resolve_polish_selection("user-1")

    assert summary_model == "doubao-seed-2-0-pro-260215"
    assert polish_provider == "proxy"
    assert polish_model == "deepseek-chat"


def test_polish_selection_falls_back_when_provider_unregistered(monkeypatch: pytest.MonkeyPatch) -> None:
    """POLISH_PROVIDER 非注册服务时回落到默认注册服务，但仍保留内部钉死的 model_id。"""
    monkeypatch.setattr(process_youtube.ServiceRegistry, "list_services", lambda service_type: ["proxy"])
    monkeypatch.setattr(
        process_youtube.ServiceRegistry,
        "get_metadata",
        lambda service_type, name: type("Metadata", (), {"priority": 1})(),
    )
    monkeypatch.setattr(process_youtube.settings, "POLISH_PROVIDER", "not-a-real-service")
    monkeypatch.setattr(process_youtube.settings, "POLISH_MODEL_ID", "deepseek-chat")

    provider, model_id = process_youtube._resolve_polish_selection("user-1")

    assert provider == "proxy"
    assert model_id == "deepseek-chat"


@pytest.mark.parametrize("module", [process_youtube, process_audio])
def test_polish_selection_internal_model_both_pipelines(monkeypatch: pytest.MonkeyPatch, module: Any) -> None:
    """youtube 与 audio 两条链路的 polish 都钉死内部模型、与用户摘要选择解耦。"""
    monkeypatch.setattr(module.ServiceRegistry, "list_services", lambda service_type: ["proxy"])
    monkeypatch.setattr(
        module.ServiceRegistry,
        "get_metadata",
        lambda service_type, name: type("Metadata", (), {"priority": 1})(),
    )
    monkeypatch.setattr(module.settings, "POLISH_PROVIDER", "proxy")
    monkeypatch.setattr(module.settings, "POLISH_MODEL_ID", "deepseek-chat")

    provider, model_id = module._resolve_polish_selection("user-1")

    assert provider == "proxy"
    assert model_id == "deepseek-chat"
