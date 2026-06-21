"""成本可见 PR-1 plumbing:user_id 从 SmartFactory → ServiceRegistry → ProxyLLMService 贯通。

proxy 已能在 payload 注入 user(见 tests/services/test_llm_proxy_end_user_tagging.py),
本组钉住 user_id 真的能一路传到 proxy 构造器:

- SmartFactory.get_service(..., user_id=) 把 user_id 透传给 ServiceRegistry.get;
- ServiceRegistry.get 只在服务 __init__ 接受 user_id 时注入(不破坏 ASR/image_service);
- 未显式传 user_id 时,worker 设置的 user 上下文(get_current_user_id 兜底)同样能给 proxy
  打标 —— 这覆盖 summary_generator 等不显式传 user_id 的主摘要路径。
"""

from __future__ import annotations

import pytest

from app.core.registry import ServiceRegistry
from app.core.smart_factory import (
    SelectionStrategy,
    SmartFactory,
    SmartFactoryConfig,
)
from app.core.user_context import reset_current_user_id, set_current_user_id
from app.services.llm import proxy as proxy_module


def _configure_factory() -> None:
    SmartFactory.reset()
    SmartFactory.configure(
        SmartFactoryConfig(
            default_strategy=SelectionStrategy.HEALTH_FIRST,
            enable_monitoring=False,
            enable_fault_tolerance=False,
            cache_instances=True,
        )
    )


async def test_get_service_forwards_user_id_to_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_factory()
    captured: dict = {}

    def fake_get(service_type: str, name: str, **kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("app.core.smart_factory.ServiceRegistry.get", fake_get)
    await SmartFactory.get_service("llm", provider="proxy", model_id="chat", user_id="u-1")
    assert captured.get("user_id") == "u-1"


def test_registry_injects_user_id_into_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proxy_module.settings, "LITELLM_API_KEY", "sk-test")
    svc = ServiceRegistry.get("llm", "proxy", model_id="chat", user_id="u-2", force_new=True)
    assert svc._end_user_id == "u-2"


async def test_user_context_fallback_tags_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    # summary_generator 等主路径调 get_service 时不显式传 user_id;worker 入口设置的
    # user 上下文经 smart_factory 的 get_current_user_id 兜底,仍应给 proxy 打标。
    _configure_factory()
    monkeypatch.setattr(proxy_module.settings, "LITELLM_API_KEY", "sk-test")
    token = set_current_user_id("ctx-user")
    try:
        svc = await SmartFactory.get_service("llm", provider="proxy", model_id="chat")
        assert svc._end_user_id == "ctx-user"
    finally:
        reset_current_user_id(token)
