from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.core.exceptions import BusinessError
from app.core.response import error
from app.i18n.codes import ErrorCode
from app.services.moderation import gate as moderation_gate
from app.services.youtube import allowlist_service, blocklist_service, channel_flag_service, search_cache
from app.services.youtube.search_service import VideoHit, YouTubeSearchService


def _hit(vid: str, channel_id: str | None = None) -> VideoHit:
    return VideoHit(
        video_id=vid,
        title=f"T {vid}",
        channel=None,
        channel_id=channel_id,
        thumbnail=f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        url=f"https://www.youtube.com/watch?v={vid}",
    )


class _GateResult:
    def scalar_one_or_none(self) -> object:
        return None  # 无 feature/discover 配置行 → gate 默认开


class _GateOpenSession:
    """kill-switch gate 直读 service_configs 列;测试给个最小会话让其走 default-on 路径
    (无配置行 → 启用),不触发 fail-open 告警。其它服务调用已在 service 层被 monkeypatch,不碰 db。"""

    async def execute(self, _stmt: object) -> _GateResult:
        return _GateResult()


def _make_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    from app.api.v1 import youtube_search

    app = FastAPI()
    app.include_router(youtube_search.router, prefix="/api/v1")

    async def _no_db() -> Any:
        return _GateOpenSession()

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

    async def _empty_blocklist(_db: Any) -> blocklist_service.Blocklist:
        return blocklist_service.Blocklist(terms=frozenset(), channel_ids=frozenset(), channel_names=frozenset())

    monkeypatch.setattr(blocklist_service, "get_blocklist", _empty_blocklist)

    async def _empty_allowlist(_db: Any) -> allowlist_service.Allowlist:
        return allowlist_service.Allowlist(frozenset(), frozenset(), frozenset())

    monkeypatch.setattr(youtube_search.allowlist_service, "get_allowlist", _empty_allowlist)
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

    async def _bl(_db: Any) -> blocklist_service.Blocklist:
        return blocklist_service.Blocklist(
            terms=frozenset({"spam"}), channel_ids=frozenset(), channel_names=frozenset()
        )

    monkeypatch.setattr(blocklist_service, "get_blocklist", _bl)
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

    async def _upsert(
        _db: Any, normalized: str, display: str, hits: list[VideoHit], *, sensitive: bool = False
    ) -> None:
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


async def test_blocked_channel_filtered_from_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    # 缓存陈旧场景:缓存里含被拉黑频道 → 读时 filter_hits 剔除(缓存不动)。
    app = _make_app(monkeypatch)

    async def _cached(_db: Any, _n: str) -> list[VideoHit]:
        return [_hit("v1", channel_id="UCblocked"), _hit("v2", channel_id="UCok")]

    async def _bl(_db: Any) -> blocklist_service.Blocklist:
        return blocklist_service.Blocklist(
            terms=frozenset(), channel_ids=frozenset({"UCblocked"}), channel_names=frozenset()
        )

    monkeypatch.setattr(search_cache, "get_cached_results", _cached)
    monkeypatch.setattr(blocklist_service, "get_blocklist", _bl)
    async with _client(app) as client:
        body = (await client.get("/api/v1/youtube/search?q=cats")).json()
    assert body["code"] == 0
    assert [i["video_id"] for i in body["data"]["items"]] == ["v2"]
    assert body["data"]["cached"] is True


async def test_blocked_channel_filtered_from_live(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch)

    async def _miss(_db: Any, _n: str) -> None:
        return None

    async def _search(_self: Any, query: str, limit: int) -> list[VideoHit]:
        return [_hit("v3", channel_id="UCbad"), _hit("v4", channel_id="UCgood")]

    async def _upsert(_db: Any, _n: str, _d: str, _h: list[VideoHit], *, sensitive: bool = False) -> None:
        return None

    async def _bl(_db: Any) -> blocklist_service.Blocklist:
        return blocklist_service.Blocklist(
            terms=frozenset(), channel_ids=frozenset({"UCbad"}), channel_names=frozenset()
        )

    monkeypatch.setattr(search_cache, "get_cached_results", _miss)
    monkeypatch.setattr(YouTubeSearchService, "search", _search)
    monkeypatch.setattr(search_cache, "upsert_results", _upsert)
    monkeypatch.setattr(blocklist_service, "get_blocklist", _bl)
    async with _client(app) as client:
        body = (await client.get("/api/v1/youtube/search?q=dogs")).json()
    assert body["code"] == 0
    assert [i["video_id"] for i in body["data"]["items"]] == ["v4"]
    assert body["data"]["cached"] is False


async def test_blocklisted_channel_skips_moderation_on_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    # miss 路径:被拉黑频道在送审前就被剔除 → filter_display/record_flags 都收不到它
    # (彻底不进 CMS/TMS),只有未拉黑频道进审;响应与缓存也只剩未拉黑项。
    from app.api.v1 import youtube_search

    app = _make_app(monkeypatch)
    moderated: list[list[str]] = []
    recorded: list[list[str]] = []
    upserts: list[list[str]] = []

    async def _miss(_db: Any, _n: str) -> None:
        return None

    async def _search(_self: Any, query: str, limit: int) -> list[VideoHit]:
        return [_hit("vbad", channel_id="UCbad"), _hit("vok", channel_id="UCok")]

    async def _bl(_db: Any) -> blocklist_service.Blocklist:
        return blocklist_service.Blocklist(
            terms=frozenset(), channel_ids=frozenset({"UCbad"}), channel_names=frozenset()
        )

    async def _filter(hits: list[VideoHit], *, request_id: Any) -> moderation_gate.DisplayModerationOutcome:
        moderated.append([h.video_id for h in hits])  # 记录 filter_display 实际看到了哪些
        return moderation_gate.DisplayModerationOutcome(kept=list(hits), blocked=[])

    async def _record(blocked: list[VideoHit]) -> None:
        recorded.append([h.video_id for h in blocked])

    async def _upsert(_db: Any, _n: str, _d: str, hits: list[VideoHit], *, sensitive: bool = False) -> None:
        upserts.append([h.video_id for h in hits])

    monkeypatch.setattr(search_cache, "get_cached_results", _miss)
    monkeypatch.setattr(YouTubeSearchService, "search", _search)
    monkeypatch.setattr(blocklist_service, "get_blocklist", _bl)
    monkeypatch.setattr(youtube_search.moderation_gate, "filter_display", _filter)
    monkeypatch.setattr(channel_flag_service, "record_flags", _record)
    monkeypatch.setattr(search_cache, "upsert_results", _upsert)
    async with _client(app) as client:
        body = (await client.get("/api/v1/youtube/search?q=dogs")).json()
    assert body["code"] == 0
    assert moderated == [["vok"]]  # 被拉黑的 vbad 根本没进 filter_display(不进 TMS)
    assert recorded == [[]]  # 也没被 record_flags 重复标记
    assert upserts == [["vok"]]  # 缓存只存未拉黑项
    assert [i["video_id"] for i in body["data"]["items"]] == ["vok"]


async def test_display_moderation_filters_before_upsert(monkeypatch: pytest.MonkeyPatch) -> None:
    # miss 路径:filter_display 剔 v1 → upsert 只收到干净子集 [v2],返回也只剩 v2
    from app.api.v1 import youtube_search

    app = _make_app(monkeypatch)
    upserts: list[list[str]] = []

    async def _miss(_db: Any, _n: str) -> None:
        return None

    async def _search(_self: Any, query: str, limit: int) -> list[VideoHit]:
        return [_hit("v1"), _hit("v2")]

    async def _upsert(_db: Any, _n: str, _d: str, hits: list[VideoHit], *, sensitive: bool = False) -> None:
        upserts.append([h.video_id for h in hits])

    async def _filter(hits: list[VideoHit], *, request_id: Any) -> moderation_gate.DisplayModerationOutcome:
        kept = [h for h in hits if h.video_id != "v1"]
        blocked = [h for h in hits if h.video_id == "v1"]
        return moderation_gate.DisplayModerationOutcome(kept=kept, blocked=blocked)

    monkeypatch.setattr(search_cache, "get_cached_results", _miss)
    monkeypatch.setattr(YouTubeSearchService, "search", _search)
    monkeypatch.setattr(search_cache, "upsert_results", _upsert)
    monkeypatch.setattr(youtube_search.moderation_gate, "filter_display", _filter)
    async with _client(app) as client:
        body = (await client.get("/api/v1/youtube/search?q=dogs")).json()
    assert body["code"] == 0
    assert [i["video_id"] for i in body["data"]["items"]] == ["v2"]
    assert upserts == [["v2"]]  # 缓存只存干净子集


async def test_display_moderation_skipped_on_cache_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    # cache hit:filter_display 不应被调用(缓存即干净子集,不重复审)
    from app.api.v1 import youtube_search

    app = _make_app(monkeypatch)

    async def _cached(_db: Any, _n: str) -> list[VideoHit]:
        return [_hit("v1")]

    def _boom(*_a: Any, **_k: Any):
        raise AssertionError("filter_display must not run on cache hit")

    monkeypatch.setattr(search_cache, "get_cached_results", _cached)
    monkeypatch.setattr(youtube_search.moderation_gate, "filter_display", _boom)
    async with _client(app) as client:
        body = (await client.get("/api/v1/youtube/search?q=cats")).json()
    assert body["code"] == 0
    assert [i["video_id"] for i in body["data"]["items"]] == ["v1"]
    assert body["data"]["cached"] is True


@pytest.mark.asyncio
async def test_miss_records_blocked_and_upserts_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch)
    upserts: list[int] = []
    recorded: list[list[str]] = []

    async def _miss(_db: Any, _n: str) -> None:
        return None

    async def _search(_self: Any, query: str, limit: int) -> list[VideoHit]:
        return [
            VideoHit(
                video_id="a",
                title="good",
                channel=None,
                channel_id="UCa",
                handle=None,
                thumbnail=None,
                url="https://y/a",
            ),
            VideoHit(
                video_id="b",
                title="bad",
                channel=None,
                channel_id="UCb",
                handle=None,
                thumbnail=None,
                url="https://y/b",
            ),
        ]

    async def _fd(hits: list[VideoHit], *, request_id: Any) -> moderation_gate.DisplayModerationOutcome:
        return moderation_gate.DisplayModerationOutcome(kept=hits[:1], blocked=hits[1:])

    async def _upsert(
        _db: Any, normalized: str, display: str, hits: list[VideoHit], *, sensitive: bool = False
    ) -> None:
        upserts.append(len(hits))

    async def _record(blocked: list[VideoHit]) -> None:
        recorded.append([h.video_id for h in blocked])

    monkeypatch.setattr(search_cache, "get_cached_results", _miss)
    monkeypatch.setattr(YouTubeSearchService, "search", _search)
    monkeypatch.setattr(moderation_gate, "filter_display", _fd)
    monkeypatch.setattr(search_cache, "upsert_results", _upsert)
    monkeypatch.setattr(channel_flag_service, "record_flags", _record)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        body = (await client.get("/api/v1/youtube/search?q=Dogs")).json()
    assert body["code"] == 0
    assert [i["video_id"] for i in body["data"]["items"]] == ["a"]  # 只返回 kept
    assert upserts == [1]  # 只缓存 kept(干净子集)
    assert recorded == [["b"]]  # blocked 交给 record_flags


async def test_blocked_results_mark_query_sensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.v1 import youtube_search

    app = _make_app(monkeypatch)
    captured: dict[str, object] = {}

    async def _miss(_db: Any, _n: str) -> None:
        return None

    async def _search(_self: Any, query: str, limit: int) -> list[VideoHit]:
        return [_hit("vok", channel_id="UCok"), _hit("vbad", channel_id="UCbad")]

    async def _filter(hits: list[VideoHit], *, request_id: Any) -> moderation_gate.DisplayModerationOutcome:
        # 一条被 block(shadow:kept 仍含全部,blocked 另列)
        blocked = [h for h in hits if h.channel_id == "UCbad"]
        return moderation_gate.DisplayModerationOutcome(kept=list(hits), blocked=blocked)

    async def _record(blocked: list[VideoHit]) -> None:
        pass

    async def _upsert(_db: Any, _n: str, _d: str, hits: list[VideoHit], *, sensitive: bool = False) -> None:
        captured["sensitive"] = sensitive

    monkeypatch.setattr(search_cache, "get_cached_results", _miss)
    monkeypatch.setattr(YouTubeSearchService, "search", _search)
    monkeypatch.setattr(youtube_search.moderation_gate, "filter_display", _filter)
    monkeypatch.setattr(channel_flag_service, "record_flags", _record)
    monkeypatch.setattr(search_cache, "upsert_results", _upsert)
    async with _client(app) as client:
        body = (await client.get("/api/v1/youtube/search?q=foo")).json()
    assert body["code"] == 0
    assert captured["sensitive"] is True


async def test_clean_results_not_sensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.v1 import youtube_search

    app = _make_app(monkeypatch)
    captured: dict[str, object] = {}

    async def _miss(_db: Any, _n: str) -> None:
        return None

    async def _search(_self: Any, query: str, limit: int) -> list[VideoHit]:
        return [_hit("vok", channel_id="UCok")]

    async def _filter(hits: list[VideoHit], *, request_id: Any) -> moderation_gate.DisplayModerationOutcome:
        return moderation_gate.DisplayModerationOutcome(kept=list(hits), blocked=[])

    async def _record(blocked: list[VideoHit]) -> None:
        pass

    async def _upsert(_db: Any, _n: str, _d: str, hits: list[VideoHit], *, sensitive: bool = False) -> None:
        captured["sensitive"] = sensitive

    monkeypatch.setattr(search_cache, "get_cached_results", _miss)
    monkeypatch.setattr(YouTubeSearchService, "search", _search)
    monkeypatch.setattr(youtube_search.moderation_gate, "filter_display", _filter)
    monkeypatch.setattr(channel_flag_service, "record_flags", _record)
    monkeypatch.setattr(search_cache, "upsert_results", _upsert)
    async with _client(app) as client:
        body = (await client.get("/api/v1/youtube/search?q=bar")).json()
    assert body["code"] == 0
    assert captured["sensitive"] is False


@pytest.mark.asyncio
async def test_cache_hit_does_not_record_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch)

    async def _cached(_db: Any, _n: str) -> list[VideoHit]:
        return [
            VideoHit(
                video_id="a", title="x", channel=None, channel_id=None, handle=None, thumbnail=None, url="https://y/a"
            )
        ]

    async def _record_boom(_blocked: list[VideoHit]) -> None:
        raise AssertionError("cache hit 不该标记")

    monkeypatch.setattr(search_cache, "get_cached_results", _cached)
    monkeypatch.setattr(channel_flag_service, "record_flags", _record_boom)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        body = (await client.get("/api/v1/youtube/search?q=cats")).json()
    assert body["code"] == 0
    assert [i["video_id"] for i in body["data"]["items"]] == ["a"]


async def test_search_disabled_returns_discover_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.v1 import youtube_search

    app = _make_app(monkeypatch)

    async def _disabled(_db: Any) -> bool:
        return False

    monkeypatch.setattr(youtube_search, "is_discover_enabled", _disabled)

    def _boom_normalize(_q: str) -> str:
        raise AssertionError("gate 未短路:normalize_query 不该被调用")

    monkeypatch.setattr(youtube_search.search_cache, "normalize_query", _boom_normalize)
    async with _client(app) as client:
        body = (await client.get("/api/v1/youtube/search?q=cats")).json()
    assert body["code"] == int(ErrorCode.DISCOVER_DISABLED)


async def test_trending_disabled_returns_discover_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.v1 import youtube_search

    app = _make_app(monkeypatch)
    app.dependency_overrides[youtube_search._trending_rate_limit] = lambda: None

    async def _disabled(_db: Any) -> bool:
        return False

    monkeypatch.setattr(youtube_search, "is_discover_enabled", _disabled)
    async with _client(app) as client:
        body = (await client.get("/api/v1/youtube/search/trending")).json()
    assert body["code"] == int(ErrorCode.DISCOVER_DISABLED)


async def test_search_enabled_passes_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.v1 import youtube_search

    app = _make_app(monkeypatch)

    async def _enabled(_db: Any) -> bool:
        return True

    monkeypatch.setattr(youtube_search, "is_discover_enabled", _enabled)

    async def _cached(_db: Any, _n: str) -> list[VideoHit]:
        return [_hit("v1")]

    monkeypatch.setattr(search_cache, "get_cached_results", _cached)
    async with _client(app) as client:
        body = (await client.get("/api/v1/youtube/search?q=cats")).json()
    assert body["code"] == 0


async def test_allowlisted_channel_skips_moderation_and_preserves_order(monkeypatch: pytest.MonkeyPatch) -> None:
    # miss 路径:放行频道在送审前被分流 → filter_display/record_flags 都收不到它,但仍在结果里且保持原序
    from app.api.v1 import youtube_search
    from app.services.moderation import gate as _gate

    app = _make_app(monkeypatch)

    hit_allow = VideoHit(
        video_id="a1",
        title="政经分析",
        channel="时事频道",
        channel_id="UCallow",
        handle=None,
        thumbnail="https://i.ytimg.com/vi/a1/hqdefault.jpg",
        url="https://www.youtube.com/watch?v=a1",
    )
    hit_normal = VideoHit(
        video_id="n1",
        title="普通视频",
        channel="别的频道",
        channel_id="UCnormal",
        handle=None,
        thumbnail="https://i.ytimg.com/vi/n1/hqdefault.jpg",
        url="https://www.youtube.com/watch?v=n1",
    )

    async def _miss(_db: Any, _n: str) -> None:
        return None

    async def _search(_self: Any, _q: str, _limit: int) -> list[VideoHit]:
        return [hit_allow, hit_normal]  # 放行频道排在前

    async def _upsert(_db: Any, _n: str, _d: str, hits: list[VideoHit], *, sensitive: bool = False) -> None:
        return None

    moderated: list[list[str]] = []

    async def _filter_real(hits: list[VideoHit], *, request_id: Any) -> _gate.DisplayModerationOutcome:
        moderated.append([h.video_id for h in hits])
        return _gate.DisplayModerationOutcome(kept=list(hits), blocked=[])

    recorded: list[list[str]] = []

    async def _record(blocked: list[VideoHit]) -> None:
        recorded.append([h.video_id for h in blocked])

    async def _allowlist(_db: Any) -> allowlist_service.Allowlist:
        return allowlist_service.Allowlist(
            channel_ids=frozenset({"UCallow"}),
            channel_handles=frozenset(),
            channel_names=frozenset(),
        )

    monkeypatch.setattr(search_cache, "get_cached_results", _miss)
    monkeypatch.setattr(YouTubeSearchService, "search", _search)
    monkeypatch.setattr(search_cache, "upsert_results", _upsert)
    monkeypatch.setattr(youtube_search.moderation_gate, "filter_display", _filter_real)
    monkeypatch.setattr(channel_flag_service, "record_flags", _record)
    monkeypatch.setattr(youtube_search.allowlist_service, "get_allowlist", _allowlist)

    async with _client(app) as client:
        body = (await client.get("/api/v1/youtube/search?q=政经")).json()
    assert body["code"] == 0
    assert moderated == [["n1"]]  # 放行频道未送审
    assert recorded == [[]]  # 放行频道未进 record_flags
    assert [i["video_id"] for i in body["data"]["items"]] == ["a1", "n1"]  # 原序保持,放行项在前
