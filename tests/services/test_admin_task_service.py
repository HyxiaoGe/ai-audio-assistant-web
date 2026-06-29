from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.services.task_service import TaskService

_UID_A = "11111111-1111-1111-1111-111111111111"
_UID_B = "22222222-2222-2222-2222-222222222222"
_TID = "33333333-3333-3333-3333-333333333333"


def datetime_fixed() -> Any:
    import datetime as _dt

    return _dt.datetime(2026, 6, 29, tzinfo=_dt.UTC)


class _Result:
    def __init__(self, *, one: Any = None, rows: Any = None, count: int | None = None) -> None:
        self._one = one
        self._rows = rows or []
        self._count = count

    def scalar_one_or_none(self) -> Any:
        return self._one

    def scalar_one(self) -> int:
        return self._count if self._count is not None else 0

    def scalars(self) -> _Result:
        return self

    def all(self) -> Any:
        return self._rows


class _QueueDB:
    """按预设序列返回 execute 结果;记录是否发生写操作。"""

    def __init__(self, results: list[_Result]) -> None:
        self._results = list(results)
        self.committed = False
        self.added: list[Any] = []

    async def execute(self, _stmt: Any) -> _Result:
        return self._results.pop(0)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.committed = True


async def test_get_admin_task_returns_any_users_task() -> None:
    task = SimpleNamespace(id=_TID, user_id=_UID_B)  # 目标用户 B 的任务
    db = _QueueDB([_Result(one=task)])
    got = await TaskService.get_admin_task(db, _TID)  # type: ignore[arg-type]
    assert got is task


async def test_get_admin_task_missing_raises_task_not_found() -> None:
    db = _QueueDB([_Result(one=None)])
    with pytest.raises(BusinessError) as ei:
        await TaskService.get_admin_task(db, _TID)  # type: ignore[arg-type]
    assert ei.value.code == ErrorCode.TASK_NOT_FOUND


async def test_get_admin_task_bad_uuid_raises_task_not_found() -> None:
    db = _QueueDB([])  # 不应触达 db
    with pytest.raises(BusinessError) as ei:
        await TaskService.get_admin_task(db, "not-a-uuid")  # type: ignore[arg-type]
    assert ei.value.code == ErrorCode.TASK_NOT_FOUND


async def test_list_user_tasks_for_admin_maps_rows(monkeypatch: Any) -> None:
    from app.schemas.public import PublicYouTubeInfo

    yt_row = SimpleNamespace(
        id="a-id",
        title="YT 视频",
        source_type="youtube",
        status="completed",
        progress=100,
        duration_seconds=120,
        created_at=datetime_fixed(),
        error_message=None,
    )
    up_row = SimpleNamespace(
        id="b-id",
        title=None,
        source_type="upload",
        status="failed",
        progress=0,
        duration_seconds=None,
        created_at=datetime_fixed(),
        error_message="转写失败",
    )

    def _fake_yt(task: Any) -> Any:
        return PublicYouTubeInfo(video_id="v1", channel_title="频道名") if task.source_type == "youtube" else None

    monkeypatch.setattr(TaskService, "_build_public_youtube_info", staticmethod(_fake_yt))
    db = _QueueDB([_Result(count=2), _Result(rows=[yt_row, up_row])])

    items, total = await TaskService.list_user_tasks_for_admin(db, _UID_B, 1, 20, "all")  # type: ignore[arg-type]

    assert total == 2
    assert items[0].channel_title == "频道名"  # youtube 行透出频道名
    assert items[1].channel_title is None  # 非 youtube → None(先判空再取属性)
    assert items[1].error_message == "转写失败"  # 失败原因透出
    assert items[0].title == "YT 视频"


async def test_list_user_tasks_for_admin_bad_uuid_returns_empty(monkeypatch: Any) -> None:
    db = _QueueDB([])  # 不应触达 db
    items, total = await TaskService.list_user_tasks_for_admin(db, "not-a-uuid", 1, 20, "all")  # type: ignore[arg-type]
    assert items == [] and total == 0


async def test_get_admin_task_detail_omits_media_keeps_debug(monkeypatch: Any) -> None:
    task = SimpleNamespace(
        id=_TID,
        user_id=_UID_B,
        title="标题",
        source_type="youtube",
        source_key="users/B/x.mp3",
        source_url="https://youtu.be/x",
        status="failed",
        progress=0,
        stage="transcribing",
        duration_seconds=60,
        detected_language="zh",
        created_at=datetime_fixed(),
        updated_at=datetime_fixed(),
        error_message="ASR 超时",
        options=None,
        is_public=False,
        published_at=None,
        asr_provider=None,
        asr_engine=None,
        asr_variant=None,
        llm_provider=None,
        stages=[],
    )

    async def _fake_get(_db: Any, _tid: str) -> Any:
        return task

    monkeypatch.setattr(TaskService, "get_admin_task", _fake_get)

    detail = await TaskService.get_admin_task_detail(object(), _TID)  # type: ignore[arg-type]

    assert detail.audio_url is None  # 不外泄可播音频
    assert detail.source_key is None  # 不外泄存储键
    assert detail.status == "failed"
    assert detail.error_message == "ASR 超时"  # 失败原因可见(排障)
    assert detail.id == _TID


async def test_admin_transcript_is_select_only_no_split_write(monkeypatch: Any) -> None:
    """关键回归:读「单条带时间戳的未切分行」后,DB 绝无写入(不触发 transcripts.py 的惰性切分)。"""

    async def _fake_get(_db: Any, _tid: str) -> Any:
        return SimpleNamespace(id=_TID, user_id=_UID_B)

    monkeypatch.setattr(TaskService, "get_admin_task", _fake_get)
    single_row = SimpleNamespace(
        sequence=1,
        speaker_id="0",
        speaker_label=None,
        content="[00:00.000,00:30.000,0] 一整段未切分的带时间戳转写",
        start_time=0.0,
        end_time=30.0,
    )
    db = _QueueDB([_Result(rows=[single_row])])

    resp = await TaskService.get_admin_task_transcript(db, _TID)  # type: ignore[arg-type]

    assert resp.total == 1
    assert resp.items[0].content == single_row.content  # 原样返回,未被拆分/改写
    assert db.committed is False and db.added == []  # 结构性证明:零写入


async def test_admin_summary_is_text_only(monkeypatch: Any) -> None:
    async def _fake_get(_db: Any, _tid: str) -> Any:
        return SimpleNamespace(id=_TID, user_id=_UID_B)

    monkeypatch.setattr(TaskService, "get_admin_task", _fake_get)
    row = SimpleNamespace(
        summary_type="overview",
        version=2,
        content="# 概览\n正文",
        is_active=True,
        image_key="users/B/img.png",
        images=[{"placeholder": "p", "status": "ready"}],
        created_at=datetime_fixed(),
    )
    db = _QueueDB([_Result(rows=[row])])

    resp = await TaskService.get_admin_task_summary(db, _TID)  # type: ignore[arg-type]

    assert resp.items[0].content == "# 概览\n正文"
    assert resp.items[0].image_url is None and resp.items[0].images is None  # 纯文本,图字段全 None
    assert db.committed is False and db.added == []
