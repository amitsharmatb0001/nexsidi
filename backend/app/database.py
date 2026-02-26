"""Async SQLAlchemy database engine, session factory, and schema management.

Key design decisions:
- Async engine (asyncpg) for non-blocking I/O
- Session factory yields scoped sessions with proper cleanup
- Schema creation separated from model definition
- RLS context (tenant, user, role) set via SET LOCAL per-transaction
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
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


async def create_schemas(engine: AsyncEngine) -> None:
    """Create all PostgreSQL schemas if they don't exist.

    This runs with the app database user. The schemas must be pre-created
    by the admin user in production (via migration). This is a dev convenience.
    """
    async with engine.begin() as conn:
        for schema in SCHEMAS:
            await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))


@asynccontextmanager
async def get_tenant_session(
    organization_id: str,
    user_id: str,
    role: str,
) -> AsyncGenerator[AsyncSession]:
    """Yield a session with RLS context variables set.

    Every query in this session is automatically filtered by PostgreSQL RLS
    policies using the SET LOCAL variables. SET LOCAL is transaction-scoped —
    it resets when the transaction ends.

    Args:
        organization_id: UUID of the user's organization
        user_id: UUID of the authenticated user
        role: User's role (super_admin, org_admin, team_lead, member, viewer)
    """
    factory = get_session_factory()
    async with factory() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.current_tenant = :tid"), {"tid": organization_id})
            await session.execute(text("SET LOCAL app.current_user = :uid"), {"uid": user_id})
            await session.execute(text("SET LOCAL app.current_role = :role"), {"role": role})
            yield session


async def get_session() -> AsyncGenerator[AsyncSession]:
    """Yield a plain session without RLS context. For health checks and non-tenant routes."""
    factory = get_session_factory()
    async with factory() as session:
        async with session.begin():
            yield session


async def close_database() -> None:
    """Dispose engine connections. Called at app shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
