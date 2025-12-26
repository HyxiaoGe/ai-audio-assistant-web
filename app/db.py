from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings


def _get_database_url() -> str:
    database_url = settings.DATABASE_URL
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")
    return database_url


engine: AsyncEngine = create_async_engine(_get_database_url(), pool_pre_ping=True)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session
