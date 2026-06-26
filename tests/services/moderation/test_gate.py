from __future__ import annotations

import pytest

from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.services.moderation import gate
from app.services.moderation.client import ModerationResult


@pytest.fixture
def _stub_moderate(monkeypatch: pytest.MonkeyPatch):
    """把 gate._moderate 换成返回指定 action 的桩,隔离 HTTP。返回记录调用次数的容器。"""

    def _install(action: str) -> dict:
        calls = {"n": 0}

        async def _fake(text: str, scene: str, request_id: str | None) -> ModerationResult:
            calls["n"] += 1
            return ModerationResult(action=action, cms_trace_id="t")

        monkeypatch.setattr(gate, "_moderate", _fake)
        return calls

    return _install


# ---------------- off:不调 CMS,直接放行 ----------------
@pytest.mark.asyncio
async def test_off_never_calls_cms(monkeypatch: pytest.MonkeyPatch, _stub_moderate) -> None:
    monkeypatch.setattr(gate.config, "search_mode", lambda: "off")
    calls = _stub_moderate("block")  # 即便 CMS 会 block,off 也不该调
    await gate.search_query("anything", request_id=None)  # 不抛
    assert calls["n"] == 0


# ---------------- shadow:恒放行 ----------------
@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["pass", "review", "block", "degraded"])
async def test_shadow_always_allows(monkeypatch: pytest.MonkeyPatch, _stub_moderate, action: str) -> None:
    monkeypatch.setattr(gate.config, "search_mode", lambda: "shadow")
    calls = _stub_moderate(action)
    await gate.search_query("text", request_id=None)  # 任何结局都不抛
    assert calls["n"] == 1  # 但确实调了 CMS(影子观测)


# ---------------- enforce:按真值表 ----------------
@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["pass", "review"])
async def test_enforce_pass_and_review_allow(monkeypatch: pytest.MonkeyPatch, _stub_moderate, action: str) -> None:
    monkeypatch.setattr(gate.config, "search_mode", lambda: "enforce")
    _stub_moderate(action)
    await gate.search_query("text", request_id=None)  # 放行


@pytest.mark.asyncio
async def test_enforce_block_raises_search_code(monkeypatch: pytest.MonkeyPatch, _stub_moderate) -> None:
    monkeypatch.setattr(gate.config, "search_mode", lambda: "enforce")
    _stub_moderate("block")
    with pytest.raises(BusinessError) as ei:
        await gate.search_query("text", request_id=None)
    assert ei.value.code == ErrorCode.YOUTUBE_SEARCH_QUERY_BLOCKED


@pytest.mark.asyncio
async def test_enforce_degraded_fails_closed(monkeypatch: pytest.MonkeyPatch, _stub_moderate) -> None:
    monkeypatch.setattr(gate.config, "search_mode", lambda: "enforce")
    _stub_moderate("degraded")
    with pytest.raises(BusinessError) as ei:
        await gate.search_query("text", request_id=None)
    assert ei.value.code == ErrorCode.MODERATION_SERVICE_UNAVAILABLE


@pytest.mark.asyncio
async def test_publish_enforce_block_raises_publish_code(monkeypatch: pytest.MonkeyPatch, _stub_moderate) -> None:
    monkeypatch.setattr(gate.config, "publish_mode", lambda: "enforce")
    _stub_moderate("block")
    with pytest.raises(BusinessError) as ei:
        await gate.publish("title + summary", request_id=None)
    assert ei.value.code == ErrorCode.PUBLISH_CONTENT_BLOCKED


# ---------------- 空文本:无可审,直接放行不调 CMS ----------------
@pytest.mark.asyncio
async def test_blank_text_skips_cms(monkeypatch: pytest.MonkeyPatch, _stub_moderate) -> None:
    monkeypatch.setattr(gate.config, "publish_mode", lambda: "enforce")
    calls = _stub_moderate("block")
    await gate.publish("   ", request_id=None)  # 全空白 → 跳过
    assert calls["n"] == 0
