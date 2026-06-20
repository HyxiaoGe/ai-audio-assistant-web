"""转写全文搜索端点 GET /tasks/search 契约测试(裸 app + 假 session,SQL 形状用
postgres 方言编译断言;FTS 语义正确性在真实 PG 手验,sqlite 测不了 to_tsvector)。

设计:跨用户隔离的「哪个视频提到 X + 跳时间戳」搜索 —— jiebacfg 中文分词 FTS,
按 ts_rank 排序,返回每个命中转写段的 {task_id, title, snippet, start_time, rank}。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import httpx
from fastapi import FastAPI, Request
from httpx import ASGITransport
from sqlalchemy.dialects import postgresql

from app.api.deps import CurrentUser, get_current_user, get_db
from app.api.v1 import tasks as tasks_module
from app.core.exceptions import BusinessError
from app.core.response import error
from app.services import transcript_search

_USER_ID = "11111111-1111-1111-1111-111111111111"
_TASK_A = "22222222-2222-2222-2222-222222222222"
_TASK_B = "33333333-3333-3333-3333-333333333333"


def _csql(stmt: Any) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).lower()


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def __iter__(self) -> Any:
        return iter(self._rows)


class _FakeSession:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self.rows = rows or []
        self.executed = 0
        self.last_sql: str | None = None

    async def execute(self, stmt: Any) -> _FakeResult:
        self.executed += 1
        self.last_sql = _csql(stmt)
        return _FakeResult(self.rows)


def _hit_row(*, task_id: str, title: str, content: str, start_time: float, rank: float) -> SimpleNamespace:
    # DB 现在返回原始 content 整段;高亮在应用层做(见下方 build_search_statement 不再用 ts_headline)。
    return SimpleNamespace(task_id=task_id, title=title, content=content, start_time=start_time, rank=rank)


def _make_app(session: _FakeSession) -> FastAPI:
    app = FastAPI()
    app.include_router(tasks_module.router)

    @app.exception_handler(BusinessError)
    async def _handle(_req: Request, exc: BusinessError) -> Any:
        return error(int(exc.code), exc.code.name)

    async def _db() -> AsyncIterator[_FakeSession]:
        yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=_USER_ID, email="u@ex.com")
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ===== SQL 形状(jieba FTS + 用户隔离 + 软删过滤 + 排序 + limit) =====


def test_search_statement_uses_jieba_fts_and_scopes_user() -> None:
    sql = _csql(transcript_search.build_search_statement(_USER_ID, "长城", 20))
    # 中文分词 FTS:索引侧 to_tsvector 与查询侧 websearch_to_tsquery 都用 jiebacfg
    assert "to_tsvector('jiebacfg'" in sql
    assert "websearch_to_tsquery('jiebacfg'" in sql
    assert "@@" in sql
    # 返回原始整段 content(短句),高亮在应用层做;弃用 ts_headline(pg_jieba 1.1.1
    # 字节偏移 bug 会删掉命中的中文 token 而非高亮,实证 '库克' 被删空)。
    assert "ts_headline" not in sql
    assert "transcripts.content" in sql
    # 相关性排序
    assert "ts_rank" in sql
    assert "order by" in sql and "desc" in sql
    # 跨用户隔离 + 软删过滤(转写无软删列,经 join 按任务的 user_id + deleted_at 过滤)
    assert "tasks.user_id =" in sql
    assert "tasks.deleted_at is null" in sql
    assert "limit 20" in sql


# ===== 端点装配/映射 =====


async def test_search_returns_ranked_hits() -> None:
    # DB 返回原始整段 content;端点须在映射时按查询词应用 <mark> 高亮。
    session = _FakeSession(
        rows=[
            _hit_row(task_id=_TASK_A, title="爬长城", content="去长城玩", start_time=12.5, rank=0.9),
            _hit_row(task_id=_TASK_B, title="另一个", content="长城很长", start_time=3.0, rank=0.4),
        ]
    )
    async with _client(_make_app(session)) as client:
        body = (await client.get("/tasks/search", params={"q": "长城"})).json()
    assert body["code"] == 0
    assert body["data"]["query"] == "长城"
    hits = body["data"]["hits"]
    assert [h["task_id"] for h in hits] == [_TASK_A, _TASK_B]
    # 高亮在应用层(_highlight)对原始 content 应用,而非依赖 DB 的 ts_headline
    assert hits[0]["snippet"] == "去<mark>长城</mark>玩"
    assert hits[1]["snippet"] == "<mark>长城</mark>很长"
    assert hits[0]["start_time"] == 12.5
    assert hits[0]["title"] == "爬长城"


async def test_blank_query_returns_no_hits_without_db_call() -> None:
    session = _FakeSession(rows=[_hit_row(task_id=_TASK_A, title="x", content="y", start_time=1.0, rank=0.1)])
    async with _client(_make_app(session)) as client:
        body = (await client.get("/tasks/search", params={"q": "   "})).json()
    assert body["code"] == 0
    assert body["data"]["hits"] == []
    assert session.executed == 0  # 空查询不打 DB


async def test_missing_query_is_422() -> None:
    session = _FakeSession()
    async with _client(_make_app(session)) as client:
        resp = await client.get("/tasks/search")
    assert resp.status_code == 422


async def test_limit_over_max_is_422() -> None:
    session = _FakeSession()
    async with _client(_make_app(session)) as client:
        resp = await client.get("/tasks/search", params={"q": "长城", "limit": 999})
    assert resp.status_code == 422
