"""Async database engine and session management.

Two connection roles are planned:
  - app engine (this module): used by the API and orchestrator.
  - read-only engine (Stage 5): a separate Postgres role with SELECT-only
    grants, used exclusively for AI-generated queries. Defense in depth --
    the SQL firewall is the first gate, the DB role is the last.
"""

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            pool_size=5,
            max_overflow=5,
            pool_pre_ping=True,
        )
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a database session."""
    get_engine()  # ensure factory exists
    assert _session_factory is not None
    async with _session_factory() as session:
        yield session


async def check_database_connection() -> bool:
    """Cheap connectivity probe used by the health endpoint."""
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
