"""公开探索端点契约测试(裸 app + 假 session,参照 tests/test_notifications_api.py 打法)。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport
from sqlalchemy.dialects import postgresql

from app.api.deps import get_db
from app.api.v1 import public as public_module
from app.config import settings
from app.core.exceptions import BusinessError
from app.core.response import error
from app.core.security import verify_scoped_token
from app.i18n.codes import ErrorCode
from app.models.summary import Summary
from app.models.task import Task
from app.models.transcript import Transcript

_OWNER_ID = "11111111-1111-1111-1111-111111111111"
_TASK_ID = "22222222-2222-2222-2222-222222222222"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _make_task(
    *,
    id: str = _TASK_ID,
    user_id: str = _OWNER_ID,
    is_public: bool = True,
    status: str = "completed",
    published_offset: int = 0,
    deleted: bool = False,
    options: dict[str, Any] | None = None,
) -> Task:
    t = Task(
        id=id,
        user_id=user_id,
        title=f"任务{id[:4]}",
        source_type="upload",
        source_key=f"upload/{user_id}/{id}.mp3",
        status=status,
        progress=100,
        duration_seconds=60,
        options=options or {},
    )
    t.is_public = is_public
    t.published_at = datetime.now(UTC) + timedelta(seconds=published_offset) if is_public else None
    t.detected_language = "zh"
    t.deleted_at = datetime.now(UTC) if deleted else None
    t.created_at = datetime.now(UTC)
    t.updated_at = datetime.now(UTC)
    return t


def _csql(stmt: Any) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).lower()


class _FakeResult:
    def __init__(self, *, scalar: int | None = None, rows: list[Any] | None = None, one: Any = None) -> None:
        self._scalar = scalar
        self._rows = rows or []
        self._one = one

    def scalar_one(self) -> int:
        assert self._scalar is not None
        return self._scalar

    def scalar_one_or_none(self) -> Any:
        return self._one

    def scalars(self) -> _FakeResult:
        return self

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    """内存 tasks/transcripts/summaries + 按编译 SQL 分支的假 session。"""

    def __init__(
        self,
        tasks: list[Task] | None = None,
        transcripts: list[Transcript] | None = None,
        summaries: list[Summary] | None = None,
    ) -> None:
        self.tasks = tasks or []
        self.transcripts = transcripts or []
        self.summaries = summaries or []
        self.committed = 0

    async def commit(self) -> None:
        self.committed += 1

    @staticmethod
    def _normalize_uuid(raw: str) -> str:
        """去连字符后的 32 位十六进制串与含连字符版本双向兼容比较。"""
        return raw.replace("-", "").lower()

    def _filter_tasks(self, sql: str) -> list[Task]:
        rows = list(self.tasks)
        if "is_public is true" in sql:
            rows = [t for t in rows if t.is_public]
        if "status = 'completed'" in sql:
            rows = [t for t in rows if t.status == "completed"]
        if "deleted_at is null" in sql:
            rows = [t for t in rows if t.deleted_at is None]
        if "tasks.id =" in sql:
            # PostgreSQL 方言把 UUID 编译为无连字符的 32 位串
            wanted_raw = sql.split("tasks.id =")[1].strip().split()[0].strip("'")
            wanted = self._normalize_uuid(wanted_raw)
            rows = [t for t in rows if self._normalize_uuid(str(t.id)) == wanted]
        if "tasks.user_id =" in sql:
            wanted_raw = sql.split("tasks.user_id =")[1].strip().split()[0].strip("'")
            wanted = self._normalize_uuid(wanted_raw)
            rows = [t for t in rows if self._normalize_uuid(str(t.user_id)) == wanted]
        return rows

    async def execute(self, stmt: Any) -> _FakeResult:
        sql = _csql(stmt)
        first_col = stmt.column_descriptions[0].get("name") if stmt.column_descriptions else None
        if first_col == "count" or sql.startswith("select count"):
            return _FakeResult(scalar=len(self._filter_tasks(sql)))
        if "from transcripts" in sql:
            wanted_raw = sql.split("transcripts.task_id =")[1].strip().split()[0].strip("'")
            wanted = self._normalize_uuid(wanted_raw)
            rows = sorted(
                (r for r in self.transcripts if self._normalize_uuid(str(r.task_id)) == wanted),
                key=lambda r: r.sequence,
            )
            return _FakeResult(rows=list(rows))
        if "from summaries" in sql:
            wanted_raw = sql.split("summaries.task_id =")[1].strip().split()[0].strip("'")
            wanted = self._normalize_uuid(wanted_raw)
            rows = [r for r in self.summaries if self._normalize_uuid(str(r.task_id)) == wanted and r.is_active]
            return _FakeResult(rows=rows)
        rows = self._filter_tasks(sql)
        rows.sort(key=lambda t: t.published_at or _EPOCH, reverse=True)
        return _FakeResult(rows=rows, one=rows[0] if rows else None)


def _make_app(session: _FakeSession) -> FastAPI:
    app = FastAPI()
    app.include_router(public_module.router)

    @app.exception_handler(BusinessError)
    async def _handle(_req: Request, exc: BusinessError) -> Any:
        return error(int(exc.code), exc.code.name)

    async def _db() -> AsyncIterator[_FakeSession]:
        yield session

    app.dependency_overrides[get_db] = _db
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ===== 列表 =====


async def test_list_returns_only_public_completed() -> None:
    session = _FakeSession(
        tasks=[
            _make_task(id=_TASK_ID, published_offset=10),
            _make_task(id="33333333-3333-3333-3333-333333333333", is_public=False),
            _make_task(id="44444444-4444-4444-4444-444444444444", status="summarizing"),
            _make_task(id="55555555-5555-5555-5555-555555555555", deleted=True),
        ]
    )
    async with _client(_make_app(session)) as client:
        body = (await client.get("/public/tasks")).json()
    assert body["code"] == 0
    data = body["data"]
    assert data["total"] == 1
    assert [item["id"] for item in data["items"]] == [_TASK_ID]
    item = data["items"][0]
    # 裁剪面:公开列表项不带任何内部字段
    for forbidden in ("status", "progress", "error_message", "source_key", "user_id"):
        assert forbidden not in item


async def test_list_rejects_oversize_page_size() -> None:
    async with _client(_make_app(_FakeSession())) as client:
        resp = await client.get("/public/tasks", params={"page_size": 500})
    assert resp.status_code == 422  # Query(le=50) 校验


async def test_detail_public_ok_and_trimmed() -> None:
    session = _FakeSession(
        tasks=[_make_task(options={"summary_style_auto_detected": True, "summary_style": "lecture"})]
    )
    async with _client(_make_app(session)) as client:
        body = (await client.get(f"/public/tasks/{_TASK_ID}")).json()
    assert body["code"] == 0
    data = body["data"]
    assert data["id"] == _TASK_ID
    assert data["audio_url"].endswith(f"upload/{_OWNER_ID}/{_TASK_ID}.mp3")
    assert data["detected_summary_style"] == "lecture"
    for forbidden in ("error_message", "stages", "options", "source_metadata", "source_key", "status"):
        assert forbidden not in data


async def test_detail_private_task_is_not_found() -> None:
    session = _FakeSession(tasks=[_make_task(is_public=False)])
    async with _client(_make_app(session)) as client:
        body = (await client.get(f"/public/tasks/{_TASK_ID}")).json()
    assert body["code"] == int(ErrorCode.TASK_NOT_FOUND)


async def test_detail_invalid_uuid_is_not_found_not_500() -> None:
    async with _client(_make_app(_FakeSession())) as client:
        body = (await client.get("/public/tasks/not-a-uuid")).json()
    assert body["code"] == int(ErrorCode.TASK_NOT_FOUND)


# ===== 转写 / 摘要 / 媒体票 =====

_TEST_SECRET = "unit-test-secret-not-production"


@pytest.fixture
def _jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "JWT_SECRET", _TEST_SECRET)


def _make_transcript(*, sequence: int, content: str) -> Transcript:
    tr = Transcript(
        task_id=_TASK_ID,
        sequence=sequence,
        content=content,
        start_time=sequence * 1.0,
        end_time=sequence * 1.0 + 0.9,
        speaker_id="S1",
        speaker_label="讲话人 1",
    )
    tr.words = [{"w": "内部字段"}]
    tr.is_edited = False
    tr.original_content = "润色前原文"
    return tr


async def test_transcripts_trimmed_and_readonly() -> None:
    session = _FakeSession(
        tasks=[_make_task()],
        transcripts=[_make_transcript(sequence=2, content="第二段"), _make_transcript(sequence=1, content="第一段")],
    )
    async with _client(_make_app(session)) as client:
        body = (await client.get(f"/public/tasks/{_TASK_ID}/transcripts")).json()
    assert body["code"] == 0
    items = body["data"]["items"]
    assert [i["content"] for i in items] == ["第一段", "第二段"]  # 按 sequence 排序
    for forbidden in ("words", "confidence", "is_edited", "original_content"):
        assert forbidden not in items[0]
    assert session.committed == 0  # 纯只读:绝无懒拆分等写副作用


async def test_transcripts_of_private_task_not_found() -> None:
    session = _FakeSession(tasks=[_make_task(is_public=False)], transcripts=[_make_transcript(sequence=1, content="x")])
    async with _client(_make_app(session)) as client:
        body = (await client.get(f"/public/tasks/{_TASK_ID}/transcripts")).json()
    assert body["code"] == int(ErrorCode.TASK_NOT_FOUND)


async def test_summaries_trimmed() -> None:
    summary = Summary(
        task_id=_TASK_ID,
        summary_type="overview",
        version=1,
        content="# 摘要\n{{IMAGE:concept|图1|关键词}}",
    )
    summary.is_active = True
    summary.created_at = datetime.now(UTC)
    summary.images = [
        {
            "placeholder": "{{IMAGE:concept|图1|关键词}}",
            "status": "ready",
            "url": f"/api/v1/summaries/images/{_OWNER_ID}/{_TASK_ID}/img.webp",
            "alt": "图1",
            "model_id": "doubao-seedream-4-5",
            "error": None,
        }
    ]
    session = _FakeSession(tasks=[_make_task()], summaries=[summary])
    async with _client(_make_app(session)) as client:
        body = (await client.get(f"/public/tasks/{_TASK_ID}/summaries")).json()
    assert body["code"] == 0
    items = body["data"]["items"]
    assert items[0]["summary_type"] == "overview"
    image = items[0]["images"][0]
    assert image["status"] == "ready" and image["url"].endswith("img.webp")
    for forbidden in ("model_id", "error"):
        assert forbidden not in image
    for forbidden in ("model_used", "token_count", "prompt_version", "visual_content"):
        assert forbidden not in items[0]


async def test_media_ticket_pins_public_task(_jwt_secret: None) -> None:
    session = _FakeSession(tasks=[_make_task()])
    async with _client(_make_app(session)) as client:
        body = (await client.post(f"/public/tasks/{_TASK_ID}/media-ticket")).json()
    assert body["code"] == 0
    claims = verify_scoped_token(body["data"]["token"])
    assert claims["scope"] == "media"
    assert claims["sub"] == _OWNER_ID  # 票主体=任务 owner(媒体 key 第二段)
    assert claims["resource"] == {"public_task": _TASK_ID}  # 钉死任务,绝不裸放整个命名空间
    assert body["data"]["expires_in"] == settings.MEDIA_TOKEN_TTL


async def test_media_ticket_rejected_for_private_task(_jwt_secret: None) -> None:
    session = _FakeSession(tasks=[_make_task(is_public=False)])
    async with _client(_make_app(session)) as client:
        body = (await client.post(f"/public/tasks/{_TASK_ID}/media-ticket")).json()
    assert body["code"] == int(ErrorCode.TASK_NOT_FOUND)


# ===== youtube_info =====

_YOUTUBE_VIDEO_ID = "dQw4w9WgXcQ"
_YOUTUBE_URL = f"https://www.youtube.com/watch?v={_YOUTUBE_VIDEO_ID}"


def _make_youtube_task(
    *,
    title: str = "Never Gonna Give You Up",
    duration_seconds: int | None = 212,
) -> Task:
    """返回一个公开 youtube 来源的任务，source_url 含合法视频 ID。"""
    t = Task(
        id=_TASK_ID,
        user_id=_OWNER_ID,
        title=title,
        source_type="youtube",
        source_url=_YOUTUBE_URL,
        source_key=None,
        status="completed",
        progress=100,
        duration_seconds=duration_seconds,
        options={},
    )
    t.is_public = True
    t.published_at = datetime.now(UTC)
    t.detected_language = "en"
    t.deleted_at = None
    t.created_at = datetime.now(UTC)
    t.updated_at = datetime.now(UTC)
    return t


async def test_youtube_task_detail_contains_youtube_info() -> None:
    """公开 youtube 任务详情必须含 youtube_info，且子字段值正确。"""
    session = _FakeSession(tasks=[_make_youtube_task()])
    async with _client(_make_app(session)) as client:
        body = (await client.get(f"/public/tasks/{_TASK_ID}")).json()
    assert body["code"] == 0
    data = body["data"]
    yi = data.get("youtube_info")
    assert yi is not None, "youtube_info 应在公开详情中出现"
    assert yi["video_id"] == _YOUTUBE_VIDEO_ID
    assert yi["title"] == "Never Gonna Give You Up"
    # 缩略图 URL 由 video_id 推算（YouTube 标准格式）
    assert yi["thumbnail_url"] == f"https://i.ytimg.com/vi/{_YOUTUBE_VIDEO_ID}/hqdefault.jpg"
    assert yi["duration_seconds"] == 212
    # source_url 透出，供前端嵌入播放器
    assert data.get("source_url") == _YOUTUBE_URL


async def test_non_youtube_task_detail_has_null_youtube_info() -> None:
    """非 youtube 来源的任务，youtube_info 必须为 null（不存在或显式 null）。"""
    session = _FakeSession(tasks=[_make_task()])  # source_type="upload"
    async with _client(_make_app(session)) as client:
        body = (await client.get(f"/public/tasks/{_TASK_ID}")).json()
    assert body["code"] == 0
    data = body["data"]
    # youtube_info 不存在 或 显式为 null
    assert data.get("youtube_info") is None


async def test_youtube_info_excludes_private_fields() -> None:
    """公开 youtube_info 不得含任何私有字段（owner 标识、内部存储 key、账号凭据等）。"""
    session = _FakeSession(tasks=[_make_youtube_task()])
    async with _client(_make_app(session)) as client:
        body = (await client.get(f"/public/tasks/{_TASK_ID}")).json()
    assert body["code"] == 0
    data = body["data"]
    yi = data.get("youtube_info") or {}
    # 不允许出现的私有字段
    for forbidden in ("user_id", "access_token", "refresh_token", "subscription_id", "last_synced_at"):
        assert forbidden not in yi, f"youtube_info 不应含私有字段: {forbidden}"
    # 整体详情不允许出现的私有字段（已有断言，此处加固 youtube 任务路径）
    for forbidden in ("error_message", "stages", "options", "source_metadata", "source_key", "status", "user_id"):
        assert forbidden not in data, f"公开详情不应含私有字段: {forbidden}"


async def test_youtube_task_without_valid_video_id_has_null_youtube_info() -> None:
    """source_url 无法提取 video_id 时，youtube_info 为 null（不崩溃）。"""
    t = Task(
        id=_TASK_ID,
        user_id=_OWNER_ID,
        title="坏链任务",
        source_type="youtube",
        source_url="https://www.youtube.com/channel/UC1234",  # 频道页，无 video_id
        source_key=None,
        status="completed",
        progress=100,
        duration_seconds=None,
        options={},
    )
    t.is_public = True
    t.published_at = datetime.now(UTC)
    t.detected_language = "zh"
    t.deleted_at = None
    t.created_at = datetime.now(UTC)
    t.updated_at = datetime.now(UTC)
    session = _FakeSession(tasks=[t])
    async with _client(_make_app(session)) as client:
        body = (await client.get(f"/public/tasks/{_TASK_ID}")).json()
    assert body["code"] == 0
    assert body["data"].get("youtube_info") is None
