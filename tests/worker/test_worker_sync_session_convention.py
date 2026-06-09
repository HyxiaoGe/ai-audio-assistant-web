"""守卫回归：worker 任务用的是同步 SQLAlchemy session（worker/db.py: get_sync_db_session，
psycopg2 + sessionmaker），`session.execute()` 返回的是同步 Result（CursorResult/ChunkedIteratorResult），
**不能** 直接 `await`，否则运行时 `TypeError: object ... can't be used in 'await' expression`。

本文件曾因 process_audio.py 里 4 处「裸 await session.execute」导致整条转写流水线在运行期炸掉
（upload/youtube 任务都会失败在 transcribing 阶段）。正确写法是经 `_maybe_await(session.execute(...))`
——它对同步结果直接返回、对协程才 await，兼容同步/异步 session。

这条静态守卫遍历所有 worker 任务，确保不再出现裸 `await session.<会发请求的方法>`。
若将来某个 worker 任务确实改用异步 session，请连同本断言一起更新。
"""

from __future__ import annotations

import re
from pathlib import Path

_WORKER_TASKS_DIR = Path(__file__).resolve().parents[2] / "worker" / "tasks"

# 同步 session 上 await 会炸的方法（都会真正触库/返回同步 Result）
_BARE_AWAIT_SESSION = re.compile(r"await\s+session\.(execute|commit|scalars|scalar|flush|get|delete|refresh|merge)\b")

# 接受 AsyncSession 的服务函数：内部对 session 裸 await，worker 用同步 session 调它同样会炸。
# 裸 await 守卫只扫 worker/tasks 自身，扫不到这类「炸点藏在被调函数里」的耦合，故单列。
# 这正是 process_audio 转写完成后 RAG 入库静默失败的根因（任务仍 completed，chunks 却没入库）。
# worker 必须改调同步孪生 ingest_task_chunks_sync（同步嵌入 + 同步提交）。
_ASYNC_SESSION_FUNCS_IN_WORKER = re.compile(r"\bingest_task_chunks_async\b")


def test_no_bare_await_on_sync_worker_session() -> None:
    offenders: list[str] = []
    for path in sorted(_WORKER_TASKS_DIR.glob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _BARE_AWAIT_SESSION.search(line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")

    assert not offenders, (
        "worker 任务用同步 session，禁止裸 await session.<方法>；请改用 _maybe_await(session.<方法>(...))。\n"
        + "\n".join(offenders)
    )


def test_worker_uses_sync_rag_ingest() -> None:
    offenders: list[str] = []
    for path in sorted(_WORKER_TASKS_DIR.glob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _ASYNC_SESSION_FUNCS_IN_WORKER.search(line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")

    assert not offenders, (
        "worker 用同步 session，RAG 入库必须调 ingest_task_chunks_sync（接受 Session、同步嵌入/提交），"
        "禁止调接受 AsyncSession 的 ingest_task_chunks_async（内部裸 await session 会运行期炸）。\n"
        + "\n".join(offenders)
    )
