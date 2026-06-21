"""建任务时捕获创建者身份(name/avatar)落本地 UserProfile —— 让管理员成本看板不再「未命名用户」。

成本看板按用户展示 ASR¥/配图¥/LLM$,用户名取自本地 UserProfile.display_name。该字段原先唯一写入点
是「发布任务到探索广场」时的 publish-time 快照(_capture_publisher_identity),没发布过的用户恒 NULL
→ 看板回退成「未命名用户」。成本看板的用户集(有 ASR/配图用量者)⊆ 创建过任务的用户,故在「创建任务」
这一持有本人 token 的时刻顺带捕获即可全覆盖。best-effort:已捕获则不重复调 auth-service;无 token /
抓取失败 / DB 异常均不阻断建任务。
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.api.deps import CurrentUser
from app.models.user import UserProfile
from app.schemas.task import TaskCreateRequest, TaskOptions
from app.services import task_service as task_service_module
from app.services.task_service import TaskService

_USER_ID = "f6d3827e-3827-4c4c-8e5e-6880a1c05f22"
_YT_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


class _FakeResult:
    def scalar_one_or_none(self) -> None:
        return None  # 无同内容历史任务 → 不触发去重分支


class _FakeSession:
    """最小异步会话桩:满足 create_task 用到的 execute/add/commit/refresh/get,不做真实落库。"""

    def __init__(self, profile: UserProfile | None) -> None:
        self.profile = profile
        self.added: list[object] = []
        self.commits = 0

    async def execute(self, *args: object, **kwargs: object) -> _FakeResult:
        return _FakeResult()

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, obj: object) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = str(uuid4())

    async def get(self, _model: object, _pk: object) -> object | None:
        return self.profile


def _common_patches(monkeypatch: pytest.MonkeyPatch) -> None:
    # 走管理员路径跳过配额预检;桩掉阶段初始化与 celery 派发(本测试只关心身份捕获)。
    monkeypatch.setattr("app.services.task_service.is_admin_user", lambda _u: True)

    async def _noop_init_stages(_db: object, _task: object) -> None:
        return None

    monkeypatch.setattr(
        "app.services.task_stage_service.TaskStageService.initialize_stages",
        staticmethod(_noop_init_stages),
    )
    monkeypatch.setattr(
        "worker.celery_app.celery_app.send_task",
        lambda name, args=None, kwargs=None: None,
    )


def _patch_identity(
    monkeypatch: pytest.MonkeyPatch,
    value: tuple[str | None, str | None],
    calls: list[str] | None = None,
) -> None:
    async def _fake(token: str) -> tuple[str | None, str | None]:
        if calls is not None:
            calls.append(token)
        return value

    monkeypatch.setattr(task_service_module, "fetch_auth_identity", _fake)


def _user() -> CurrentUser:
    return CurrentUser(id=_USER_ID, email="seanfield767@gmail.com", scopes=["admin"])


def _data() -> TaskCreateRequest:
    return TaskCreateRequest(source_type="youtube", source_url=_YT_URL, options=TaskOptions())


async def test_create_task_captures_creator_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    _common_patches(monkeypatch)
    _patch_identity(monkeypatch, ("sean", "https://avatar/sean"))
    profile = UserProfile(id=UUID(_USER_ID))  # 登录即建(deps._resolve_user),display_name 仍 NULL
    session = _FakeSession(profile=profile)

    task = await TaskService.create_task(session, _user(), _data(), trace_id="t", token="tok")

    assert task.status == "queued"
    assert profile.display_name == "sean"
    assert profile.avatar_url == "https://avatar/sean"


async def test_create_task_skips_capture_when_already_named(monkeypatch: pytest.MonkeyPatch) -> None:
    _common_patches(monkeypatch)
    calls: list[str] = []
    _patch_identity(monkeypatch, ("新名", "https://new"), calls=calls)
    profile = UserProfile(id=UUID(_USER_ID))
    profile.display_name = "老名"
    profile.avatar_url = "https://old"
    session = _FakeSession(profile=profile)

    await TaskService.create_task(session, _user(), _data(), trace_id="t", token="tok")

    assert calls == []  # 已有名字 → 绝不再调 auth-service,也不覆盖
    assert profile.display_name == "老名"
    assert profile.avatar_url == "https://old"


async def test_create_task_without_token_skips_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    _common_patches(monkeypatch)
    calls: list[str] = []
    _patch_identity(monkeypatch, ("sean", "https://x"), calls=calls)
    profile = UserProfile(id=UUID(_USER_ID))
    session = _FakeSession(profile=profile)

    task = await TaskService.create_task(session, _user(), _data(), trace_id="t", token=None)

    assert task.status == "queued"  # 无 token 照常建任务
    assert calls == []
    assert profile.display_name is None


async def test_create_task_succeeds_when_identity_fetch_yields_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _common_patches(monkeypatch)
    _patch_identity(monkeypatch, (None, None))  # userinfo 失败 → fetch 返回 (None, None)
    profile = UserProfile(id=UUID(_USER_ID))
    session = _FakeSession(profile=profile)

    task = await TaskService.create_task(session, _user(), _data(), trace_id="t", token="tok")

    assert task.status == "queued"  # 抓取无结果照常建任务
    assert profile.display_name is None
    assert profile.avatar_url is None
