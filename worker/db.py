"""
Database session factories for Celery workers.

Worker tasks should use sync database operations (get_sync_db_session) to avoid
asyncio event loop conflicts. The FastAPI app uses the async database (app/db.py).

For the rare worker task that genuinely needs an async session (e.g. it calls an
async-only service such as the LLM summary-style recommender), use
worker_async_session_factory below — it is backed by a NullPool async engine.
A worker must NOT reuse app.db's pooled async engine: Celery runs each task with a
one-shot asyncio.run (a fresh event loop every time), and asyncpg connections are
bound to the loop that created them. A pooled connection left over from a previous
task's now-closed loop, when checked out by the next task, fails on pre-ping with
"Exception terminating connection". NullPool never caches connections across loops.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import settings


def _get_sync_database_url() -> str:
    """
    Convert async database URL to sync URL for worker tasks.

    Replaces postgresql+asyncpg:// with postgresql+psycopg2://
    """
    database_url = settings.DATABASE_URL
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")

    # Convert async driver to sync driver
    if "postgresql+asyncpg://" in database_url:
        database_url = database_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")

    return database_url


# Create sync engine and session factory for worker tasks
sync_engine = create_engine(_get_sync_database_url(), pool_pre_ping=True)
SyncSessionFactory = sessionmaker(bind=sync_engine, expire_on_commit=False)

# Async engine/session for the few worker tasks that need a real AsyncSession.
# NullPool: never cache connections across Celery's per-task asyncio.run event loops.
worker_async_engine: AsyncEngine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
worker_async_session_factory = async_sessionmaker(worker_async_engine, expire_on_commit=False)


@contextmanager
def get_sync_db_session() -> Generator[Session, None, None]:
    """
    Context manager for sync database sessions in worker tasks.

    Usage:
        with get_sync_db_session() as session:
            user = session.query(User).filter_by(id=user_id).first()
    """
    session = SyncSessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
