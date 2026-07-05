"""转写段落手动编辑端点 PATCH /transcripts/{task_id}/segments/{segment_id} 契约测试。

裸 app + 假 session(与 test_task_search_api 同风格):
- 归属校验:task 按 user_id + task_id + 软删过滤;segment 按 id + task_id(防跨任务改)。
- 首次编辑:content 写新值、is_edited 置 True、original_content 存旧值。
- 二次编辑:original_content 保留最早原文(不被覆盖)。
- 未找到 task→40401,未找到 segment→40402,空白内容→422。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
from fastapi import FastAPI, Request
from httpx import ASGITransport
from sqlalchemy.dialects import postgresql

from app.api.deps import CurrentUser, get_current_user, get_db
from app.api.v1 import transcripts as transcripts_module
from app.core.exceptions import BusinessError
from app.core.response import error

_USER_ID = "11111111-1111-1111-1111-111111111111"
_TASK_ID = "22222222-2222-2222-2222-222222222222"
_SEG_ID = "33333333-3333-3333-3333-333333333333"
_URL = f"/transcripts/{_TASK_ID}/segments/{_SEG_ID}"


def _csql(stmt: Any) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).lower()


class _ScalarResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _FakeSession:
    """按 execute 调用顺序依次弹出预置结果;记录编译后的 SQL 与 commit 次数。"""

    def __init__(self, results: list[Any]) -> None:
        self._results = list(results)
        self.sqls: list[str] = []
        self.commits = 0

    async def execute(self, stmt: Any) -> _ScalarResult:
        self.sqls.append(_csql(stmt))
        return _ScalarResult(self._results.pop(0) if self._results else None)

    async def commit(self) -> None:
        self.commits += 1


def _seg(**over: Any) -> SimpleNamespace:
    base: dict[str, Any] = dict(
        id=_SEG_ID,
        speaker_id="0",
        speaker_label=None,
        content="old text",
        start_time=1.0,
        end_time=2.0,
        confidence=None,
        words=None,
        sequence=1,
        is_edited=False,
        original_content=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    base.update(over)
    return SimpleNamespace(**base)


def _make_app(session: _FakeSession) -> FastAPI:
    app = FastAPI()
    app.include_router(transcripts_module.router)

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


async def test_first_edit_sets_original_and_flips_is_edited() -> None:
    seg = _seg(content="old text", is_edited=False, original_content=None)
    session = _FakeSession(results=[SimpleNamespace(id=_TASK_ID), seg])
    async with _client(_make_app(session)) as client:
        body = (await client.patch(_URL, json={"content": "new text"})).json()

    assert body["code"] == 0
    item = body["data"]
    assert item["content"] == "new text"
    assert item["is_edited"] is True
    assert item["original_content"] == "old text"
    # 落到对象上并 commit
    assert seg.content == "new text"
    assert seg.is_edited is True
    assert seg.original_content == "old text"
    assert session.commits == 1
    # 归属作用域:task 按 user_id + 软删;segment 按 id + task_id
    assert "tasks.user_id =" in session.sqls[0]
    assert "tasks.deleted_at is null" in session.sqls[0]
    assert "transcripts.id =" in session.sqls[1]
    assert "transcripts.task_id =" in session.sqls[1]


async def test_second_edit_preserves_earliest_original() -> None:
    seg = _seg(content="first edit", is_edited=True, original_content="the real original")
    session = _FakeSession(results=[SimpleNamespace(id=_TASK_ID), seg])
    async with _client(_make_app(session)) as client:
        body = (await client.patch(_URL, json={"content": "second edit"})).json()

    assert body["code"] == 0
    assert seg.content == "second edit"
    assert seg.original_content == "the real original"  # 不被覆盖
    assert seg.is_edited is True
    assert session.commits == 1


async def test_missing_task_returns_task_not_found() -> None:
    session = _FakeSession(results=[None])  # task 查不到(不归属/不存在/软删)
    async with _client(_make_app(session)) as client:
        body = (await client.patch(_URL, json={"content": "x"})).json()

    assert body["code"] == 40401  # TASK_NOT_FOUND
    assert session.commits == 0


async def test_missing_segment_returns_transcript_not_found() -> None:
    session = _FakeSession(results=[SimpleNamespace(id=_TASK_ID), None])  # task 有,segment 无
    async with _client(_make_app(session)) as client:
        body = (await client.patch(_URL, json={"content": "x"})).json()

    assert body["code"] == 40402  # TRANSCRIPT_NOT_FOUND
    assert session.commits == 0


async def test_blank_content_is_422() -> None:
    session = _FakeSession(results=[SimpleNamespace(id=_TASK_ID), _seg()])
    async with _client(_make_app(session)) as client:
        resp = await client.patch(_URL, json={"content": "   "})
    assert resp.status_code == 422
    assert session.commits == 0


async def test_empty_content_is_422() -> None:
    session = _FakeSession(results=[SimpleNamespace(id=_TASK_ID), _seg()])
    async with _client(_make_app(session)) as client:
        resp = await client.patch(_URL, json={"content": ""})
    assert resp.status_code == 422
    assert session.commits == 0
