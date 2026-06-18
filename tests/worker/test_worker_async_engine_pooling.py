"""守卫回归：worker 任务里凡需要真·异步 session 的（prewarm 摘要风格推荐等），
必须走 worker 专属的 NullPool 异步引擎，**不能**复用 app.db 那个池化引擎。

根因：Celery 每个任务用一次性 `asyncio.run`（每次新建并销毁事件循环）。asyncpg/aiosqlite 的连接
绑定在创建它的事件循环上。app.db 的池化引擎（AsyncAdaptedQueuePool）会把连接缓存跨任务复用——
上一任务 `asyncio.run` 关掉循环后，连接仍留在池里；下一任务（新循环）checkout 到它，pre-ping 在
已关闭循环上 ping/terminate 该连接 → `Exception terminating connection`。onboarding 时 prewarm 被
按频道 fan-out 成突发，同一 prefork 子进程背靠背处理 → 必然命中。

NullPool 不缓存连接：每次都开新连接、任务结束即关，从根上消除跨循环复用。
"""

from __future__ import annotations

import asyncio

from sqlalchemy import event, text
from sqlalchemy.pool import NullPool


def test_worker_async_engine_uses_nullpool() -> None:
    from worker.db import worker_async_engine

    assert isinstance(worker_async_engine.pool, NullPool), (
        "worker 异步引擎必须用 NullPool，否则连接会跨 asyncio.run 事件循环被复用 → "
        "Exception terminating connection。"
    )


def test_worker_async_session_opens_fresh_connection_per_event_loop() -> None:
    """两次独立 asyncio.run（模拟两个相邻的 Celery 任务调用）必须各开一个**新**底层连接。

    这是驱动无关的判别器（真正的跨循环崩溃是 asyncpg 特有的，aiosqlite 较宽容复现不出来，
    故不用「第二次会不会报错」来判别）：统计 SQLAlchemy 的 connect 事件——
      - NullPool（worker 正确配置）：每次 checkout 都建新连接 → 两次 = 2 次 connect。
      - 池化引擎（app.db 的错误复用）：连接跨循环被缓存复用 → 两次 = 1 次 connect。
    """
    from worker.db import worker_async_engine, worker_async_session_factory

    connect_count = 0

    @event.listens_for(worker_async_engine.sync_engine, "connect")
    def _on_connect(dbapi_connection: object, connection_record: object) -> None:
        nonlocal connect_count
        connect_count += 1

    async def _use_session() -> int:
        async with worker_async_session_factory() as session:
            result = await session.execute(text("SELECT 1"))
            return result.scalar_one()

    try:
        assert asyncio.run(_use_session()) == 1
        assert asyncio.run(_use_session()) == 1
        assert connect_count == 2, (
            f"worker 异步引擎应每个事件循环开新连接（NullPool），实测 connect 次数={connect_count}；"
            "若为 1 说明连接被跨循环复用（池化引擎），正是 Exception terminating connection 的根因。"
        )
    finally:
        event.remove(worker_async_engine.sync_engine, "connect", _on_connect)
