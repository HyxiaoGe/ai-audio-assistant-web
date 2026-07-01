from __future__ import annotations

from pydantic import BaseModel

from app.services.youtube.search_service import VideoHit


class SearchData(BaseModel):
    query: str
    items: list[VideoHit]
    cached: bool


class TrendingItemOut(BaseModel):
    query: str
    count: int


class TrendingData(BaseModel):
    items: list[TrendingItemOut]


class RecommendationData(BaseModel):
    items: list[VideoHit]
