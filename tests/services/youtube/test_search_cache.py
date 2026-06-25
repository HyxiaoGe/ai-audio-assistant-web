from datetime import UTC, datetime, timedelta

import pytest

from app.services.youtube import search_cache as sc
from app.services.youtube.search_cache import TrendingItem, normalize_query
from app.services.youtube.search_service import VideoHit


class _Result:
    def __init__(self, row=None, rows=None) -> None:
        self._row = row
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._row

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, result: _Result) -> None:
        self._result = result
        self.committed = False

    async def execute(self, _stmt):
        return self._result

    async def commit(self) -> None:
        self.committed = True


class _Row:
    def __init__(
        self, normalized_query, display_query, results_json, fetched_at, search_count=0, last_searched_at=None
    ) -> None:
        self.normalized_query = normalized_query
        self.display_query = display_query
        self.results_json = results_json
        self.fetched_at = fetched_at
        self.search_count = search_count
        self.last_searched_at = last_searched_at


class _FakeRedis:
    def __init__(self, returns) -> None:
        self._returns = returns
        self.calls = []

    async def set(self, key, value, ex=None, nx=None):
        self.calls.append((key, ex, nx))
        return self._returns


def _hit(vid: str) -> VideoHit:
    return VideoHit(
        video_id=vid,
        title=f"T {vid}",
        channel=None,
        channel_id=None,
        thumbnail=f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        url=f"https://www.youtube.com/watch?v={vid}",
    )


def test_normalize_query_trims_collapses_casefolds() -> None:
    assert normalize_query("  Hello   WORLD  ") == "hello world"
    assert normalize_query("\tPython\n Tutorial ") == "python tutorial"


def test_is_denylisted_compares_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sc.settings, "YOUTUBE_SEARCH_DENYLIST", ["  Bad Word "])
    assert sc.is_denylisted("bad word") is True
    assert sc.is_denylisted("good") is False


async def test_get_cached_results_fresh_returns_hits() -> None:
    now = datetime.now(UTC)
    row = _Row("q", "q", [_hit("v1").model_dump()], fetched_at=now)
    out = await sc.get_cached_results(_FakeSession(_Result(row=row)), "q")
    assert out is not None
    assert out[0].video_id == "v1"


async def test_get_cached_results_expired_returns_none() -> None:
    old = datetime.now(UTC) - timedelta(hours=7)
    row = _Row("q", "q", [_hit("v1").model_dump()], fetched_at=old)
    assert await sc.get_cached_results(_FakeSession(_Result(row=row)), "q") is None


async def test_get_cached_results_missing_returns_none() -> None:
    assert await sc.get_cached_results(_FakeSession(_Result(row=None)), "q") is None


async def test_heat_is_new_searcher_true_when_setnx_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sc, "get_redis_client", lambda: _FakeRedis(returns=True))
    assert await sc.heat_is_new_searcher("q", "user-1") is True


async def test_heat_is_new_searcher_false_when_setnx_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sc, "get_redis_client", lambda: _FakeRedis(returns=None))
    assert await sc.heat_is_new_searcher("q", "user-1") is False


async def test_heat_is_new_searcher_failopen_true_on_redis_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(sc, "get_redis_client", _boom)
    assert await sc.heat_is_new_searcher("q", "user-1") is True


async def test_upsert_results_commits(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeSession(_Result())
    await sc.upsert_results(db, "hello world", "Hello World", [_hit("v1")])
    assert db.committed is True


async def test_register_query_heat_commits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sc, "get_redis_client", lambda: _FakeRedis(returns=True))
    db = _FakeSession(_Result())
    await sc.register_query_heat(db, "hello world", "user-1")
    assert db.committed is True


async def test_get_trending_below_threshold_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sc.settings, "YOUTUBE_TRENDING_MIN_VOLUME", 20)
    rows = [_Row(f"q{i}", f"q{i}", [], None, search_count=i) for i in range(3)]
    assert await sc.get_trending(_FakeSession(_Result(rows=rows))) == []


async def test_get_trending_sorts_filters_denylist_and_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sc.settings, "YOUTUBE_TRENDING_MIN_VOLUME", 2)
    monkeypatch.setattr(sc.settings, "YOUTUBE_TRENDING_TOP_N", 2)
    monkeypatch.setattr(sc.settings, "YOUTUBE_SEARCH_DENYLIST", ["spam"])
    rows = [
        _Row("a", "A", [], None, search_count=5),
        _Row("spam", "Spam", [], None, search_count=99),  # denylist 应被过滤
        _Row("b", "B", [], None, search_count=10),
        _Row("c", "C", [], None, search_count=1),
    ]
    out = await sc.get_trending(_FakeSession(_Result(rows=rows)))
    assert [i.query for i in out] == ["B", "A"]  # 降序 + top-2,spam 被滤
    assert out[0] == TrendingItem(query="B", count=10)


async def test_get_trending_threshold_uses_post_denylist_count(monkeypatch: pytest.MonkeyPatch) -> None:
    # Raw rows = 3 distinct queries; 2 are denylisted → eligible = 1 < MIN_VOLUME=2 → expect []
    # This pins that the threshold is evaluated against len(eligible), not len(rows).
    monkeypatch.setattr(sc.settings, "YOUTUBE_TRENDING_MIN_VOLUME", 2)
    monkeypatch.setattr(sc.settings, "YOUTUBE_TRENDING_TOP_N", 10)
    monkeypatch.setattr(sc.settings, "YOUTUBE_SEARCH_DENYLIST", ["bad one", "bad two"])
    rows = [
        _Row("bad one", "Bad One", [], None, search_count=50),   # denylisted (normalize → "bad one")
        _Row("bad two", "Bad Two", [], None, search_count=40),   # denylisted (normalize → "bad two")
        _Row("good", "Good", [], None, search_count=30),          # eligible
    ]
    assert await sc.get_trending(_FakeSession(_Result(rows=rows))) == []
