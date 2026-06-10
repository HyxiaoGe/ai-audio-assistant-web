"""任务公开可见性开关(仅管理员,且只能操作本人 completed 任务)契约测试。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import FastAPI, Request
from httpx import ASGITransport
from sqlalchemy.dialects import postgresql

from app.api.deps import CurrentUser, get_admin_user, get_current_user, get_db
from app.api.v1 import tasks as tasks_module
from app.core.exceptions import BusinessError
from app.core.response import error
from app.i18n.codes import ErrorCode
from app.models.task import Task

_ADMIN_ID = "11111111-1111-1111-1111-111111111111"
_OTHER_ID = "99999999-9999-9999-9999-999999999999"
_TASK_ID = "22222222-2222-2222-2222-222222222222"


def _make_task(*, user_id: str = _ADMIN_ID, status: str = "completed", is_public: bool = False) -> Task:
    t = Task(
        id=_TASK_ID,
        user_id=user_id,
        title="任务",
        source_type="upload",
        source_key=f"upload/{user_id}/{_TASK_ID}.mp3",
        status=status,
        progress=100,
        options={},
    )
    t.is_public = is_public
    t.published_at = datetime.now(UTC) if is_public else None
    t.deleted_at = None
    return t


def _csql(stmt: Any) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).lower()


def _normalize_uuid(raw: str) -> str:
    """去连字符后的 32 位十六进制串与含连字符版本双向兼容比较。"""
    return raw.replace("-", "").lower()


class _FakeResult:
    def __init__(self, one: Any) -> None:
        self._one = one

    def scalar_one_or_none(self) -> Any:
        return self._one


class _FakeSession:
    def __init__(self, tasks: list[Task]) -> None:
        self.tasks = tasks
        self.committed = 0

    async def commit(self) -> None:
        self.committed += 1

    async def execute(self, stmt: Any) -> _FakeResult:
        sql = _csql(stmt)
        rows = list(self.tasks)
        if "tasks.id =" in sql:
            wanted_raw = sql.split("tasks.id =")[1].strip().split()[0].strip("'")
            wanted = _normalize_uuid(wanted_raw)
            rows = [t for t in rows if _normalize_uuid(str(t.id)) == wanted]
        if "tasks.user_id =" in sql:
            wanted_raw = sql.split("tasks.user_id =")[1].strip().split()[0].strip("'")
            wanted = _normalize_uuid(wanted_raw)
            rows = [t for t in rows if _normalize_uuid(str(t.user_id)) == wanted]
        if "deleted_at is null" in sql:
            rows = [t for t in rows if t.deleted_at is None]
        return _FakeResult(one=rows[0] if rows else None)


def _make_app(session: _FakeSession, *, admin: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(tasks_module.router)

    @app.exception_handler(BusinessError)
    async def _handle(_req: Request, exc: BusinessError) -> Any:
        return error(int(exc.code), exc.code.name)

    async def _db() -> AsyncIterator[_FakeSession]:
        yield session

    app.dependency_overrides[get_db] = _db
    if admin:
        app.dependency_overrides[get_admin_user] = lambda: CurrentUser(
            id=_ADMIN_ID, email="admin@ex.com", scopes=["admin"]
        )
    else:
        # 不覆盖 get_admin_user:让真实 scope 校验跑起来,只喂一个无 admin scope 的用户
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=_ADMIN_ID, email="u@ex.com")
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_non_admin_forbidden() -> None:
    session = _FakeSession([_make_task()])
    async with _client(_make_app(session, admin=False)) as client:
        body = (await client.patch(f"/tasks/{_TASK_ID}/visibility", json={"is_public": True})).json()
    assert body["code"] == int(ErrorCode.PERMISSION_DENIED)
    assert session.committed == 0


async def test_admin_publishes_own_completed_task() -> None:
    session = _FakeSession([_make_task()])
    async with _client(_make_app(session)) as client:
        body = (await client.patch(f"/tasks/{_TASK_ID}/visibility", json={"is_public": True})).json()
    assert body["code"] == 0
    assert body["data"]["is_public"] is True
    assert body["data"]["published_at"] is not None
    assert session.committed == 1


async def test_admin_cannot_publish_others_task() -> None:
    session = _FakeSession([_make_task(user_id=_OTHER_ID)])
    async with _client(_make_app(session)) as client:
        body = (await client.patch(f"/tasks/{_TASK_ID}/visibility", json={"is_public": True})).json()
    assert body["code"] == int(ErrorCode.TASK_NOT_FOUND)


async def test_cannot_publish_uncompleted_task() -> None:
    session = _FakeSession([_make_task(status="summarizing")])
    async with _client(_make_app(session)) as client:
        body = (await client.patch(f"/tasks/{_TASK_ID}/visibility", json={"is_public": True})).json()
    assert body["code"] == int(ErrorCode.INVALID_PARAMETER)


async def test_unpublish_clears_published_at_and_is_idempotent() -> None:
    session = _FakeSession([_make_task(is_public=True)])
    async with _client(_make_app(session)) as client:
        first = (await client.patch(f"/tasks/{_TASK_ID}/visibility", json={"is_public": False})).json()
        second = (await client.patch(f"/tasks/{_TASK_ID}/visibility", json={"is_public": False})).json()
    assert first["data"]["is_public"] is False
    assert first["data"]["published_at"] is None  # 取消公开清空,重新公开时刷新发布时间
    assert second["code"] == 0  # 幂等


async def test_republish_refreshes_published_at() -> None:
    session = _FakeSession([_make_task()])
    async with _client(_make_app(session)) as client:
        await client.patch(f"/tasks/{_TASK_ID}/visibility", json={"is_public": True})
        stamp1 = session.tasks[0].published_at
        await client.patch(f"/tasks/{_TASK_ID}/visibility", json={"is_public": True})
        stamp2 = session.tasks[0].published_at  # 已公开再公开:幂等,不刷新时间
    assert stamp1 == stamp2
