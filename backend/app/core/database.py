"""Async SQLAlchemy 2.x engine and session management."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings

# --------------------------------------------------------------------------
# Async engine (used by the API and the workers)
# --------------------------------------------------------------------------
_engine_kwargs: dict[str, object] = {"future": True, "pool_pre_ping": True}
if settings.resolved_database_url.startswith("sqlite"):
    # ``check_same_thread`` is irrelevant for aiosqlite but the timeout helps
    # when a worker and the API hit the same file database.
    _engine_kwargs["connect_args"] = {"timeout": 30}
    # NullPool keeps every connection fresh: it avoids cross-event-loop
    # surprises (tests, uvicorn reload workers) and is cheap for SQLite.
    _engine_kwargs["poolclass"] = NullPool

async_engine: AsyncEngine = create_async_engine(
    # Echoing every statement is a *diagnostic* switch of its own: tying it to
    # ``debug`` filled the log with SQL for a normal debug-enabled deployment.
    settings.resolved_database_url,
    echo=settings.sql_echo,
    **_engine_kwargs,
)

async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=async_engine,
    expire_on_commit=False,
    autoflush=False,
)


#: ``journal_mode`` is stored *in the database file*, so it only has to be set
#: once per process; re-issuing it on every connection (NullPool opens one per
#: session) costs a disk round-trip each time.
_wal_ensured = False


def _enable_sqlite_pragmas(dbapi_connection, _record) -> None:  # noqa: ANN001
    """Enable WAL (once) + foreign keys and sane timeouts for SQLite connections."""
    global _wal_ensured
    cursor = dbapi_connection.cursor()
    try:
        if not _wal_ensured:
            cursor.execute("PRAGMA journal_mode=WAL")
            _wal_ensured = True
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
    finally:
        cursor.close()


if settings.resolved_database_url.startswith("sqlite"):  # pragma: no branch
    event.listen(async_engine.sync_engine, "connect", _enable_sqlite_pragmas)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional scope for background workers and scripts."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


# --------------------------------------------------------------------------
# background write serialisation
# --------------------------------------------------------------------------
# SQLite admits a single writer at a time. The camera manager has three async
# writers (detection results, heatmap flush, health metrics) and letting them
# collide makes one of them wait for ``busy_timeout`` (or fail outright with
# SQLITE_BUSY_SNAPSHOT). One lock keeps the app's own writers polite.
_write_lock: asyncio.Lock | None = None


def db_write_lock() -> asyncio.Lock:
    """The process-wide lock guarding background database writes."""
    global _write_lock
    if _write_lock is None:
        _write_lock = asyncio.Lock()
    return _write_lock


# --------------------------------------------------------------------------
# Sync engine (Alembic migrations only)
# --------------------------------------------------------------------------
def _sync_url() -> str:
    url = settings.resolved_database_url
    return (
        url.replace("+aiosqlite", "")
        .replace("+asyncpg", "+psycopg")
        .replace("+aiomysql", "+pymysql")
    )


def create_sync_engine() -> Engine:
    return create_engine(_sync_url(), future=True, pool_pre_ping=True)


sync_session_factory: sessionmaker[Session] = sessionmaker(bind=create_sync_engine(), future=True)


def sync_session() -> Iterator[Session]:
    with sync_session_factory() as session:
        yield session


async def init_models() -> None:
    """Create tables directly (used for tests / first-run without Alembic)."""
    from app.models import Base  # noqa: PLC0415 - avoids a circular import

    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    await async_engine.dispose()
