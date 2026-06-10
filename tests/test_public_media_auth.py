"""公开媒体票(resource 钉死 public_task)在媒体端点的允许集复核测试。

裸 app 挂真实 media router + 假 session;serve_media_object 打桩(存储层不在被测面)。
存量无 pin 票据行为由 tests/test_media_ticket_auth.py 覆盖,这里只加一条回归锚。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request, Response
from httpx import ASGITransport
from sqlalchemy.dialects import postgresql

from app.api.deps import get_db
from app.api.v1 import media as media_module
from app.config import settings
from app.core.exceptions import BusinessError
from app.core.response import error
from app.core.security import SCOPE_MEDIA, issue_scoped_token
from app.i18n.codes import ErrorCode
from app.models.task import Task

_OWNER_ID = "11111111-1111-1111-1111-111111111111"
_OTHER_ID = "99999999-9999-9999-9999-999999999999"
_TASK_ID = "22222222-2222-2222-2222-222222222222"
_SOURCE_KEY = f"upload/{_OWNER_ID}/{_TASK_ID}.mp3"
_TEST_SECRET = "unit-test-secret-not-production"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "JWT_SECRET", _TEST_SECRET)

    async def _fake_serve(file_path: str, range_header: str | None = None, *, allow_redirect: bool = True) -> Response:
        return Response(content=b"ok", media_type="application/octet-stream")

    monkeypatch.setattr(media_module, "serve_media_object", _fake_serve)


def _make_task(*, is_public: bool = True, status: str = "completed") -> Task:
    t = Task(
        id=_TASK_ID,
        user_id=_OWNER_ID,
        title="任务",
        source_type="upload",
        source_key=_SOURCE_KEY,
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


def _normalize_uuid(value: str) -> str:
    return value.replace("-", "")


class _FakeResult:
    def __init__(self, one: Any) -> None:
        self._one = one

    def scalar_one_or_none(self) -> Any:
        return self._one


class _FakeSession:
    def __init__(self, tasks: list[Task]) -> None:
        self.tasks = tasks

    async def execute(self, stmt: Any) -> _FakeResult:
        sql = _csql(stmt)
        rows = list(self.tasks)
        if "is_public is true" in sql:
            rows = [t for t in rows if t.is_public]
        if "status = 'completed'" in sql:
            rows = [t for t in rows if t.status == "completed"]
        if "deleted_at is null" in sql:
            rows = [t for t in rows if t.deleted_at is None]
        if "tasks.id =" in sql:
            wanted = sql.split("tasks.id =")[1].strip().split()[0].strip("'")
            rows = [t for t in rows if _normalize_uuid(str(t.id)) == _normalize_uuid(wanted)]
        if "tasks.user_id =" in sql:
            wanted = sql.split("tasks.user_id =")[1].strip().split()[0].strip("'")
            rows = [t for t in rows if _normalize_uuid(str(t.user_id)) == _normalize_uuid(wanted)]
        return _FakeResult(one=rows[0] if rows else None)


def _make_app(tasks: list[Task]) -> FastAPI:
    app = FastAPI()
    app.include_router(media_module.router, prefix="/media")

    @app.exception_handler(BusinessError)
    async def _handle(_req: Request, exc: BusinessError) -> Any:
        return error(int(exc.code), exc.code.name)

    async def _db() -> AsyncIterator[_FakeSession]:
        yield _FakeSession(tasks)

    app.dependency_overrides[get_db] = _db
    return app


def _public_ticket(*, sub: str = _OWNER_ID, task_id: str = _TASK_ID) -> str:
    return issue_scoped_token(sub=sub, scope=SCOPE_MEDIA, ttl=300, resource={"public_task": task_id})


def _legacy_ticket(sub: str) -> str:
    return issue_scoped_token(sub=sub, scope=SCOPE_MEDIA, ttl=300)


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_public_ticket_allows_task_audio() -> None:
    async with _client(_make_app([_make_task()])) as client:
        resp = await client.get(f"/media/{_SOURCE_KEY}", params={"token": _public_ticket()})
    assert resp.status_code == 200 and resp.content == b"ok"


async def test_public_ticket_allows_task_summary_images_namespace() -> None:
    key = f"summary_images/{_OWNER_ID}/{_TASK_ID}/img.webp"
    async with _client(_make_app([_make_task()])) as client:
        resp = await client.get(f"/media/{key}", params={"token": _public_ticket()})
    assert resp.status_code == 200


async def test_public_ticket_denies_owner_other_media() -> None:
    # 同一 owner 的另一份音频不在该任务允许集内:pin 票绝不解锁整个命名空间
    other_key = f"upload/{_OWNER_ID}/other-file.mp3"
    async with _client(_make_app([_make_task()])) as client:
        body = (await client.get(f"/media/{other_key}", params={"token": _public_ticket()})).json()
    assert body["code"] == int(ErrorCode.RESOURCE_NOT_FOUND)


async def test_public_ticket_dies_when_unpublished() -> None:
    # 取消公开后,已签发未过期的票立即失效(每次请求 DB 复核 is_public)
    async with _client(_make_app([_make_task(is_public=False)])) as client:
        body = (await client.get(f"/media/{_SOURCE_KEY}", params={"token": _public_ticket()})).json()
    assert body["code"] == int(ErrorCode.RESOURCE_NOT_FOUND)


async def test_public_ticket_cross_owner_key_denied() -> None:
    # key 第二段不是票 sub:assert_owns_media_key 双保险直接拒
    foreign_key = f"upload/{_OTHER_ID}/{_TASK_ID}.mp3"
    async with _client(_make_app([_make_task()])) as client:
        body = (await client.get(f"/media/{foreign_key}", params={"token": _public_ticket()})).json()
    assert body["code"] == int(ErrorCode.RESOURCE_NOT_FOUND)


async def test_malformed_resource_ticket_rejected() -> None:
    bad = issue_scoped_token(sub=_OWNER_ID, scope=SCOPE_MEDIA, ttl=300, resource={"unexpected": "shape"})
    async with _client(_make_app([_make_task()])) as client:
        body = (await client.get(f"/media/{_SOURCE_KEY}", params={"token": bad})).json()
    assert body["code"] == int(ErrorCode.AUTH_TOKEN_INVALID)


async def test_legacy_ticket_owner_path_unchanged() -> None:
    # 无 pin 存量票:owner 命名空间放行、跨租户拒——回归锚
    async with _client(_make_app([_make_task()])) as client:
        ok = await client.get(f"/media/{_SOURCE_KEY}", params={"token": _legacy_ticket(_OWNER_ID)})
        denied = (await client.get(f"/media/{_SOURCE_KEY}", params={"token": _legacy_ticket(_OTHER_ID)})).json()
    assert ok.status_code == 200
    assert denied["code"] == int(ErrorCode.RESOURCE_NOT_FOUND)
