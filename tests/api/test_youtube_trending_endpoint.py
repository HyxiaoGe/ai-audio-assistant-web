from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from app.api import deps
from app.services.youtube import search_cache
from app.services.youtube.search_cache import TrendingItem


def _make_app() -> FastAPI:
    from app.api.v1 import youtube_search

    app = FastAPI()
    app.include_router(youtube_search.router, prefix="/api/v1")

    async def _no_db() -> Any:
        return object()

    app.dependency_overrides[deps.get_db] = _no_db
    app.dependency_overrides[youtube_search._trending_rate_limit] = lambda: None
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_trending_empty_when_below_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _none(_db: Any) -> list[TrendingItem]:
        return []

    monkeypatch.setattr(search_cache, "get_trending", _none)
    async with _client(_make_app()) as client:
        body = (await client.get("/api/v1/youtube/search/trending")).json()
    assert body["code"] == 0
    assert body["data"]["items"] == []


async def test_trending_maps_items(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _items(_db: Any) -> list[TrendingItem]:
        return [TrendingItem(query="news", count=9), TrendingItem(query="music", count=4)]

    monkeypatch.setattr(search_cache, "get_trending", _items)
    async with _client(_make_app()) as client:
        body = (await client.get("/api/v1/youtube/search/trending?limit=1")).json()
    assert body["code"] == 0
    assert body["data"]["items"] == [{"query": "news", "count": 9}]  # limit=1 收口
