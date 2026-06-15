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
from app.core.smart_factory import SmartFactory
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
            if "summaries.task_id in (" in sql:
                inside = sql.split("summaries.task_id in (", 1)[1].split(")", 1)[0]
                wanted_set = {self._normalize_uuid(tok.strip().strip("'")) for tok in inside.split(",") if tok.strip()}
                rows = [r for r in self.summaries if self._normalize_uuid(str(r.task_id)) in wanted_set and r.is_active]
            else:
                wanted_raw = sql.split("summaries.task_id =")[1].strip().split()[0].strip("'")
                wanted = self._normalize_uuid(wanted_raw)
                rows = [r for r in self.summaries if self._normalize_uuid(str(r.task_id)) == wanted and r.is_active]
            if "summary_type = 'overview'" in sql:
                rows = [r for r in rows if r.summary_type == "overview"]
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


# ===== OSS 直链测试夹具 =====

_FAKE_OSS_HOST = "https://test-bucket.oss-cn-shenzhen.aliyuncs.com"


class _FakeOssStorage:
    """假 OSS storage,接口签名与 media.py 307 路径同款:generate_presigned_url(object_name, expires_in)。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def generate_presigned_url(self, object_name: str, expires_in: int) -> str:
        self.calls.append((object_name, expires_in))
        return f"{_FAKE_OSS_HOST}/{object_name}?OSSAccessKeyId=test&Expires=9999999999&Signature=fakesig"


@pytest.fixture(autouse=True)
def _storage_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认 OSS 不可用:单测绝不真签名(本机 .env 可能带真实凭据),回落代理 URL 成为确定行为。"""

    async def _raise(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("storage unavailable in unit tests")

    monkeypatch.setattr(SmartFactory, "get_service", _raise)


def _install_fake_oss(monkeypatch: pytest.MonkeyPatch) -> _FakeOssStorage:
    """覆盖 autouse 的不可用默认,让 OSS 预签名走假实现并记录调用。"""
    fake = _FakeOssStorage()

    async def _get_service(*_args: Any, **_kwargs: Any) -> Any:
        return fake

    monkeypatch.setattr(SmartFactory, "get_service", _get_service)
    return fake


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


async def test_list_items_include_cover_and_excerpt(monkeypatch: pytest.MonkeyPatch) -> None:
    """列表项带封面(首张 ready 配图 OSS 直链)+ 摘录(正文剥 markdown)。"""
    fake = _install_fake_oss(monkeypatch)
    session = _FakeSession(tasks=[_make_task()], summaries=[_make_image_summary()])
    async with _client(_make_app(session)) as client:
        body = (await client.get("/public/tasks")).json()
    assert body["code"] == 0
    item = body["data"]["items"][0]
    assert item["cover_url"] and "Signature=" in item["cover_url"]
    assert "img.webp" in item["cover_url"]
    assert item["excerpt"] == "摘要"
    # 封面直链也是 600s 短 TTL
    assert fake.calls and all(expires == 600 for _key, expires in fake.calls)


async def test_list_items_cover_excerpt_none_without_summary() -> None:
    """无 active overview 摘要的任务:cover_url/excerpt 静默回落 None,不 500。"""
    session = _FakeSession(tasks=[_make_task()])
    async with _client(_make_app(session)) as client:
        body = (await client.get("/public/tasks")).json()
    assert body["code"] == 0
    item = body["data"]["items"][0]
    assert item["cover_url"] is None
    assert item["excerpt"] is None


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


# ===== 公开媒体 OSS 直链(绕开隧道) =====

_LEGACY_IMAGE_KEY = f"summary_images/{_OWNER_ID}/{_TASK_ID}/legacy.png"
_READY_IMAGE_PROXY_URL = f"/api/v1/summaries/images/{_OWNER_ID}/{_TASK_ID}/img.webp"


def _make_image_summary() -> Summary:
    """带旧式单图(image_key)+新式多图(ready/pending 各一)的 active 摘要。"""
    summary = Summary(
        task_id=_TASK_ID,
        summary_type="overview",
        version=1,
        content="# 摘要\n{{IMAGE:concept|图1|关键词}}",
    )
    summary.is_active = True
    summary.created_at = datetime.now(UTC)
    summary.image_key = _LEGACY_IMAGE_KEY
    summary.images = [
        {
            "placeholder": "{{IMAGE:concept|图1|关键词}}",
            "status": "ready",
            "url": _READY_IMAGE_PROXY_URL,
            "alt": "图1",
            "model_id": "doubao-seedream-4-5",
            "error": None,
        },
        {
            "placeholder": "{{IMAGE:concept|图2|关键词}}",
            "status": "pending",
            "url": None,
            "alt": "图2",
            "model_id": None,
            "error": None,
        },
    ]
    return summary


async def test_public_summaries_ready_images_use_presigned_direct_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """ready 配图(image_url 与 images[].url 两处)都换成短 TTL OSS 预签名直链。"""
    fake = _install_fake_oss(monkeypatch)
    session = _FakeSession(tasks=[_make_task()], summaries=[_make_image_summary()])
    async with _client(_make_app(session)) as client:
        body = (await client.get(f"/public/tasks/{_TASK_ID}/summaries")).json()
    assert body["code"] == 0
    item = body["data"]["items"][0]
    # 旧式单图 image_key → 直链(不再是 /api/v1/media/ 代理路径,带签名 query)
    assert "/api/v1/media/" not in item["image_url"]
    assert "Signature=" in item["image_url"]
    assert _LEGACY_IMAGE_KEY in item["image_url"]
    # images[].url(ready)→ 直链,对象 key 由存量代理路径还原(summary_images/ 前缀)
    ready = item["images"][0]
    assert "/api/v1/summaries/images/" not in ready["url"]
    assert "Signature=" in ready["url"]
    assert f"summary_images/{_OWNER_ID}/{_TASK_ID}/img.webp" in ready["url"]
    # 直链换发成功时附代理回落路径(前端直链过期 403 后切它走媒体票链路自愈)
    assert ready["proxy_url"] == _READY_IMAGE_PROXY_URL
    # pending 图不签名,url 保持 None
    assert item["images"][1]["url"] is None
    assert item["images"][1]["proxy_url"] is None
    # 全部签名都是 600s 短 TTL(取消公开后残余暴露 ≤TTL)
    assert fake.calls and all(expires == 600 for _key, expires in fake.calls)


async def test_public_summaries_presign_failure_falls_back_to_proxy_urls() -> None:
    """OSS 签发失败时回落现状代理 URL,绝不让整个摘要 500。(autouse 默认 OSS 不可用)"""
    session = _FakeSession(tasks=[_make_task()], summaries=[_make_image_summary()])
    async with _client(_make_app(session)) as client:
        body = (await client.get(f"/public/tasks/{_TASK_ID}/summaries")).json()
    assert body["code"] == 0
    item = body["data"]["items"][0]
    assert item["image_url"] == f"/api/v1/media/{_LEGACY_IMAGE_KEY}"
    assert item["images"][0]["url"] == _READY_IMAGE_PROXY_URL
    # url 本身已是代理回落形态时不再给 proxy_url(避免前端把同一路径再试一遍)
    assert item["images"][0]["proxy_url"] is None


async def test_public_detail_audio_direct_url_presigned(monkeypatch: pytest.MonkeyPatch) -> None:
    """公开详情出 3600s 预签名音频直链;audio_url 代理路径保留不动(前端回落用)。"""
    fake = _install_fake_oss(monkeypatch)
    session = _FakeSession(tasks=[_make_task()])
    async with _client(_make_app(session)) as client:
        body = (await client.get(f"/public/tasks/{_TASK_ID}")).json()
    assert body["code"] == 0
    data = body["data"]
    audio_key = f"upload/{_OWNER_ID}/{_TASK_ID}.mp3"
    assert data["audio_url"] == f"/api/v1/media/{audio_key}"  # 代理路径不动
    assert data["audio_direct_url"] is not None
    assert "/api/v1/media/" not in data["audio_direct_url"]
    assert "Signature=" in data["audio_direct_url"]
    assert audio_key in data["audio_direct_url"]
    assert fake.calls == [(audio_key, 3600)]  # 与媒体端点 307 路径同 TTL


async def test_public_detail_audio_direct_url_none_on_presign_failure() -> None:
    """签发失败时 audio_direct_url=None(不 500),audio_url 代理路径照常。(autouse 默认 OSS 不可用)"""
    session = _FakeSession(tasks=[_make_task()])
    async with _client(_make_app(session)) as client:
        body = (await client.get(f"/public/tasks/{_TASK_ID}")).json()
    assert body["code"] == 0
    data = body["data"]
    assert data["audio_direct_url"] is None
    assert data["audio_url"] == f"/api/v1/media/upload/{_OWNER_ID}/{_TASK_ID}.mp3"


async def test_public_detail_without_audio_has_null_direct_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """无音频(source_key=None)的任务即使 OSS 可用也不签名,audio_direct_url=None。"""
    fake = _install_fake_oss(monkeypatch)
    session = _FakeSession(tasks=[_make_youtube_task()])  # source_key=None
    async with _client(_make_app(session)) as client:
        body = (await client.get(f"/public/tasks/{_TASK_ID}")).json()
    assert body["code"] == 0
    assert body["data"]["audio_direct_url"] is None
    assert body["data"]["audio_url"] is None
    assert fake.calls == []


# ===== 边缘缓存头(Cache-Control,仅业务成功路径) =====

_CACHE_CONTROL = "public, max-age=60, s-maxage=60, stale-while-revalidate=60"
_GET_PATHS = (
    "/public/tasks",
    f"/public/tasks/{_TASK_ID}",
    f"/public/tasks/{_TASK_ID}/transcripts",
    f"/public/tasks/{_TASK_ID}/summaries",
)


async def test_public_get_success_responses_have_cache_control() -> None:
    """4 个公开 GET 的业务成功(code==0)响应必须带边缘缓存头。"""
    session = _FakeSession(
        tasks=[_make_task()],
        transcripts=[_make_transcript(sequence=1, content="第一段")],
        summaries=[_make_image_summary()],
    )
    async with _client(_make_app(session)) as client:
        for path in _GET_PATHS:
            resp = await client.get(path)
            assert resp.json()["code"] == 0, path
            assert resp.headers.get("cache-control") == _CACHE_CONTROL, path


async def test_public_not_found_envelope_has_no_cache_control() -> None:
    """利刃回归:错误信封是 HTTP 200+code=40401,绝不能带缓存头——
    否则刚公开的任务会在边缘 PoP 缓存「不存在」长达 max-age。"""
    session = _FakeSession(tasks=[_make_task(is_public=False)])  # 私有任务 → TASK_NOT_FOUND
    async with _client(_make_app(session)) as client:
        for path in _GET_PATHS[1:]:  # 列表端点无 404 形态
            resp = await client.get(path)
            assert resp.status_code == 200, path
            assert resp.json()["code"] == int(ErrorCode.TASK_NOT_FOUND), path
            assert "cache-control" not in resp.headers, path


async def test_public_rate_limited_envelope_has_no_cache_control(monkeypatch: pytest.MonkeyPatch) -> None:
    """限流错误信封(同样 HTTP 200)不得带缓存头,否则一个 PoP 被打满后所有人吃缓存限流页。"""
    import app.core.rate_limit as rate_limit_module

    class _SaturatedRedis:
        async def incr(self, _key: str) -> int:
            return settings.RATE_LIMIT_PUBLIC_PER_MIN + 1  # 直接越限

        async def expire(self, _key: str, _ttl: int) -> None:
            return None

    monkeypatch.setattr(rate_limit_module, "get_redis_client", lambda: _SaturatedRedis())
    session = _FakeSession(tasks=[_make_task()])
    async with _client(_make_app(session)) as client:
        resp = await client.get("/public/tasks")
    assert resp.status_code == 200
    assert resp.json()["code"] == int(ErrorCode.RATE_LIMIT_EXCEEDED)
    assert "cache-control" not in resp.headers


async def test_media_ticket_mint_has_no_cache_control(_jwt_secret: None) -> None:
    """媒体票 POST(mint)是即时性凭据签发,成功响应也绝不带缓存头。"""
    session = _FakeSession(tasks=[_make_task()])
    async with _client(_make_app(session)) as client:
        resp = await client.post(f"/public/tasks/{_TASK_ID}/media-ticket")
    assert resp.json()["code"] == 0
    assert "cache-control" not in resp.headers
