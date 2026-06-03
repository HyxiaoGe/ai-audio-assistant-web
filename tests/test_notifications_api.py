"""通知 REST API 契约测试（隔离打法：裸 FastAPI app 挂 router + 假 session + 依赖覆盖）。

模型是 Postgres 专用类型（JSONB/UUID + gen_random_uuid()/::jsonb server_default），
无法在 sqlite 起真实表，故沿用仓库既有「假 session + 内存列表 + 复用 handler 真实语句」
打法（参照 tests/services/test_task_list_status_filter.py / tests/test_media_ticket_auth.py）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy.dialects import postgresql

from app.api.deps import CurrentUser, get_current_user, get_db
from app.api.v1 import notifications as notif_module
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.models.notification import Notification

_USER_ID = "11111111-1111-1111-1111-111111111111"


def _make_notif(
    *,
    id: str,
    category: str = "task",
    read: bool = False,
    created_offset: int = 0,
    params: dict[str, Any] | None = None,
    type_: str = "task_completed",
) -> Notification:
    n = Notification(
        id=id,
        user_id=_USER_ID,
        category=category,
        type=type_,
        priority="normal",
        title="t",
        message="m",
        action_url=f"/tasks/{id}",
        extra_data=params or {"task_title": "演示"},
    )
    n.read_at = datetime.now(UTC) if read else None
    n.created_at = datetime.now(UTC) + timedelta(seconds=created_offset)
    return n


def _csql(stmt: Any) -> str:
    return str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()


class _FakeResult:
    def __init__(self, *, scalar: int | None = None, rows: list[Any] | None = None,
                 one: Any = "__missing__") -> None:
        self._scalar = scalar
        self._rows = rows or []
        self._one = one

    def scalar_one_or_none(self) -> Any:
        return None if self._one == "__missing__" else self._one

    def scalars(self) -> _FakeResult:
        return self

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    """内存通知列表 + 按 handler 真实语句分支的假 session。"""

    def __init__(self, notifs: list[Notification]) -> None:
        self.store = list(notifs)
        self.committed = 0

    async def scalar(self, stmt: Any) -> int:
        sql = _csql(stmt)
        rows = [n for n in self.store if n.user_id == _USER_ID]
        if "read_at is null" in sql:
            rows = [n for n in rows if n.read_at is None]
        if "category =" in sql:
            cat = sql.split("category =")[1].strip().split()[0].strip("'")
            rows = [n for n in rows if n.category == cat]
        return len(rows)

    async def execute(self, stmt: Any) -> _FakeResult:
        if getattr(stmt, "is_update", False):
            sql = _csql(stmt)
            affected = 0
            for n in self.store:
                if n.user_id != _USER_ID:
                    continue
                if "read_at is null" in sql and n.read_at is not None:
                    continue
                if n.read_at is None:
                    n.read_at = datetime.now(UTC)
                    affected += 1
            self._last_affected = affected
            return _FakeResult()
        sql = _csql(stmt)
        # count 查询：列名为 'count'
        first_col = stmt.column_descriptions[0].get("name")
        if first_col == "count":
            return _FakeResult(scalar=await self.scalar(stmt))
        # 单条按 id：WHERE 带 id =
        if "notifications.id =" in sql:
            wanted = sql.split("notifications.id =")[1].strip().split()[0].strip("'")
            match = next(
                (n for n in self.store if n.id == wanted and n.user_id == _USER_ID), None
            )
            return _FakeResult(one=match)
        # 分页 rows：施加过滤 + created_at desc
        rows = [n for n in self.store if n.user_id == _USER_ID]
        if "read_at is null" in sql:
            rows = [n for n in rows if n.read_at is None]
        if "category =" in sql:
            cat = sql.split("category =")[1].strip().split()[0].strip("'")
            rows = [n for n in rows if n.category == cat]
        rows.sort(key=lambda n: n.created_at, reverse=True)
        return _FakeResult(rows=rows)

    async def commit(self) -> None:
        self.committed += 1

    async def refresh(self, _obj: Any) -> None:
        pass


def _register_error_handler(app: FastAPI) -> None:
    from fastapi.responses import JSONResponse

    @app.exception_handler(BusinessError)
    async def _handler(_req: Any, exc: BusinessError) -> JSONResponse:
        status_map = {ErrorCode.NOTIFICATION_NOT_FOUND: 404}
        return JSONResponse({"code": int(exc.code)}, status_code=status_map.get(exc.code, 500))


def _app(session: _FakeSession) -> FastAPI:
    app = FastAPI()
    app.include_router(notif_module.router, prefix="/api/v1")

    async def _db() -> AsyncIterator[Any]:
        yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(id=_USER_ID, email="u@ex.com")
    _register_error_handler(app)
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_list_returns_type_and_params() -> None:
    session = _FakeSession([_make_notif(id="n1", params={"task_title": "演示", "duration": 12})])
    async with _client(_app(session)) as client:
        resp = await client.get("/api/v1/notifications")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 1
    item = data["items"][0]
    assert item["type"] == "task_completed"
    assert item["params"] == {"task_title": "演示", "duration": 12}
    assert item["action_url"] == "/tasks/n1"
    assert "dismissed_at" not in item
    assert "extra_data" not in item


async def test_list_unread_only_filter() -> None:
    session = _FakeSession([
        _make_notif(id="n1", read=False),
        _make_notif(id="n2", read=True),
    ])
    async with _client(_app(session)) as client:
        resp = await client.get("/api/v1/notifications", params={"unread_only": "true"})
    data = resp.json()["data"]
    assert data["total"] == 1
    assert [i["id"] for i in data["items"]] == ["n1"]


async def test_list_category_filter() -> None:
    session = _FakeSession([
        _make_notif(id="n1", category="task"),
        _make_notif(id="n2", category="system"),
    ])
    async with _client(_app(session)) as client:
        resp = await client.get("/api/v1/notifications", params={"category": "system"})
    data = resp.json()["data"]
    assert data["total"] == 1
    assert [i["id"] for i in data["items"]] == ["n2"]


async def test_stats_returns_total_and_unread() -> None:
    session = _FakeSession([
        _make_notif(id="n1", read=False),
        _make_notif(id="n2", read=False),
        _make_notif(id="n3", read=True),
    ])
    async with _client(_app(session)) as client:
        resp = await client.get("/api/v1/notifications/stats")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data == {"total": 3, "unread": 2}


async def test_stats_has_no_dismissed_field() -> None:
    session = _FakeSession([_make_notif(id="n1", read=False)])
    async with _client(_app(session)) as client:
        resp = await client.get("/api/v1/notifications/stats")
    assert "dismissed" not in resp.json()["data"]


async def test_mark_read_returns_unread_and_sets_read_at() -> None:
    session = _FakeSession([
        _make_notif(id="n1", read=False),
        _make_notif(id="n2", read=False),
    ])
    async with _client(_app(session)) as client:
        resp = await client.patch("/api/v1/notifications/n1/read")
    assert resp.status_code == 200
    assert resp.json()["data"] == {"unread": 1}
    assert session.store[0].read_at is not None


async def test_mark_read_is_idempotent() -> None:
    n = _make_notif(id="n1", read=False)
    session = _FakeSession([n])
    async with _client(_app(session)) as client:
        first = await client.patch("/api/v1/notifications/n1/read")
        first_read_at = session.store[0].read_at
        second = await client.patch("/api/v1/notifications/n1/read")
    assert first.json()["data"] == {"unread": 0}
    assert second.json()["data"] == {"unread": 0}
    # 第二次不改写 read_at（幂等：仅 read_at 为空才写）
    assert session.store[0].read_at == first_read_at


async def test_mark_read_missing_id_returns_404() -> None:
    session = _FakeSession([_make_notif(id="n1")])
    async with _client(_app(session)) as client:
        resp = await client.patch("/api/v1/notifications/does-not-exist/read")
    assert resp.status_code == 404
    assert resp.json()["code"] == int(ErrorCode.NOTIFICATION_NOT_FOUND)


async def test_read_all_returns_affected_count() -> None:
    session = _FakeSession([
        _make_notif(id="n1", read=False),
        _make_notif(id="n2", read=False),
        _make_notif(id="n3", read=True),  # 已读不计入 affected
    ])
    async with _client(_app(session)) as client:
        resp = await client.patch("/api/v1/notifications/read-all")
    assert resp.status_code == 200
    assert resp.json()["data"] == {"affected": 2, "unread": 0}
    assert all(n.read_at is not None for n in session.store)


async def test_read_all_not_shadowed_by_id_route() -> None:
    # /read-all 必须命中 read-all handler，而不是被 /{id}/read 当成 id="read-all"。
    # 命中 id 路由会走 scalar_one_or_none(None) → 404；命中 read-all 则 200 + affected。
    session = _FakeSession([_make_notif(id="n1", read=False)])
    async with _client(_app(session)) as client:
        resp = await client.patch("/api/v1/notifications/read-all")
    assert resp.status_code == 200
    assert "affected" in resp.json()["data"]


async def test_delete_single_endpoint_removed() -> None:
    # 纯未读/已读无删除：DELETE /notifications/{id} 已下线。
    # 路径 /notifications/{id} 仍有 PATCH(/{id}/read 不同路径)，DELETE 该裸路径 → 404（无此路由）。
    session = _FakeSession([_make_notif(id="n1")])
    async with _client(_app(session)) as client:
        resp = await client.delete("/api/v1/notifications/n1")
    assert resp.status_code in (404, 405)


async def test_clear_endpoint_removed() -> None:
    session = _FakeSession([_make_notif(id="n1")])
    async with _client(_app(session)) as client:
        resp = await client.delete("/api/v1/notifications/clear")
    assert resp.status_code in (404, 405)


def test_router_has_no_delete_routes() -> None:
    from app.api.v1 import notifications as mod

    methods = set()
    for route in mod.router.routes:
        methods |= set(getattr(route, "methods", set()) or set())
    assert "DELETE" not in methods
