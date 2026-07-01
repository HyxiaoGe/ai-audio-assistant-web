from __future__ import annotations

import pytest

import worker.tasks.harvest_recommendations as hr
from app.services.youtube.search_service import VideoHit


def _hit(vid: str, views: int | None = None) -> VideoHit:
    return VideoHit(video_id=vid, title=f"T {vid}", url=f"https://youtu.be/{vid}", view_count=views)


def test_merge_dedup_keeps_max_view_count() -> None:
    out = hr._merge_dedup([[_hit("a", 100), _hit("b", 50)], [_hit("a", 300)]])
    by_id = {h.video_id: h.view_count for h in out}
    assert by_id == {"a": 300, "b": 50}


def test_top_n_by_views_sorts_desc_and_truncates() -> None:
    out = hr._top_n_by_views([_hit("a", 10), _hit("b", 100), _hit("c", 50)], 2)
    assert [h.video_id for h in out] == ["b", "c"]


def test_top_n_treats_missing_view_count_as_zero() -> None:
    out = hr._top_n_by_views([_hit("a", None), _hit("b", 5)], 2)
    assert [h.video_id for h in out] == ["b", "a"]


async def test_harvest_orchestration_stores_ranked(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _seed(_db: object) -> list[str]:
        return ["t1", "t2"]

    async def _search(self: object, term: str, limit: int) -> list[VideoHit]:
        return [_hit(f"{term}-hi", 100), _hit(f"{term}-lo", 1)]

    async def _get_bl(_db: object) -> str:
        return "BL"

    async def _moderate(_db: object, hits: list[VideoHit], bl: object, request_id: str | None = None):
        return hits, False

    stored: dict[str, list[VideoHit]] = {}

    async def _replace(_db: object, hits: list[VideoHit]) -> None:
        stored["hits"] = hits

    monkeypatch.setattr(hr, "_seed_terms", _seed)
    monkeypatch.setattr(hr.YouTubeSearchService, "search", _search)
    monkeypatch.setattr(hr.blocklist_service, "get_blocklist", _get_bl)
    monkeypatch.setattr(hr, "moderate_hits", _moderate)
    monkeypatch.setattr(hr.recommendation_service, "replace_recommendations", _replace)

    result = await hr._harvest(object())
    assert result["stored"] == 4  # 2 词 × 2 条,去重后 4 条
    assert stored["hits"][0].view_count == 100  # 按 view_count 降序


async def test_harvest_single_term_failure_does_not_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _seed(_db: object) -> list[str]:
        return ["good", "bad"]

    async def _search(self: object, term: str, limit: int) -> list[VideoHit]:
        if term == "bad":
            raise RuntimeError("ytsearch down")
        return [_hit("g", 100)]

    async def _get_bl(_db: object) -> str:
        return "BL"

    async def _moderate(_db: object, hits: list[VideoHit], bl: object, request_id: str | None = None):
        return hits, False

    stored: dict[str, list[VideoHit]] = {}

    async def _replace(_db: object, hits: list[VideoHit]) -> None:
        stored["hits"] = hits

    monkeypatch.setattr(hr, "_seed_terms", _seed)
    monkeypatch.setattr(hr.YouTubeSearchService, "search", _search)
    monkeypatch.setattr(hr.blocklist_service, "get_blocklist", _get_bl)
    monkeypatch.setattr(hr, "moderate_hits", _moderate)
    monkeypatch.setattr(hr.recommendation_service, "replace_recommendations", _replace)

    result = await hr._harvest(object())
    assert result["stored"] == 1  # bad 跳过,good 仍入库
    assert stored["hits"][0].video_id == "g"


async def test_harvest_empty_does_not_clear_table(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _seed(_db: object) -> list[str]:
        return ["t1"]

    async def _search(self: object, term: str, limit: int) -> list[VideoHit]:
        return []  # 抓空

    replaced = {"called": False}

    async def _replace(_db: object, hits: list[VideoHit]) -> None:
        replaced["called"] = True

    monkeypatch.setattr(hr, "_seed_terms", _seed)
    monkeypatch.setattr(hr.YouTubeSearchService, "search", _search)
    monkeypatch.setattr(hr.recommendation_service, "replace_recommendations", _replace)

    result = await hr._harvest(object())
    assert result["stored"] == 0
    assert replaced["called"] is False  # 抓空绝不清表,保留上一轮


async def test_seed_terms_falls_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _none(_db: object) -> None:
        return None

    monkeypatch.setattr(hr, "get_curated_trending_queries", _none)
    terms = await hr._seed_terms(object())
    assert terms == hr._DEFAULT_SEEDS


async def test_seed_terms_uses_curated_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _curated(_db: object) -> list[str]:
        return ["大模型", "OpenAI"]

    monkeypatch.setattr(hr, "get_curated_trending_queries", _curated)
    terms = await hr._seed_terms(object())
    assert terms == ["大模型", "OpenAI"]
