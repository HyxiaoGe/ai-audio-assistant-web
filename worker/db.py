"""
Synchronous database session factory for Celery workers.

Worker tasks use sync database operations to avoid asyncio event loop conflicts.
The FastAPI app continues to use async database (app/db.py).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

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
