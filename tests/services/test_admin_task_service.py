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
