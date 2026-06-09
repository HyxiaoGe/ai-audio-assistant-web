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
