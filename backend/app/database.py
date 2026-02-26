"""Async SQLAlchemy database engine, session factory, and schema management.

Key design decisions:
- Async engine (asyncpg) for non-blocking I/O
- Session factory yields scoped sessions with proper cleanup
- Schema creation separated from model definition
- RLS context setting is in dependencies.py (single source of truth)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from app.config import Settings

# Module-level engine and session factory — initialized in setup_database()
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None

# All PostgreSQL schemas used by the application
SCHEMAS = ("auth", "core", "pipeline", "chat", "deploy", "billing", "audit", "notify")


def setup_database(settings: Settings) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Create engine and session factory. Called once at app startup."""
    global _engine, _session_factory

    _engine = create_async_engine(
        settings.async_database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=settings.db_pool_recycle,
        pool_pre_ping=True,
        echo=settings.debug,
    )

    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    return _engine, _session_factory


def get_engine() -> AsyncEngine:
    """Return the initialized engine. Raises if setup_database() not called."""
    if _engine is None:
        raise RuntimeError("Database not initialized. Call setup_database() first.")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the session factory. Raises if setup_database() not called."""
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call setup_database() first.")
    return _session_factory


async def create_schemas_and_tables(engine: AsyncEngine) -> None:
    """Create all PostgreSQL schemas and tables if they don't exist.

    On startup the app automatically:
    1. Creates all 8 schemas (auth, core, pipeline, chat, etc.)
    2. Creates all tables via SQLAlchemy metadata.create_all()

    This is idempotent — safe to run every time the server starts.
    Tables that already exist are silently skipped.
    """
    from app.models import Base  # noqa: F401 — triggers all model imports

    async with engine.begin() as conn:
        # Step 1: Create schemas
        for schema in SCHEMAS:
            await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))

        # Step 2: Create all tables (skips existing ones)
        await conn.run_sync(Base.metadata.create_all)


async def close_database() -> None:
    """Dispose engine connections. Called at app shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
