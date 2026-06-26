from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport

from app.api import deps
from app.core.exceptions import BusinessError
from app.core.response import error
from app.i18n.codes import ErrorCode
from app.services.moderation import gate
from app.services.moderation.client import ModerationResult
from app.services.youtube import blocklist_service, search_cache
from app.services.youtube.search_service import VideoHit, YouTubeSearchService


def _empty_bl() -> blocklist_service.Blocklist:
    return blocklist_service.Blocklist(terms=frozenset(), channel_ids=frozenset(), channel_names=frozenset())


def _make_youtube_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    from app.api.v1 import youtube_search

    app = FastAPI()
    app.include_router(youtube_search.router, prefix="/api/v1")

    async def _no_db() -> Any:
        return object()

    async def _anon_viewer() -> Any:
        return None

    app.dependency_overrides[deps.get_db] = _no_db
    app.dependency_overrides[deps.get_public_viewer] = _anon_viewer
    app.dependency_overrides[youtube_search._search_rate_limit] = lambda: None

    @app.exception_handler(BusinessError)
    async def _handle(_req: Request, exc: BusinessError) -> Any:
        return error(int(exc.code), exc.code.name)

    async def _noop_heat(_db: Any, _n: str, _k: str) -> None:
        return None

    monkeypatch.setattr(search_cache, "register_query_heat", _noop_heat)

    async def _bl(_db: Any) -> blocklist_service.Blocklist:
        return _empty_bl()

    monkeypatch.setattr(blocklist_service, "get_blocklist", _bl)
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_youtube_search_enforce_block_returns_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_youtube_app(monkeypatch)
    monkeypatch.setattr(gate.config, "search_mode", lambda: "enforce")

    async def _cms_block(text: str, scene: str, request_id: str | None) -> ModerationResult:
        return ModerationResult(action="block")

    monkeypatch.setattr(gate, "_moderate", _cms_block)

    async with _client(app) as client:
        body = (await client.get("/api/v1/youtube/search?q=badword")).json()
    assert body["code"] == int(ErrorCode.YOUTUBE_SEARCH_QUERY_BLOCKED)


async def test_manual_denylist_short_circuits_before_cms(monkeypatch: pytest.MonkeyPatch) -> None:
    # 人工屏蔽词命中必须先于 CMS:gate 绝不被调到(调到就 AssertionError)
    app = _make_youtube_app(monkeypatch)
    monkeypatch.setattr(gate.config, "search_mode", lambda: "enforce")

    async def _bl(_db: Any) -> blocklist_service.Blocklist:
        return blocklist_service.Blocklist(terms=frozenset({"spam"}), channel_ids=frozenset(), channel_names=frozenset())

    monkeypatch.setattr(blocklist_service, "get_blocklist", _bl)

    async def _must_not_call(*_a: Any, **_k: Any) -> ModerationResult:
        raise AssertionError("CMS gate must not run when manual denylist already hit")

    monkeypatch.setattr(gate, "_moderate", _must_not_call)

    async with _client(app) as client:
        body = (await client.get("/api/v1/youtube/search?q=SPAM")).json()
    assert body["code"] == int(ErrorCode.YOUTUBE_SEARCH_QUERY_BLOCKED)


async def test_youtube_search_off_allows_and_calls_service(monkeypatch: pytest.MonkeyPatch) -> None:
    # off:不调 CMS,正常走真实搜索
    app = _make_youtube_app(monkeypatch)
    monkeypatch.setattr(gate.config, "search_mode", lambda: "off")

    async def _miss(_db: Any, _n: str) -> None:
        return None

    async def _search(_self: Any, query: str, limit: int) -> list[VideoHit]:
        return [
            VideoHit(
                video_id="v1",
                title="t",
                channel=None,
                channel_id=None,
                thumbnail="https://i.ytimg.com/vi/v1/hqdefault.jpg",
                url="https://www.youtube.com/watch?v=v1",
            )
        ]

    async def _upsert(_db: Any, n: str, d: str, hits: list[VideoHit]) -> None:
        return None

    monkeypatch.setattr(search_cache, "get_cached_results", _miss)
    monkeypatch.setattr(YouTubeSearchService, "search", _search)
    monkeypatch.setattr(search_cache, "upsert_results", _upsert)

    async with _client(app) as client:
        body = (await client.get("/api/v1/youtube/search?q=cats")).json()
    assert body["code"] == 0
    assert body["data"]["items"][0]["video_id"] == "v1"
