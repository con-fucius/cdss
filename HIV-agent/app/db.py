"""Async database access for Phase 1 storage."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from .config import get_database_url

_engine = None
_sessionmaker = None


def get_engine():
    """Return a process-wide SQLAlchemy AsyncEngine."""
    global _engine
    if _engine is None:
        from sqlalchemy.ext.asyncio import create_async_engine

        _engine = create_async_engine(
            get_database_url(),
            pool_pre_ping=True,
            future=True,
        )
    return _engine


def get_sessionmaker():
    """Return a configured async_sessionmaker."""
    global _sessionmaker
    if _sessionmaker is None:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


@asynccontextmanager
async def get_session() -> AsyncIterator[object]:
    """Yield one AsyncSession scoped to the current operation."""
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        yield session


async def check_database() -> bool:
    """Return True when the configured database accepts a SELECT 1."""
    from sqlalchemy import text

    async with get_engine().connect() as conn:
        await conn.execute(text("SELECT 1"))
    return True


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
