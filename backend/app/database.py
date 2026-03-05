"""Async SQLAlchemy database engine, session factory, and Alembic migrations.

Key design decisions:
- Async engine (asyncpg) for non-blocking I/O
- Session factory yields scoped sessions with proper cleanup
- Alembic migrations run automatically on server startup
- RLS context setting is in dependencies.py (single source of truth)

RLS Enforcement Architecture (NOT an illusion):
    Row-Level Security is enforced at three layers — not just in migrations.

    1. Authenticated routes → get_tenant_session (dependencies.py)
       Every session yielded by this dependency calls set_tenant_context()
       BEFORE yielding, inside an active transaction:
           SET LOCAL app.current_tenant = '<org_uuid>'
           SET LOCAL app.current_user_id = '<user_uuid>'
           SET LOCAL app.user_role = '<role>'
       SET LOCAL is transaction-scoped and resets automatically when the
       transaction commits or rolls back — no risk of context leaking between
       requests.

    2. Pre-auth routes (/register, /login, /refresh) → auth_mode bypass
       These routes cannot provide a tenant ID (user is not yet authenticated).
       They execute SET LOCAL app.auth_mode = 'true' instead.
       Migration 005 defines permissive policies for auth_mode=true that allow
       registration/login operations without a tenant context.

    3. Background / pipeline services → manual _set_rls_context()
       Pipeline service code (pipeline.py) runs outside HTTP request scope.
       It manually calls _set_rls_context(session, org_id, user_id) before
       every query that touches tenant-scoped tables.

    WHY NOT IN POOL CHECKOUT: SET LOCAL is transaction-scoped. Hooking pool
    checkout (connect event) would set session-level variables that persist
    across transactions in the same connection — wrong semantics and a
    tenant-isolation bug. The application-layer pattern is intentional.
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

# R17-FIX: Admin engine for cross-tenant operations (crash recovery).
# Connects as the admin/postgres user which bypasses RLS policies.
# Only initialized if database_admin_url is configured.
_admin_engine: AsyncEngine | None = None
_admin_session_factory: async_sessionmaker[AsyncSession] | None = None

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
        # R20-FIX: Set pool_timeout to fail fast on pool exhaustion.
        # Without this, the default is to wait indefinitely for a connection.
        # With require_admin consuming 2 connections per request, just
        # (pool_size + max_overflow) / 2 concurrent admin requests exhaust
        # the pool, and all subsequent requests hang forever.
        pool_timeout=10,
        echo=settings.debug,
    )

    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    # R17-FIX: Also initialize admin engine for cross-tenant operations.
    # Used by find_interrupted_runs() and mark_interrupted() which need to
    # see all runs regardless of tenant (crash recovery at startup).
    global _admin_engine, _admin_session_factory
    if settings.database_admin_url:
        admin_url = str(settings.database_admin_url)
        # Ensure async driver scheme
        if admin_url.startswith("postgresql://"):
            admin_url = "postgresql+asyncpg://" + admin_url[len("postgresql://"):]
        _admin_engine = create_async_engine(
            admin_url,
            pool_size=2,  # Minimal — admin ops are rare
            max_overflow=2,
            pool_pre_ping=True,
            # R30-FIX-11: Admin engine needs pool_recycle too. Without it,
            # connections idle longer than PostgreSQL's wait_timeout (default
            # 8h) go stale. pool_pre_ping detects this but adds a round-trip
            # per checkout. pool_recycle proactively refreshes connections.
            pool_recycle=settings.db_pool_recycle,
            # R21-FIX: Admin engine also needs pool_timeout. Without it, a
            # stuck admin query (e.g., crash recovery holding all 4 connections)
            # causes subsequent admin operations to hang indefinitely.
            pool_timeout=10,
        )
        _admin_session_factory = async_sessionmaker(
            bind=_admin_engine,
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


def get_admin_session_factory() -> async_sessionmaker[AsyncSession] | None:
    """Return the admin session factory for cross-tenant operations.

    R17-FIX: Returns None if database_admin_url is not configured.
    The admin session bypasses RLS policies (connects as admin/postgres user).
    Use only for system operations like crash recovery, never for user requests.
    """
    return _admin_session_factory


async def run_migrations() -> None:
    """Run Alembic migrations automatically on server startup.

    Programmatically executes `alembic upgrade head` so all schemas
    and tables are created/updated without any manual commands.

    This is idempotent — safe to run every time the server starts.
    Already-applied migrations are skipped automatically by Alembic.

    F16-FIX: Uses PostgreSQL advisory lock to prevent concurrent
    Alembic instances (multiple workers starting simultaneously)
    from causing DDL deadlocks.
    """
    # F16-FIX: Advisory lock constant (0x4E455853 = "NEXS" in hex)
    _MIGRATION_LOCK_ID = 0x4E455853

    def _run_alembic_upgrade() -> None:
        from alembic import command
        from alembic.config import Config

        backend_dir = Path(__file__).resolve().parent.parent
        alembic_cfg = Config(str(backend_dir / "alembic.ini"))
        alembic_cfg.set_main_option("script_location", str(backend_dir / "alembic"))
        command.upgrade(alembic_cfg, "head")

    # F16-FIX: Acquire advisory lock before running migrations
    lock_acquired = False
    if _engine is not None:
        try:
            from sqlalchemy import text
            async with _engine.connect() as conn:
                result = await conn.execute(
                    text(f"SELECT pg_try_advisory_lock({_MIGRATION_LOCK_ID})")
                )
                lock_acquired = bool(result.scalar())
                if not lock_acquired:
                    logger.info(
                        "migration_skipped_lock_held",
                        msg="Another instance is running migrations. Skipping.",
                    )
                    return  # Another worker is handling migrations
        except Exception as exc:
            logger.warning("advisory_lock_failed", error=str(exc)[:100])
            # Fall through — run migrations anyway (single-instance fallback)
            lock_acquired = True  # Treat as acquired for the finally block

    # Run in a thread because Alembic's env.py uses asyncio.run() internally
    # which cannot be called from within an already-running event loop
    # R29-FIX-14: Wrap in try/except to sanitize Alembic errors — they can
    # include sqlalchemy.url from alembic.ini which contains the DB password.
    try:
        await asyncio.to_thread(_run_alembic_upgrade)
    except Exception as exc:
        from app.services.ai_router import _sanitize_error
        sanitized = _sanitize_error(exc)
        logger.error("alembic_migration_failed", error=sanitized)
        raise RuntimeError(f"Database migration failed: {sanitized}") from None
    finally:
        # F16-FIX: Release advisory lock
        if lock_acquired and _engine is not None:
            try:
                from sqlalchemy import text
                async with _engine.connect() as conn:
                    await conn.execute(
                        text(f"SELECT pg_advisory_unlock({_MIGRATION_LOCK_ID})")
                    )
            except Exception:
                pass  # Lock has no TTL — it's released when session ends anyway
    logger.info("Alembic migrations applied (upgrade head)")


async def close_database() -> None:
    """Dispose engine connections. Called at app shutdown."""
    global _engine, _session_factory, _admin_engine, _admin_session_factory
    if _admin_engine is not None:
        await _admin_engine.dispose()
        _admin_engine = None
        _admin_session_factory = None
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
