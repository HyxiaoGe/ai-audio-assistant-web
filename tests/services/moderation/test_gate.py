from __future__ import annotations

import asyncio
import logging

import pytest

from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.services.moderation import gate
from app.services.moderation.client import ModerationResult
from app.services.youtube.search_service import VideoHit


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


@pytest.fixture
def _route_moderate(monkeypatch: pytest.MonkeyPatch):
    """把 gate._moderate 换成「按文本路由 action」的桩,同时记录调用数与并发峰值。

    router(text) -> action。fake 内 await asyncio.sleep(0) 让出事件循环,使 Semaphore
    的并发上限可观测(峰值严格 ≤ 上限)。
    """

    def _install(router):
        calls = {"n": 0, "in_flight": 0, "peak": 0, "scenes": set()}

        async def _fake(text: str, scene: str, request_id: str | None) -> ModerationResult:
            calls["n"] += 1
            calls["scenes"].add(scene)
            calls["in_flight"] += 1
            calls["peak"] = max(calls["peak"], calls["in_flight"])
            await asyncio.sleep(0)
            calls["in_flight"] -= 1
            return ModerationResult(action=router(text), cms_trace_id="t", cloud_label="tms")

        monkeypatch.setattr(gate, "_moderate", _fake)
        return calls

    return _install


def _vh(vid: str, title: str, channel: str | None = None, channel_id: str | None = None) -> VideoHit:
    return VideoHit(
        video_id=vid,
        title=title,
        channel=channel,
        channel_id=channel_id,
        handle=None,
        thumbnail=None,
        url=f"https://y/{vid}",
    )


@pytest.mark.asyncio
async def test_display_off_returns_all_no_cms(monkeypatch, _route_moderate) -> None:
    monkeypatch.setattr(gate.config, "display_mode", lambda: "off")
    calls = _route_moderate(lambda t: "block")  # 即便会 block,off 也不该调
    hits = [_vh("a", "x"), _vh("b", "y")]
    out = await gate.filter_display(hits, request_id=None)
    assert [h.video_id for h in out] == ["a", "b"]
    assert calls["n"] == 0


@pytest.mark.asyncio
async def test_display_empty_hits_short_circuits(monkeypatch, _route_moderate) -> None:
    monkeypatch.setattr(gate.config, "display_mode", lambda: "enforce")
    calls = _route_moderate(lambda t: "pass")
    out = await gate.filter_display([], request_id=None)
    assert out == []
    assert calls["n"] == 0


@pytest.mark.asyncio
async def test_display_enforce_all_pass_kept(monkeypatch, _route_moderate) -> None:
    monkeypatch.setattr(gate.config, "display_mode", lambda: "enforce")
    calls = _route_moderate(lambda t: "pass")
    hits = [_vh("a", "x"), _vh("b", "y")]
    out = await gate.filter_display(hits, request_id=None)
    assert [h.video_id for h in out] == ["a", "b"]
    assert calls["scenes"] == {"ugc_display"}  # 场景正确


@pytest.mark.asyncio
async def test_display_enforce_block_dropped_and_logged(monkeypatch, _route_moderate, caplog) -> None:
    monkeypatch.setattr(gate.config, "display_mode", lambda: "enforce")
    _route_moderate(lambda t: "block" if "bad" in t else "pass")
    hits = [_vh("a", "good"), _vh("b", "bad title", channel="EvilCh", channel_id="UCbad")]
    with caplog.at_level(logging.WARNING, logger="app.services.moderation.gate"):
        out = await gate.filter_display(hits, request_id="rid-1")
    assert [h.video_id for h in out] == ["a"]  # block 项剔除
    msgs = [r.getMessage() for r in caplog.records]
    assert any("moderation_display_block" in m and "UCbad" in m and "mode=enforce" in m for m in msgs)


@pytest.mark.asyncio
async def test_display_enforce_review_kept(monkeypatch, _route_moderate) -> None:
    monkeypatch.setattr(gate.config, "display_mode", lambda: "enforce")
    _route_moderate(lambda t: "review")
    out = await gate.filter_display([_vh("a", "x")], request_id=None)
    assert [h.video_id for h in out] == ["a"]  # review 恒放行


@pytest.mark.asyncio
async def test_display_enforce_degraded_fails_closed(monkeypatch, _route_moderate) -> None:
    monkeypatch.setattr(gate.config, "display_mode", lambda: "enforce")
    _route_moderate(lambda t: "degraded" if "deg" in t else "pass")
    hits = [_vh("a", "ok"), _vh("b", "deg one")]
    with pytest.raises(BusinessError) as ei:
        await gate.filter_display(hits, request_id=None)
    assert ei.value.code == ErrorCode.MODERATION_SERVICE_UNAVAILABLE


@pytest.mark.asyncio
async def test_display_shadow_block_kept_and_logged(monkeypatch, _route_moderate, caplog) -> None:
    monkeypatch.setattr(gate.config, "display_mode", lambda: "shadow")
    _route_moderate(lambda t: "block" if "bad" in t else "pass")
    hits = [_vh("a", "good"), _vh("b", "bad", channel_id="UCx")]
    with caplog.at_level(logging.WARNING, logger="app.services.moderation.gate"):
        out = await gate.filter_display(hits, request_id=None)
    assert [h.video_id for h in out] == ["a", "b"]  # shadow 不剔
    assert any("moderation_display_block" in r.getMessage() and "mode=shadow" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_display_shadow_degraded_kept(monkeypatch, _route_moderate) -> None:
    monkeypatch.setattr(gate.config, "display_mode", lambda: "shadow")
    _route_moderate(lambda t: "degraded")
    hits = [_vh("a", "x"), _vh("b", "y")]
    out = await gate.filter_display(hits, request_id=None)
    assert [h.video_id for h in out] == ["a", "b"]  # shadow degraded 不拦


@pytest.mark.asyncio
async def test_display_blank_text_skips_cms(monkeypatch, _route_moderate) -> None:
    monkeypatch.setattr(gate.config, "display_mode", lambda: "enforce")
    calls = _route_moderate(lambda t: "block")  # 若误调会被剔,断言保留即证未调
    hits = [_vh("a", "", channel=None)]  # title 空 + channel None → 无可审文本
    out = await gate.filter_display(hits, request_id=None)
    assert [h.video_id for h in out] == ["a"]
    assert calls["n"] == 0


@pytest.mark.asyncio
async def test_display_concurrency_bounded(monkeypatch, _route_moderate) -> None:
    monkeypatch.setattr(gate.config, "display_mode", lambda: "enforce")
    monkeypatch.setattr(gate.settings, "MODERATION_DISPLAY_CONCURRENCY", 2)
    calls = _route_moderate(lambda t: "pass")
    hits = [_vh(str(i), f"t{i}") for i in range(5)]
    out = await gate.filter_display(hits, request_id=None)
    assert len(out) == 5
    assert calls["peak"] == 2  # Semaphore=2 严格限并发,5 项分批
