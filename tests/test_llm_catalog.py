"""LLM 模型目录 API（薄代理到 LiteLLM）测试。

只关心“形状 + 关键行为”：
- 解析 LiteLLM `/model/info` 的响应
- 健康状态从 app.core.litellm_health 模块的缓存读
- 推荐 cost_tier=low 的别名
- LiteLLM 拉不到时返回空列表而不是 500
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from app.api.v1 import llm as llm_module
from app.core import litellm_health


def _model_info_payload() -> dict[str, Any]:
    return {
        "data": [
            {
                "model_name": "chat-default",
                "litellm_params": {"model": "gemini/gemini-2.5-flash"},
                "model_info": {
                    "metadata": {
                        "cost_tier": "low",
                        "description": "默认对话模型",
                        "display_name": "Gemini 2.5 Flash",
                        "provider_display": "Google",
                        "recommended_for": ["summary", "chat", "default"],
                    }
                },
            },
            {
                "model_name": "chat-premium",
                "litellm_params": {"model": "gemini/gemini-2.5-pro"},
                "model_info": {
                    "metadata": {
                        "cost_tier": "high",
                        "description": "高质量长内容",
                        "display_name": "Gemini 2.5 Pro",
                        "provider_display": "Google",
                        "recommended_for": ["review", "lecture"],
                    }
                },
            },
            {
                "model_name": "audio-structuring",
                "litellm_params": {"model": "gemini/gemini-3.1-flash-lite"},
                "model_info": {
                    "metadata": {
                        "cost_tier": "low",
                        "description": "结构化输出",
                        "display_name": "Gemini 3.1 Flash Lite",
                        "provider_display": "Google",
                        "recommended_for": ["chapters", "segmentation"],
                    }
                },
            },
        ]
    }


def _seed_health_cache(monkeypatch: pytest.MonkeyPatch, by_alias: dict[str, dict[str, Any]]) -> None:
    """模拟后台 litellm_health 已经探测过一次。

    by_alias 形如 {"chat-default": {"status": "healthy", "error": None}, ...}。
    没列出的 alias 在 endpoint 里会落到 unknown 分支，按可用处理。
    """
    monkeypatch.setattr(litellm_health, "_by_alias", dict(by_alias))
    monkeypatch.setattr(litellm_health, "_last_checked_at", time.time())


def _build_app(monkeypatch: pytest.MonkeyPatch, handler: Any) -> FastAPI:
    transport = httpx.MockTransport(handler)
    original_init = httpx.AsyncClient.__init__

    def patched_init(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> None:
        if "transport" not in kwargs:
            kwargs["transport"] = transport
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)
    monkeypatch.setattr(llm_module.settings, "LITELLM_BASE_URL", "http://litellm.test")
    monkeypatch.setattr(llm_module.settings, "LITELLM_API_KEY", "sk-test")

    # 默认假装后台还没探测过——endpoint 走乐观可用分支。具体测试需要覆盖时显式 _seed_health_cache。
    monkeypatch.setattr(litellm_health, "_by_alias", {})
    monkeypatch.setattr(litellm_health, "_last_checked_at", 0.0)

    app = FastAPI()
    app.include_router(llm_module.router, prefix="/api/v1")
    return app


@pytest.mark.asyncio
async def test_catalog_returns_aliases_with_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/model/info"):
            return httpx.Response(200, json=_model_info_payload())
        return httpx.Response(404)

    app = _build_app(monkeypatch, handler)
    _seed_health_cache(
        monkeypatch,
        {
            "chat-default": {"status": "healthy", "error": None},
            "chat-premium": {"status": "healthy", "error": None},
            "audio-structuring": {"status": "healthy", "error": None},
        },
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/llm/models")

    assert resp.status_code == 200
    body = resp.json()
    models = body["data"]["models"]
    aliases = {m["model_id"] for m in models}
    assert aliases == {"chat-default", "chat-premium", "audio-structuring"}

    chat_default = next(m for m in models if m["model_id"] == "chat-default")
    assert chat_default["display_name"] == "Gemini 2.5 Flash"
    assert chat_default["provider"] == "google"
    assert chat_default["is_available"] is True
    assert chat_default["cost_tier"] == "low"


@pytest.mark.asyncio
async def test_unhealthy_alias_marks_unavailable_with_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """litellm_health 缓存里标了 unhealthy 的别名 → endpoint 反映 is_available=False + 友好错误。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/model/info"):
            return httpx.Response(200, json=_model_info_payload())
        return httpx.Response(404)

    app = _build_app(monkeypatch, handler)
    _seed_health_cache(
        monkeypatch,
        {
            "chat-default": {"status": "healthy", "error": None},
            "chat-premium": {"status": "unhealthy", "error": "服务商认证失败：API key 无效或已过期"},
            "audio-structuring": {"status": "healthy", "error": None},
        },
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/llm/models")

    models = resp.json()["data"]["models"]
    by_alias = {m["model_id"]: m for m in models}
    assert by_alias["chat-default"]["is_available"] is True
    assert by_alias["chat-premium"]["is_available"] is False
    assert by_alias["chat-premium"]["status"] == "unhealthy"
    assert "认证失败" in by_alias["chat-premium"]["health_error"]


@pytest.mark.asyncio
async def test_recommended_is_first_healthy_low_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/model/info"):
            return httpx.Response(200, json=_model_info_payload())
        return httpx.Response(404)

    app = _build_app(monkeypatch, handler)
    _seed_health_cache(
        monkeypatch,
        {
            "chat-default": {"status": "healthy", "error": None},
            "chat-premium": {"status": "healthy", "error": None},
            "audio-structuring": {"status": "healthy", "error": None},
        },
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/llm/models")

    models = resp.json()["data"]["models"]
    recommended = [m for m in models if m["is_recommended"]]
    assert len(recommended) == 1
    # chat-default 排序在 audio-structuring 之前（alias 字符序），优先取它
    assert recommended[0]["model_id"] == "chat-default"


@pytest.mark.asyncio
async def test_litellm_unreachable_returns_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    app = _build_app(monkeypatch, handler)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/llm/models")

    assert resp.status_code == 200
    assert resp.json()["data"]["models"] == []


@pytest.mark.asyncio
async def test_health_cache_empty_optimistically_marks_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """litellm_health 后台还没探测过（冷启动）时，所有 alias 走乐观可用分支。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/model/info"):
            return httpx.Response(200, json=_model_info_payload())
        return httpx.Response(404)

    app = _build_app(monkeypatch, handler)
    # _build_app 默认就 reset 了缓存，不需要 _seed_health_cache
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/llm/models")

    models = resp.json()["data"]["models"]
    assert all(m["is_available"] for m in models)
