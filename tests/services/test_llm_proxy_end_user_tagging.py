"""成本可见 PR-1:给 LiteLLM 调用打 end-user 标签。

LiteLLM 按请求体 ``user`` 字段累计每个 end-user(customer)的 spend(官方
GET /customer/info?end_user_id=<id> 据此返回)。此前 proxy 的所有 payload 只含
model/messages/max_tokens/temperature,LiteLLM 看到整个 app 一个 key、无法把花费
拆到具体用户。本组钉住:

- ProxyLLMService(user_id=...) 构造后,所有 payload 注入 ``user``=user_id;
- 未提供 user_id 时绝不写 ``user`` 键(保持旧契约,不污染匿名/系统调用);
- 流式路径(chat_stream)同样注入;
- 可选 metadata(task_id/summary_type)随调用 kwargs 带出,供 LiteLLM 日志下钻。

user_id 随实例构造传入并存为实例状态是安全的:带 user_id 的实例在 SmartFactory
里 force_new=True、不进缓存、不跨用户复用(smart_factory.py:323),不会串号。
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.services.llm.proxy import ProxyLLMService


def _make_service(user_id: str | None = None) -> ProxyLLMService:
    return ProxyLLMService(
        config={
            "base_url": "http://litellm.test",
            "api_key": "test-key",
            "model": "m",
            "max_tokens": 16,
        },
        user_id=user_id,
    )


def _install_capture(monkeypatch: pytest.MonkeyPatch, bodies: list[dict], *, stream: bool = False) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if stream:
            return httpx.Response(
                200,
                text='data: {"choices":[{"delta":{"content":"x"}}]}\n\ndata: [DONE]\n\n',
            )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient.__init__
    monkeypatch.setattr(
        httpx.AsyncClient,
        "__init__",
        lambda self, *a, **k: original(self, *a, **{**k, "transport": transport}),
    )


async def test_generate_tags_end_user(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies: list[dict] = []
    _install_capture(monkeypatch, bodies)
    svc = _make_service(user_id="user-abc")
    await svc.generate("prompt", system_message="sys")
    assert bodies[0]["user"] == "user-abc"


async def test_generate_with_usage_tags_end_user(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies: list[dict] = []
    _install_capture(monkeypatch, bodies)
    svc = _make_service(user_id="user-xyz")
    await svc.generate_with_usage("prompt")
    assert bodies[0]["user"] == "user-xyz"


async def test_chat_tags_end_user(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies: list[dict] = []
    _install_capture(monkeypatch, bodies)
    svc = _make_service(user_id="user-chat")
    await svc.chat([{"role": "user", "content": "hi"}])
    assert bodies[0]["user"] == "user-chat"


async def test_chat_stream_tags_end_user(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies: list[dict] = []
    _install_capture(monkeypatch, bodies, stream=True)
    svc = _make_service(user_id="user-stream")
    async for _ in svc.chat_stream([{"role": "user", "content": "hi"}]):
        pass
    assert bodies[0]["user"] == "user-stream"


async def test_no_user_id_omits_user_key(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies: list[dict] = []
    _install_capture(monkeypatch, bodies)
    svc = _make_service(user_id=None)
    await svc.generate("prompt")
    assert "user" not in bodies[0]


async def test_metadata_carried_from_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies: list[dict] = []
    _install_capture(monkeypatch, bodies)
    svc = _make_service(user_id="user-md")
    await svc.generate_with_usage("prompt", task_id="task-1", summary_type="overview")
    md = bodies[0].get("metadata")
    assert md is not None
    assert md["task_id"] == "task-1"
    assert md["summary_type"] == "overview"
    assert md["app"] == "audio"
