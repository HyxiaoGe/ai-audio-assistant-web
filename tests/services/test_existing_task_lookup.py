"""「已有转写」感知——annotate_existing_tasks 单元测试。

DB 夹具:在内存 SQLite 里建 tasks 表(raw DDL),通过 AsyncSession 执行真实的
SQLAlchemy SELECT 语句,验证 SQL WHERE 条件(隐私红线)和 Python 后处理逻辑都正确。
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.services.task_service import TaskService
from app.services.youtube.existing_task_lookup import annotate_existing_tasks
from app.services.youtube.search_service import VideoHit

# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


@pytest.fixture
async def db() -> AsyncSession:  # type: ignore[misc]
    """内存 SQLite DB,只建 tasks 表(raw DDL 绕开 PG 专属类型)。

    注意:此处故意用 raw DDL 而非 Base.metadata.create_all。
    Task 模型含 PostgreSQL 专属的 JSONB 列(source_metadata、options 等),
    SQLiteTypeCompiler 无法编译 JSONB,调用 create_all 会直接抛出编译错误。
    raw DDL 把这些列降级为 TEXT,是在 SQLite 上跑真实 SQLAlchemy SELECT 语句的务实做法。
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.execute(
            text("""
                CREATE TABLE tasks (
                    id          TEXT PRIMARY KEY,
                    user_id     TEXT NOT NULL,
                    content_hash TEXT,
                    title       TEXT,
                    source_type TEXT NOT NULL DEFAULT 'youtube',
                    source_url  TEXT,
                    source_key  TEXT,
                    source_metadata TEXT NOT NULL DEFAULT '{}',
                    options     TEXT NOT NULL DEFAULT '{}',
                    status      TEXT NOT NULL DEFAULT 'pending',
                    progress    INTEGER NOT NULL DEFAULT 0,
                    stage       TEXT,
                    duration_seconds INTEGER,
                    detected_language TEXT,
                    error_code  INTEGER,
                    error_message TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    request_id  TEXT,
                    asr_provider TEXT,
                    llm_provider TEXT,
                    asr_engine  TEXT,
                    asr_variant TEXT,
                    is_public   INTEGER NOT NULL DEFAULT 0,
                    published_at TEXT,
                    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
                    deleted_at  TEXT
                )
            """)
        )

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def make_task():
    """返回一个 async 工厂:向已知 session 插入一行 task。返回生成的 task_id。"""

    async def _make(
        session: AsyncSession,
        *,
        user_id: str,
        content_hash: str,
        status: str = "completed",
        is_public: bool = False,
        deleted: bool = False,
    ) -> str:
        from uuid import uuid4

        tid = str(uuid4())
        # SQLite 上 UUID(as_uuid=False) 的 bind_processor 会剥掉横杠(用于 WHERE 比较),
        # 而 result_processor 在读取时又把横杠加回来。
        # 因此此处必须存储去横杠形式,才能让 SQLAlchemy WHERE user_id == viewer_id 在
        # SQLite 上命中;读取后 row.user_id 经 result_processor 恢复带横杠,
        # str(row.user_id) == viewer_id(带横杠) 仍成立,无需额外规范化。
        uid_stored = user_id.replace("-", "").lower()
        await session.execute(
            text(
                "INSERT INTO tasks "
                "(id, user_id, content_hash, status, is_public, deleted_at, source_type) "
                "VALUES (:id, :uid, :ch, :status, :pub, :del, 'youtube')"
            ),
            {
                "id": tid,
                "uid": uid_stored,
                "ch": content_hash,
                "status": status,
                "pub": 1 if is_public else 0,
                "del": "2020-01-01T00:00:00" if deleted else None,
            },
        )
        await session.commit()
        return tid

    return _make


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

# UUID 格式的 viewer_id,与 Task.user_id: UUID(as_uuid=False) 匹配
_U1 = "11111111-1111-1111-1111-111111111111"
_U2 = "22222222-2222-2222-2222-222222222222"
_U3 = "33333333-3333-3333-3333-333333333333"


def _hit(video_id: str) -> VideoHit:
    return VideoHit(video_id=video_id, title=video_id, url=f"https://youtu.be/{video_id}")


def _ch(video_id: str) -> str:
    return TaskService._generate_content_hash(f"youtube:{video_id}")


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


async def test_owner_completed_task_is_annotated_as_owner(db: AsyncSession, make_task) -> None:
    """viewer 自己的任务(任意状态)→ existing_task_id 命中 + is_owner=True。"""
    task_id = await make_task(db, user_id=_U1, content_hash=_ch("vid_own"), status="completed", is_public=False)
    hits = [_hit("vid_own")]
    out = await annotate_existing_tasks(db, hits, viewer_id=_U1)
    assert out[0].existing_task_id == task_id
    assert out[0].existing_is_owner is True


async def test_others_public_completed_is_annotated_not_owner(db: AsyncSession, make_task) -> None:
    """别人公开 completed → 命中但 is_owner=False。"""
    task_id = await make_task(db, user_id=_U2, content_hash=_ch("vid_pub"), status="completed", is_public=True)
    out = await annotate_existing_tasks(db, [_hit("vid_pub")], viewer_id=_U1)
    assert out[0].existing_task_id == task_id
    assert out[0].existing_is_owner is False


async def test_others_private_task_is_not_surfaced(db: AsyncSession, make_task) -> None:
    """隐私红线:别人的私有任务绝不暴露。"""
    await make_task(db, user_id=_U2, content_hash=_ch("vid_priv"), status="completed", is_public=False)
    out = await annotate_existing_tasks(db, [_hit("vid_priv")], viewer_id=_U1)
    assert out[0].existing_task_id is None
    assert out[0].existing_is_owner is False


async def test_anonymous_viewer_sees_only_public(db: AsyncSession, make_task) -> None:
    """匿名 viewer → 只匹配公开 completed, is_owner 恒 False。"""
    await make_task(db, user_id=_U2, content_hash=_ch("vid_pub2"), status="completed", is_public=True)
    out = await annotate_existing_tasks(db, [_hit("vid_pub2")], viewer_id=None)
    assert out[0].existing_task_id is not None
    assert out[0].existing_is_owner is False


async def test_own_wins_over_others_public(db: AsyncSession, make_task) -> None:
    """同一视频:别人公开版 + 自己也转过 → 自己的优先(is_owner=True)。"""
    await make_task(db, user_id=_U2, content_hash=_ch("vid_both"), status="completed", is_public=True)
    await make_task(db, user_id=_U1, content_hash=_ch("vid_both"), status="completed", is_public=False)
    out = await annotate_existing_tasks(db, [_hit("vid_both")], viewer_id=_U1)
    assert out[0].existing_is_owner is True


async def test_no_match_and_empty(db: AsyncSession) -> None:
    """未命中 + 空列表 → 均不报错、正确返回。"""
    out = await annotate_existing_tasks(db, [_hit("nomatch")], viewer_id=_U1)
    assert out[0].existing_task_id is None
    out2 = await annotate_existing_tasks(db, [], viewer_id=_U1)
    assert out2 == []


async def test_anonymous_cannot_see_private_task(db: AsyncSession, make_task) -> None:
    """匿名 viewer 不能看到别人的私有任务(隐私红线)。"""
    await make_task(db, user_id=_U2, content_hash=_ch("vid_priv_anon"), status="completed", is_public=False)
    out = await annotate_existing_tasks(db, [_hit("vid_priv_anon")], viewer_id=None)
    assert out[0].existing_task_id is None
    assert out[0].existing_is_owner is False


async def test_own_pending_task_visible_to_owner(db: AsyncSession, make_task) -> None:
    """viewer 自己的处理中任务(非 completed)也应命中。"""
    await make_task(db, user_id=_U3, content_hash=_ch("vid_wip"), status="transcribing", is_public=False)
    out = await annotate_existing_tasks(db, [_hit("vid_wip")], viewer_id=_U3)
    assert out[0].existing_task_id is not None
    assert out[0].existing_is_owner is True
