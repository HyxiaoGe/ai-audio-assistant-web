from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport

from app.api import deps
from app.core.exceptions import BusinessError
from app.core.response import error
from app.i18n.codes import ErrorCode
from app.services.youtube import search_cache
from app.services.youtube.search_service import VideoHit, YouTubeSearchService


def _hit(vid: str) -> VideoHit:
    return VideoHit(
        video_id=vid,
        title=f"T {vid}",
        channel=None,
        channel_id=None,
        thumbnail=f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        url=f"https://www.youtube.com/watch?v={vid}",
    )


def _make_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    from app.api.v1 import youtube_search

    app = FastAPI()
    app.include_router(youtube_search.router, prefix="/api/v1")

    async def _no_db() -> Any:
        return object()

    async def _anon_viewer() -> Any:
        return None

    app.dependency_overrides[deps.get_db] = _no_db
    app.dependency_overrides[deps.get_public_viewer] = _anon_viewer
    # 绕开限流(限流自身已在 test_rate_limit_user_or_ip 覆盖)
    app.dependency_overrides[youtube_search._search_rate_limit] = lambda: None

    @app.exception_handler(BusinessError)
    async def _handle(_req: Request, exc: BusinessError) -> Any:
        return error(int(exc.code), exc.code.name)

    # 默认:热度登记无副作用
    async def _noop_heat(_db: Any, _n: str, _k: str) -> None:
        return None

    monkeypatch.setattr(search_cache, "register_query_heat", _noop_heat)
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_empty_query_returns_invalid_parameter(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch)
    async with _client(app) as client:
        body = (await client.get("/api/v1/youtube/search?q=%20%20")).json()
    assert body["code"] == int(ErrorCode.INVALID_PARAMETER)


async def test_denylisted_query_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch)
    monkeypatch.setattr(search_cache.settings, "YOUTUBE_SEARCH_DENYLIST", ["spam"])
    async with _client(app) as client:
        body = (await client.get("/api/v1/youtube/search?q=SPAM")).json()
    assert body["code"] == int(ErrorCode.YOUTUBE_SEARCH_QUERY_BLOCKED)


async def test_cache_hit_returns_cached_without_calling_service(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch)

    async def _cached(_db: Any, _n: str) -> list[VideoHit]:
        return [_hit("v1")]

    def _boom(*_a: Any, **_k: Any):
        raise AssertionError("service must not be called on cache hit")

    monkeypatch.setattr(search_cache, "get_cached_results", _cached)
    monkeypatch.setattr(YouTubeSearchService, "search", _boom)
    async with _client(app) as client:
        body = (await client.get("/api/v1/youtube/search?q=cats")).json()
    assert body["code"] == 0
    assert body["data"]["cached"] is True
    assert body["data"]["items"][0]["video_id"] == "v1"
    assert body["data"]["query"] == "cats"


async def test_cache_miss_calls_service_and_upserts(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch)
    upserts: list[tuple[str, str, int]] = []

    async def _miss(_db: Any, _n: str) -> None:
        return None

    async def _search(_self: Any, query: str, limit: int) -> list[VideoHit]:
        return [_hit("v2")]

    async def _upsert(_db: Any, normalized: str, display: str, hits: list[VideoHit]) -> None:
        upserts.append((normalized, display, len(hits)))

    monkeypatch.setattr(search_cache, "get_cached_results", _miss)
    monkeypatch.setattr(YouTubeSearchService, "search", _search)
    monkeypatch.setattr(search_cache, "upsert_results", _upsert)
    async with _client(app) as client:
        body = (await client.get("/api/v1/youtube/search?q=Dogs")).json()
    assert body["code"] == 0
    assert body["data"]["cached"] is False
    assert body["data"]["items"][0]["video_id"] == "v2"
    assert upserts == [("dogs", "Dogs", 1)]  # normalized + display + 命中数
