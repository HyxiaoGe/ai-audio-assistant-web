from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import FastAPI, Request
from httpx import ASGITransport

from app.api.deps import CurrentUser, get_admin_user, get_db
from app.api.v1 import admin_tasks
from app.core.exceptions import BusinessError
from app.core.response import error
from app.i18n.codes import ErrorCode
from app.schemas.admin_task import AdminUserTaskItem
from app.schemas.public import PublicSummaryListResponse, PublicTranscriptListResponse
from app.schemas.task import TaskDetailResponse
from app.services.task_service import TaskService

_ADMIN = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_UID_B = "22222222-2222-2222-2222-222222222222"
_TID = "33333333-3333-3333-3333-333333333333"


def _make_app(*, admin_ok: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(admin_tasks.router, prefix="/api/v1")

    @app.exception_handler(BusinessError)
    async def _handle(_req: Request, exc: BusinessError) -> Any:
        return error(int(exc.code), exc.code.name)

    async def _db() -> AsyncIterator[object]:
        yield object()

    app.dependency_overrides[get_db] = _db
    if admin_ok:
        app.dependency_overrides[get_admin_user] = lambda: CurrentUser(id=_ADMIN, email="a@ex.com", scopes=["admin"])
    else:

        def _forbid() -> CurrentUser:
            raise BusinessError(ErrorCode.PERMISSION_DENIED)

        app.dependency_overrides[get_admin_user] = _forbid
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_list_tasks_ok(monkeypatch: Any) -> None:
    async def _list(_db: Any, uid: str, page: int, page_size: int, status_filter: str, q: Any = None) -> Any:
        assert uid == _UID_B
        return [
            AdminUserTaskItem(
                id="t1",
                title="标题",
                source_type="youtube",
                status="completed",
                progress=100,
                duration_seconds=10,
                created_at="2026-06-29T00:00:00Z",
                channel_title="频道",
                error_message=None,
            )
        ], 1

    monkeypatch.setattr(TaskService, "list_user_tasks_for_admin", _list)
    async with _client(_make_app()) as c:
        r = await c.get(f"/api/v1/admin/users/{_UID_B}/tasks?page=1&page_size=20")
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["total"] == 1
    assert body["data"]["items"][0]["channel_title"] == "频道"


async def test_list_tasks_passes_q(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}

    async def _list(_db: Any, uid: str, page: int, page_size: int, status_filter: str, q: Any = None) -> Any:
        seen["q"] = q
        return [], 0

    monkeypatch.setattr(TaskService, "list_user_tasks_for_admin", _list)
    async with _client(_make_app()) as c:
        r = await c.get(f"/api/v1/admin/users/{_UID_B}/tasks?q=预算")
    assert r.json()["code"] == 0
    assert seen["q"] == "预算"  # 查询词透传到 service 层


async def test_detail_ok(monkeypatch: Any) -> None:
    async def _detail(_db: Any, tid: str) -> Any:
        assert tid == _TID
        return TaskDetailResponse(
            id=_TID,
            title="标题",
            source_type="youtube",
            source_key=None,
            source_url=None,
            audio_url=None,
            status="failed",
            progress=0,
            stage=None,
            duration_seconds=None,
            language=None,
            created_at="2026-06-29T00:00:00Z",
            updated_at="2026-06-29T00:00:00Z",
            error_message="超时",
            stages=[],
            youtube_info=None,
            detected_summary_style=None,
            is_public=False,
            published_at=None,
            asr_provider=None,
            asr_engine=None,
            asr_variant=None,
            llm_provider=None,
        )

    monkeypatch.setattr(TaskService, "get_admin_task_detail", _detail)
    async with _client(_make_app()) as c:
        r = await c.get(f"/api/v1/admin/tasks/{_TID}")
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["audio_url"] is None and body["data"]["error_message"] == "超时"


async def test_transcript_ok(monkeypatch: Any) -> None:
    async def _t(_db: Any, tid: str) -> Any:
        return PublicTranscriptListResponse(task_id=tid, total=0, items=[])

    monkeypatch.setattr(TaskService, "get_admin_task_transcript", _t)
    async with _client(_make_app()) as c:
        r = await c.get(f"/api/v1/admin/tasks/{_TID}/transcript")
    assert r.json()["code"] == 0


async def test_summary_ok(monkeypatch: Any) -> None:
    async def _s(_db: Any, tid: str) -> Any:
        return PublicSummaryListResponse(task_id=tid, total=0, items=[])

    monkeypatch.setattr(TaskService, "get_admin_task_summary", _s)
    async with _client(_make_app()) as c:
        r = await c.get(f"/api/v1/admin/tasks/{_TID}/summary")
    assert r.json()["code"] == 0


async def test_non_admin_forbidden() -> None:
    async with _client(_make_app(admin_ok=False)) as c:
        r = await c.get(f"/api/v1/admin/tasks/{_TID}")
    assert r.json()["code"] == int(ErrorCode.PERMISSION_DENIED)
