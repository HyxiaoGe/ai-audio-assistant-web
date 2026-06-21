"""成本可见 PR-3:从 LiteLLM 读 end-user spend($)。

LLM 全量经 LiteLLM 代理,LiteLLM 是 LLM 成本权威账本。配合 PR-1 给请求体打了 user 标签后,
LiteLLM 按 end-user/customer 累计 spend,管理端用 master key 调 GET /customer/info?end_user_id=<id>
取回。本组钉住:① 有无 master key 决定 available;② 不可用时直接返回 {} 不发请求(优雅降级,
不阻塞 ¥ 两列);③ 可用时带 Bearer master key + end_user_id 取 spend;④ 单个用户失败/无记录
(非 200)跳过、不连累其他用户。
"""

from __future__ import annotations

import httpx
import pytest

from app.services.llm.spend_client import LiteLLMSpendClient


def _make_client(handler, master_key: str | None = "sk-master") -> LiteLLMSpendClient:
    transport = httpx.MockTransport(handler) if handler is not None else None
    return LiteLLMSpendClient(base_url="http://litellm.test", master_key=master_key, transport=transport)


def test_available_reflects_master_key() -> None:
    assert _make_client(None, master_key="sk-master").available is True
    assert _make_client(None, master_key=None).available is False
    assert _make_client(None, master_key="").available is False


async def test_no_master_key_returns_empty_without_request() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"spend": 1.0})

    client = _make_client(handler, master_key=None)
    out = await client.spend_by_end_user(["u-1"])
    assert out == {}
    assert called is False


async def test_spend_by_end_user_maps_and_authorizes() -> None:
    seen: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.params.get("end_user_id"), request.headers.get("authorization")))
        uid = request.url.params.get("end_user_id")
        spend = {"u-1": 0.0064, "u-2": 1.2345}[uid]
        return httpx.Response(200, json={"user_id": uid, "spend": spend})

    client = _make_client(handler)
    out = await client.spend_by_end_user(["u-1", "u-2"])
    assert out["u-1"] == pytest.approx(0.0064)
    assert out["u-2"] == pytest.approx(1.2345)
    assert ("u-1", "Bearer sk-master") in seen
    assert str(client._base_url).endswith("litellm.test")  # 末尾斜杠已剥


async def test_missing_user_skipped_not_fatal() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        uid = request.url.params.get("end_user_id")
        if uid == "ghost":
            return httpx.Response(400, json={"error": "customer not found"})
        return httpx.Response(200, json={"spend": 0.5})

    client = _make_client(handler)
    out = await client.spend_by_end_user(["ghost", "u-9"])
    assert "ghost" not in out
    assert out["u-9"] == pytest.approx(0.5)


async def test_malformed_spend_skipped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"user_id": "u-x"})  # 无 spend 键

    client = _make_client(handler)
    out = await client.spend_by_end_user(["u-x"])
    # 无 spend → 记 0.0(用户存在于 LiteLLM 但暂无花费),不应抛
    assert out["u-x"] == pytest.approx(0.0)
