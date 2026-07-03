"""
app/db.py

Async Postgres session management.

Pattern adapted from the chronic-disease CDSS (HIV-agent/app/db.py):
a module-level async engine + sessionmaker, lazily created, exposed via
an async context-managed get_session() so callers never manage connection
lifecycle themselves.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import get_database_url, get_db_pool_max, get_db_pool_min, is_database_configured

logger = logging.getLogger(__name__)

_engine: Optional[AsyncEngine] = None
_sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None
_ready: bool = False


def is_db_ready() -> bool:
    return _ready


def _build_engine() -> AsyncEngine:
    return create_async_engine(
        get_database_url(),
        pool_size=get_db_pool_min(),
        max_overflow=max(0, get_db_pool_max() - get_db_pool_min()),
        pool_pre_ping=True,
        future=True,
    )


async def init_engine() -> None:
    """Call once from the FastAPI lifespan at startup."""
    global _engine, _sessionmaker, _ready
    if not is_database_configured():
        logger.warning(
            "DATABASE_URL not configured — starting in degraded mode. "
            "Database-dependent endpoints will return errors."
        )
        return
    _engine = _build_engine()
    _sessionmaker = async_sessionmaker(
        _engine, class_=AsyncSession, expire_on_commit=False
    )
    _ready = True
    logger.info("Database engine initialised")


async def close_engine() -> None:
    global _engine, _ready
    if _engine is not None:
        await _engine.dispose()
    _ready = False
    logger.info("Database engine disposed")


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError(
            "Database not initialised. Call init_engine() from the app lifespan "
            "before any request that touches the database."
        )
    async with _sessionmaker() as session:
        yield session


async def check_database() -> bool:
    """Lightweight connectivity probe used by /health."""
    if _sessionmaker is None:
        return False
    try:
        from sqlalchemy import text

        async with get_session() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning("Database health check failed: %s", exc)
        return False
