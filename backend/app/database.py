"""Async SQLAlchemy database engine, session factory, and Alembic migrations.

Key design decisions:
- Async engine (asyncpg) for non-blocking I/O
- Session factory yields scoped sessions with proper cleanup
- Alembic migrations run automatically on server startup
- RLS context setting is in dependencies.py (single source of truth)
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from app.config import Settings

logger = logging.getLogger(__name__)

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


async def run_migrations() -> None:
    """Run Alembic migrations automatically on server startup.

    Programmatically executes `alembic upgrade head` so all schemas
    and tables are created/updated without any manual commands.

    This is idempotent — safe to run every time the server starts.
    Already-applied migrations are skipped automatically by Alembic.
    """

    def _run_alembic_upgrade() -> None:
        from alembic import command
        from alembic.config import Config

        backend_dir = Path(__file__).resolve().parent.parent
        alembic_cfg = Config(str(backend_dir / "alembic.ini"))
        alembic_cfg.set_main_option("script_location", str(backend_dir / "alembic"))
        command.upgrade(alembic_cfg, "head")

    # Run in a thread because Alembic's env.py uses asyncio.run() internally
    # which cannot be called from within an already-running event loop
    await asyncio.to_thread(_run_alembic_upgrade)
    logger.info("Alembic migrations applied (upgrade head)")


async def close_database() -> None:
    """Dispose engine connections. Called at app shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
