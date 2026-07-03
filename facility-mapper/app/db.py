"""
facility-mapper/app/db.py

Async SQLAlchemy engine and session management.

Follows the same pattern as ambulance-cdss/app/db.py:
- init_engine() creates the async engine and session factory
- get_session() provides a context-managed session
- check_database() pings the connection pool
- close_engine() shuts down cleanly
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import get_database_url, get_db_pool_max, get_db_pool_min

logger = logging.getLogger(__name__)

_engine = None
_session_factory = None


async def init_engine() -> None:
    """Create the async engine and session factory. Call at startup."""
    global _engine, _session_factory
    url = get_database_url()
    _engine = create_async_engine(
        url,
        pool_size=get_db_pool_min(),
        max_overflow=max(0, get_db_pool_max() - get_db_pool_min()),
        pool_pre_ping=True,
        echo=False,
    )
    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    logger.info("Facility Mapper database engine initialized.")


async def close_engine() -> None:
    """Dispose of the async engine. Call at shutdown."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        logger.info("Facility Mapper database engine disposed.")
    _engine = None


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional session scope."""
    if _session_factory is None:
        raise RuntimeError("Database engine not initialized. Call init_engine() first.")
    async with _session_factory() as session:
        yield session


async def check_database() -> bool:
    """Ping the database pool to verify connectivity."""
    if _engine is None:
        return False
    try:
        async with _engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning("Database health check failed: %s", exc)
        return False
