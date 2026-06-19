"""发布任务时捕获发布者身份(name/avatar)落本地 UserProfile。

audio 后端拿不到任意用户的 name/avatar(auth-service 只返回 token 持有者本人),所以在
管理员公开自己任务的那一刻(此时握有其 token)调 /auth/userinfo 捕获并 upsert 到 UserProfile,
供匿名探索端点展示「内容由谁公开」。best-effort:userinfo 失败不阻断公开;取消公开不捕获。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from app.api.deps import CurrentUser
from app.models.task import Task
from app.models.user import UserProfile
from app.services import task_service as task_service_module
from app.services.task_service import TaskService

_ADMIN_ID = "11111111-1111-1111-1111-111111111111"
_TASK_ID = "22222222-2222-2222-2222-222222222222"


def _make_task(*, is_public: bool = False, status: str = "completed") -> Task:
    t = Task(
        id=_TASK_ID,
        user_id=_ADMIN_ID,
        title="任务",
        source_type="upload",
        source_key=f"upload/{_ADMIN_ID}/{_TASK_ID}.mp3",
        status=status,
        progress=100,
        options={},
    )
    t.is_public = is_public
    t.published_at = datetime.now(UTC) if is_public else None
    t.deleted_at = None
    return t


class _FakeResult:
    def __init__(self, one: Any) -> None:
        self._one = one

    def scalar_one_or_none(self) -> Any:
        return self._one


class _FakeSession:
    """只够本单测:execute 永远返回那个 task;get 返回预置 profile;add/commit 记录。"""

    def __init__(self, task: Task, profile: UserProfile | None) -> None:
        self.task = task
        self.profile = profile
        self.added: list[Any] = []
        self.committed = 0

    async def execute(self, _stmt: Any) -> _FakeResult:
        return _FakeResult(self.task)

    async def get(self, _model: Any, _pk: Any) -> Any:
        return self.profile

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.committed += 1


_USER = CurrentUser(id=_ADMIN_ID, email="admin@ex.com", scopes=["admin"])


def _patch_identity(monkeypatch: pytest.MonkeyPatch, value: tuple[str | None, str | None]) -> None:
    async def _fake(_token: str) -> tuple[str | None, str | None]:
        return value

    monkeypatch.setattr(task_service_module, "fetch_auth_identity", _fake)


async def test_publish_captures_publisher_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_identity(monkeypatch, ("张三", "https://lh3.googleusercontent.com/a/abc"))
    profile = UserProfile(id=UUID(_ADMIN_ID))
    session = _FakeSession(task=_make_task(), profile=profile)
    result = await TaskService.update_task_visibility(session, _USER, _TASK_ID, True, token="tok")
    assert result.is_public is True
    assert profile.display_name == "张三"
    assert profile.avatar_url == "https://lh3.googleusercontent.com/a/abc"


async def test_unpublish_does_not_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_identity(monkeypatch, ("张三", "https://lh3.googleusercontent.com/a/abc"))
    profile = UserProfile(id=UUID(_ADMIN_ID))
    session = _FakeSession(task=_make_task(is_public=True), profile=profile)
    await TaskService.update_task_visibility(session, _USER, _TASK_ID, False, token="tok")
    assert profile.display_name is None
    assert profile.avatar_url is None


async def test_publish_without_token_does_not_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    # 没 token 无从调 userinfo —— 不应捕获,也绝不报错。
    _patch_identity(monkeypatch, ("张三", "https://x"))
    profile = UserProfile(id=UUID(_ADMIN_ID))
    session = _FakeSession(task=_make_task(), profile=profile)
    result = await TaskService.update_task_visibility(session, _USER, _TASK_ID, True, token=None)
    assert result.is_public is True
    assert profile.display_name is None


async def test_publish_succeeds_when_identity_fetch_yields_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    # userinfo 失败时 fetch_auth_identity 返回 (None, None) —— 公开照常成功,身份留空。
    _patch_identity(monkeypatch, (None, None))
    profile = UserProfile(id=UUID(_ADMIN_ID))
    session = _FakeSession(task=_make_task(), profile=profile)
    result = await TaskService.update_task_visibility(session, _USER, _TASK_ID, True, token="tok")
    assert result.is_public is True
    assert profile.display_name is None
    assert profile.avatar_url is None
