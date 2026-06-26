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


# ---------------------------------------------------------------------------
# publish gate 集成测试
# ---------------------------------------------------------------------------

from app.api.deps import CurrentUser as _CurrentUser  # noqa: E402
from app.models.summary import Summary  # noqa: E402
from app.models.task import Task  # noqa: E402
from app.services.task_service import TaskService  # noqa: E402


class _FakeResult:
    def __init__(self, obj: Any) -> None:
        self._obj = obj

    def scalar_one_or_none(self) -> Any:
        return self._obj


class _FakeSession:
    """够本单测:execute 按目标实体分流(Task/Summary);get 返 None;commit 计数。"""

    def __init__(self, task: Task, summary: Summary | None) -> None:
        self.task = task
        self.summary = summary
        self.committed = 0

    async def execute(self, stmt: Any) -> _FakeResult:
        entity = stmt.column_descriptions[0]["entity"]
        if entity is Summary:
            return _FakeResult(self.summary)
        return _FakeResult(self.task)

    async def get(self, _model: Any, _pk: Any) -> Any:
        return None

    def add(self, _obj: Any) -> None:
        pass

    async def commit(self) -> None:
        self.committed += 1


_PUB_USER = _CurrentUser(id="11111111-1111-1111-1111-111111111111", email="a@ex.com", scopes=["admin"])


def _completed_task() -> Task:
    t = Task(
        id="22222222-2222-2222-2222-222222222222",
        user_id="11111111-1111-1111-1111-111111111111",
        title="标题",
        source_type="upload",
        source_key="upload/x.mp3",
        status="completed",
        progress=100,
        options={},
    )
    t.is_public = False
    t.published_at = None
    t.deleted_at = None
    return t


@pytest.mark.asyncio
async def test_publish_enforce_block_keeps_task_private(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate.config, "publish_mode", lambda: "enforce")

    async def _cms_block(text: str, scene: str, request_id: str | None) -> ModerationResult:
        assert scene == "ugc_publish"
        assert "标题" in text  # 审的是标题 + overview 摘要
        return ModerationResult(action="block")

    monkeypatch.setattr(gate, "_moderate", _cms_block)

    task = _completed_task()
    summary = Summary(task_id=str(task.id), summary_type="overview", content="overview 摘要正文", is_active=True)
    session = _FakeSession(task=task, summary=summary)

    with pytest.raises(BusinessError) as ei:
        await TaskService.update_task_visibility(session, _PUB_USER, str(task.id), is_public=True)
    assert ei.value.code == ErrorCode.PUBLISH_CONTENT_BLOCKED

    # 关键:被拦后任务仍私有,且没 commit
    assert task.is_public is False
    assert task.published_at is None
    assert session.committed == 0


@pytest.mark.asyncio
async def test_publish_off_makes_public(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate.config, "publish_mode", lambda: "off")  # off:跳过、不调 CMS、不查摘要

    def _boom(*_a: Any, **_k: Any) -> ModerationResult:
        raise AssertionError("off 态不该调 CMS")

    monkeypatch.setattr(gate, "_moderate", _boom)

    task = _completed_task()
    session = _FakeSession(task=task, summary=None)

    result = await TaskService.update_task_visibility(session, _PUB_USER, str(task.id), is_public=True)
    assert result.is_public is True
    assert task.is_public is True
