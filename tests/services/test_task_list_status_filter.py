"""list_tasks 的 "processing" 伞形筛选必须覆盖 polishing 状态（A 修复）。

后端 worker 在 transcribing↔summarizing 之间会发出 `polishing` 任务状态
（process_audio.py / process_youtube.py），但 TaskService.list_tasks 的
status_filter == "processing" 伞形 IN 列表当初漏了 `polishing`，而路由层
list_tasks 端点的 allowed_status 白名单也没有它——于是处于润色阶段的任务
在"处理中"筛选里彻底隐身（既不被伞形匹配，?status=polishing 又被静默转成 all）。

本测试用一个只记录 statement、不连真实 DB 的假 session 驱动 list_tasks，
把生成的 count 查询按 postgres 方言编译成 SQL，断言其 WHERE 覆盖了 'polishing'。
（仓库的 Task 模型是 postgres 专用类型 JSONB/UUID，没有真实 DB 测试夹具，
故走"捕获 + 编译断言"而非起 sqlite。）
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.dialects import postgresql

from app.api.deps import CurrentUser
from app.services.task_service import TaskService


class _FakeResult:
    def __init__(self, scalar: int = 0, rows: list[Any] | None = None) -> None:
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one(self) -> int:
        return self._scalar

    def scalars(self) -> _FakeResult:
        return self

    def all(self) -> list[Any]:
        return self._rows


class _CapturingSession:
    """记录被 execute 的 statement；第一条是 count，第二条是 items。"""

    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, stmt: Any) -> _FakeResult:
        self.statements.append(stmt)
        if len(self.statements) == 1:
            return _FakeResult(scalar=0)  # count 查询
        return _FakeResult(rows=[])  # items 查询


def _compiled_sql(stmt: Any) -> str:
    return str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()


async def test_processing_filter_includes_polishing() -> None:
    db = _CapturingSession()
    user = CurrentUser(id="00000000-0000-0000-0000-000000000001", email="t@example.com")

    await TaskService.list_tasks(db, user, page=1, page_size=20, status_filter="processing")

    sql = _compiled_sql(db.statements[0])
    assert "polishing" in sql, (
        "list_tasks 的 'processing' 伞形筛选缺少 'polishing'——润色阶段任务会从「处理中」筛选里消失"
    )


def test_processing_statuses_constant_includes_pipeline_stages() -> None:
    from app.services.task_service import PROCESSING_STATUSES

    # 流水线中间态都必须在单一事实源里，否则 list_tasks 的伞形筛选会漏
    for stage in ("extracting", "transcribing", "polishing", "summarizing"):
        assert stage in PROCESSING_STATUSES


def test_list_status_filter_whitelist_accepts_polishing() -> None:
    # 路由白名单从 PROCESSING_STATUSES 派生：?status=polishing 必须被接受为精确筛选，
    # 而不是静默回退成 "all"（曾经的 bug）
    from app.api.v1.tasks import _LIST_STATUS_FILTERS
    from app.services.task_service import PROCESSING_STATUSES

    assert "polishing" in _LIST_STATUS_FILTERS
    assert set(PROCESSING_STATUSES) <= _LIST_STATUS_FILTERS
    assert {"all", "processing", "completed", "failed"} <= _LIST_STATUS_FILTERS


class _RowsSession:
    """get_status_counts 只 execute 一次 GROUP BY；用预置 rows 驱动分桶断言。"""

    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.statements: list[Any] = []

    async def execute(self, stmt: Any) -> _FakeResult:
        self.statements.append(stmt)
        return _FakeResult(rows=self.rows)


async def test_status_counts_buckets_processing_umbrella() -> None:
    # processing 角标必须与 list_tasks 的伞形筛选一致：queued/transcribing/polishing
    # 都算"处理中"；completed/failed 精确；all 为总和。
    rows = [
        ("completed", 3),
        ("failed", 2),
        ("transcribing", 1),
        ("polishing", 1),
        ("queued", 4),
    ]
    db = _RowsSession(rows)
    user = CurrentUser(id="00000000-0000-0000-0000-000000000001", email="t@example.com")

    counts = await TaskService.get_status_counts(db, user)

    assert counts == {"all": 11, "processing": 6, "completed": 3, "failed": 2}


async def test_status_counts_query_groups_by_status_scoped_to_user() -> None:
    # 单次 GROUP BY、限定本人且排除软删除——替代四个 tab 各发一次 page_size=1 查询。
    db = _RowsSession([])
    user = CurrentUser(id="00000000-0000-0000-0000-000000000001", email="t@example.com")

    await TaskService.get_status_counts(db, user)

    sql = _compiled_sql(db.statements[0])
    assert "group by" in sql and "status" in sql
    assert "count(" in sql
    assert "deleted_at is null" in sql
