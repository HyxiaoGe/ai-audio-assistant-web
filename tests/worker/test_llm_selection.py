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
