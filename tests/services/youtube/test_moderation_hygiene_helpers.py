from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.flagged_channel import FlaggedChannel
from app.models.youtube_search import YouTubeSearchQuery
from app.services.youtube import channel_flag_service as cfs
from app.services.youtube import search_cache


async def _session_factory():
    """内存 sqlite + StaticPool 让多会话共享同库。

    用 raw text DDL 建表以绕开 Postgres 专属类型(JSONB/UUID)在 sqlite DDL
    编译器中不可渲染的问题;DML 仍走 ORM 模型(SQLAlchemy 会对 JSONB 列做
    Python 侧 JSON 序列化/反序列化,与底层存储类型无关)。
    """
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS youtube_search_queries ("
                "  id TEXT PRIMARY KEY,"
                "  created_at DATETIME,"
                "  updated_at DATETIME,"
                "  normalized_query TEXT NOT NULL,"
                "  display_query TEXT NOT NULL,"
                "  results_json TEXT NOT NULL DEFAULT '[]',"
                "  fetched_at DATETIME,"
                "  search_count INTEGER NOT NULL DEFAULT 0,"
                "  last_searched_at DATETIME,"
                "  is_blocked INTEGER NOT NULL DEFAULT 0"
                ")"
            )
        )
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS flagged_channels ("
                "  id TEXT PRIMARY KEY,"
                "  created_at DATETIME,"
                "  updated_at DATETIME,"
                "  match_field TEXT NOT NULL,"
                "  match_value TEXT NOT NULL,"
                "  channel_id TEXT,"
                "  channel_handle TEXT,"
                "  channel_name TEXT,"
                "  block_count INTEGER NOT NULL DEFAULT 0,"
                "  last_video_id TEXT,"
                "  last_title TEXT,"
                "  last_flagged_at DATETIME NOT NULL,"
                "  status TEXT NOT NULL DEFAULT 'pending',"
                "  resolved_by TEXT,"
                "  resolved_at DATETIME"
                ")"
            )
        )
    return async_sessionmaker(engine, expire_on_commit=False)


def test_purge_deletes_out_of_trending_window_and_stale_sensitive() -> None:
    async def _run():
        now = datetime.now(UTC)
        old = now - timedelta(days=100)  # 远超 7d 窗口
        fresh = now - timedelta(minutes=1)  # 窗口内、缓存内
        stale6h = now - timedelta(hours=12)  # 超 6h 缓存 TTL
        Session = await _session_factory()
        async with Session() as s:
            await s.execute(
                insert(YouTubeSearchQuery),
                [
                    # 1) 出窗口 → 删
                    dict(
                        normalized_query="old",
                        display_query="old",
                        results_json=[],
                        fetched_at=old,
                        last_searched_at=old,
                        is_blocked=False,
                    ),
                    # 2) 窗口内普通 → 留
                    dict(
                        normalized_query="fresh",
                        display_query="fresh",
                        results_json=[],
                        fetched_at=fresh,
                        last_searched_at=fresh,
                        is_blocked=False,
                    ),
                    # 3) 敏感且缓存过期(最近还搜过)→ 删(子句2)
                    dict(
                        normalized_query="sens_stale",
                        display_query="sens_stale",
                        results_json=[],
                        fetched_at=stale6h,
                        last_searched_at=fresh,
                        is_blocked=True,
                    ),
                    # 4) 敏感但缓存新鲜 → 留
                    dict(
                        normalized_query="sens_fresh",
                        display_query="sens_fresh",
                        results_json=[],
                        fetched_at=fresh,
                        last_searched_at=fresh,
                        is_blocked=True,
                    ),
                ],
            )
            await s.commit()
            deleted = await search_cache.purge_stale_queries(s)
            rows = (await s.execute(select(YouTubeSearchQuery.normalized_query))).scalars().all()
        assert deleted == 2
        assert set(rows) == {"fresh", "sens_fresh"}

    asyncio.run(_run())


def test_scrub_nulls_resolved_titles_only() -> None:
    async def _run():
        now = datetime.now(UTC)
        Session = await _session_factory()
        async with Session() as s:
            await s.execute(
                insert(FlaggedChannel),
                [
                    dict(
                        match_field="channel_id",
                        match_value="UCp",
                        channel_id="UCp",
                        block_count=1,
                        last_title="pending 标题",
                        last_flagged_at=now,
                        status="pending",
                    ),
                    dict(
                        match_field="channel_id",
                        match_value="UCb",
                        channel_id="UCb",
                        block_count=1,
                        last_title="blocked 政治标题",
                        last_flagged_at=now,
                        status="blocked",
                    ),
                    dict(
                        match_field="channel_id",
                        match_value="UCd",
                        channel_id="UCd",
                        block_count=1,
                        last_title="dismissed 标题",
                        last_flagged_at=now,
                        status="dismissed",
                    ),
                ],
            )
            await s.commit()
            affected = await cfs.scrub_resolved_titles(s)
            import sqlalchemy as sa

            titem = dict((await s.execute(sa.select(FlaggedChannel.match_value, FlaggedChannel.last_title))).all())
        assert affected == 2
        assert titem["UCp"] == "pending 标题"  # pending 不动
        assert titem["UCb"] is None  # blocked 置空
        assert titem["UCd"] is None  # dismissed 置空

    asyncio.run(_run())
