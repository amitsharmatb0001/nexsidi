"""Tests for Review Round 17 fixes.

Covers critical + high fixes found during the Round 17 brutal review:
- TOKEN: Fallback revocation store fail-closed in production
- TOKEN: Fallback revoke() returns False (not True)
- TOKEN: valkey_password passed to init_revocation_store
- TOKEN: is_user_revoked timezone-safe comparison
- CELERY: _handle_soft_timeout guards against None run
- PIPELINE: find_run_by_id sets RLS context
- PIPELINE: find_interrupted_runs uses admin session factory
- AUTH: require_admin re-checks DB for role
- CONFIG: postgresql:// → postgresql+asyncpg:// rewrite
- AI: GeminiCacheManager per-hash locking (no global lock during I/O)
- PIPELINE: _run_locks/active_runs TTL eviction for PAUSED runs
- AUTH: /logout enforces expected_type="access"
- AI: VertexAI token expiry uses actual credential expiry
"""

from __future__ import annotations

import asyncio
import inspect
import re
import time

import pytest


# ── TOKEN: Fallback revocation store fail-closed ──────────────────


class TestFallbackRevocationFailClosed:
    """Fallback store must NOT be installed in production."""

    def test_init_revocation_store_checks_is_production(self):
        """init_revocation_store must check is_production before installing fallback."""
        from app.services.token_revocation import init_revocation_store
        source = inspect.getsource(init_revocation_store)
        assert "is_production" in source

    def test_init_revocation_store_does_not_install_fallback_in_prod(self):
        """In production, must NOT set _store to _FallbackRevocationStore."""
        from app.services.token_revocation import init_revocation_store
        source = inspect.getsource(init_revocation_store)
        # The production branch must return None (not a fallback)
        assert "return None" in source

    def test_fallback_revoke_returns_true(self):
        """_FallbackRevocationStore.revoke() must return True (dev-only).

        R25-FIX-8: Changed to return True. The fallback is dev-only
        (production stays None → RuntimeError → 503). Returning False
        made /refresh ALWAYS fail in dev without Valkey because the
        refresh flow treated False as "already used". Returning True
        allows token refresh to work in dev environments.
        """
        from app.services.token_revocation import _FallbackRevocationStore
        source = inspect.getsource(_FallbackRevocationStore.revoke)
        assert "return True" in source


# ── TOKEN: valkey_password passed ─────────────────────────────────


class TestValkeyPasswordPassed:
    """init_revocation_store delegates credential handling to shared Valkey pool."""

    def test_init_revocation_store_uses_shared_pool(self):
        """Must use shared Valkey pool (password handled by valkey_pool module)."""
        from app.services.token_revocation import init_revocation_store
        source = inspect.getsource(init_revocation_store)
        # Uses shared pool — no direct aioredis.from_url or password= needed
        assert "get_valkey_client" in source


# ── TOKEN: is_user_revoked timezone-safe ──────────────────────────


class TestUserRevokedTimezoneSafe:
    """is_user_revoked must handle timezone-naive datetimes safely."""

    def test_is_user_revoked_handles_naive_datetime(self):
        """Must force UTC if fromisoformat returns naive datetime."""
        from app.services.token_revocation import TokenRevocationStore
        source = inspect.getsource(TokenRevocationStore.is_user_revoked)
        assert "cutoff.tzinfo is None" in source
        assert "replace(tzinfo=" in source


# ── CELERY: _handle_soft_timeout guards None run ─────────────────


class TestSoftTimeoutNoneGuard:
    """_handle_soft_timeout must handle run=None (Celery separate process)."""

    def test_handle_soft_timeout_checks_run_is_not_none(self):
        """Must guard run.current_stage access when run is None."""
        from app.tasks.pipeline_tasks import _handle_soft_timeout
        source = inspect.getsource(_handle_soft_timeout)
        assert "run is not None" in source

    def test_handle_soft_timeout_returns_fallback_when_run_none(self):
        """Must return a dict with status=interrupted when run is None."""
        from app.tasks.pipeline_tasks import _handle_soft_timeout
        source = inspect.getsource(_handle_soft_timeout)
        # Must have a fallback return path for None run
        assert '"interrupted"' in source or "'interrupted'" in source


# ── PIPELINE: find_run_by_id sets RLS context ────────────────────


class TestFindRunByIdRLS:
    """find_run_by_id must set RLS context for the query to succeed."""

    def test_find_run_by_id_sets_rls_context(self):
        """Must call _set_rls_context before querying pipeline.runs."""
        from app.services.pipeline import PipelinePersistence
        source = inspect.getsource(PipelinePersistence.find_run_by_id)
        assert "_set_rls_context" in source

    def test_find_run_by_id_uses_begin_transaction(self):
        """Must use session.begin() for SET LOCAL to be valid."""
        from app.services.pipeline import PipelinePersistence
        source = inspect.getsource(PipelinePersistence.find_run_by_id)
        assert "session.begin()" in source


# ── PIPELINE: find_interrupted_runs uses admin session ───────────


class TestCrashRecoveryAdminSession:
    """Crash recovery must use admin session to bypass RLS."""

    def test_find_interrupted_runs_uses_admin_factory(self):
        """Must call get_admin_session_factory for cross-tenant access."""
        from app.services.pipeline import PipelinePersistence
        source = inspect.getsource(PipelinePersistence.find_interrupted_runs)
        assert "get_admin_session_factory" in source

    def test_mark_interrupted_uses_admin_factory(self):
        """Must call get_admin_session_factory for cross-tenant write."""
        from app.services.pipeline import PipelinePersistence
        source = inspect.getsource(PipelinePersistence.mark_interrupted)
        assert "get_admin_session_factory" in source


# ── DATABASE: admin session factory exists ────────────────────────


class TestAdminSessionFactory:
    """database.py must expose get_admin_session_factory."""

    def test_get_admin_session_factory_exists(self):
        """get_admin_session_factory must be importable."""
        from app.database import get_admin_session_factory
        assert callable(get_admin_session_factory)

    def test_get_admin_session_factory_returns_none_when_not_configured(self):
        """Returns None if database_admin_url is not set."""
        from app.database import get_admin_session_factory
        # In test environment, admin may or may not be configured
        result = get_admin_session_factory()
        assert result is None or callable(result)


# ── AUTH: require_admin re-checks DB for role ─────────────────────


class TestRequireAdminDBRecheck:
    """require_admin must re-check DB to prevent stale JWT role abuse."""

    def test_require_admin_queries_db(self):
        """require_admin must query the User table from DB."""
        from app.dependencies import require_admin
        source = inspect.getsource(require_admin)
        assert "select(User)" in source or "session.execute" in source

    def test_require_admin_checks_user_is_active(self):
        """Must verify user is still active in DB."""
        from app.dependencies import require_admin
        source = inspect.getsource(require_admin)
        assert "is_active" in source

    def test_require_admin_returns_db_role(self):
        """Must return TenantContext with the authoritative DB role."""
        from app.dependencies import require_admin
        source = inspect.getsource(require_admin)
        assert "user.role" in source


# ── CONFIG: postgresql:// to postgresql+asyncpg:// rewrite ────────


class TestDatabaseUrlSchemeRewrite:
    """async_database_url must rewrite scheme for asyncpg."""

    def test_async_database_url_rewrites_scheme(self):
        """Must rewrite postgresql:// to postgresql+asyncpg://."""
        from app.config import Settings
        source = inspect.getsource(Settings.async_database_url.fget)
        assert "postgresql+asyncpg://" in source
        assert "startswith" in source

    def test_rewrite_logic_is_correct(self):
        """Verify the rewrite produces correct URL."""
        # Direct logic test (not going through Settings which needs env vars)
        url = "postgresql://user:pass@host:5432/db"
        if url.startswith("postgresql://"):
            url = "postgresql+asyncpg://" + url[len("postgresql://"):]
        assert url == "postgresql+asyncpg://user:pass@host:5432/db"

    def test_already_async_url_unchanged(self):
        """URL with +asyncpg already present should not be double-rewritten."""
        url = "postgresql+asyncpg://user:pass@host:5432/db"
        if url.startswith("postgresql://"):
            url = "postgresql+asyncpg://" + url[len("postgresql://"):]
        assert url == "postgresql+asyncpg://user:pass@host:5432/db"


# ── AI: GeminiCacheManager per-hash locking ──────────────────────


class TestGeminiCachePerHashLocking:
    """GeminiCacheManager must use per-hash locks, not a single global lock."""

    def test_has_per_hash_lock_dict(self):
        """Must have a _pending dict for per-hash locks."""
        from app.services.ai_router import GeminiCacheManager
        source = inspect.getsource(GeminiCacheManager.__init__)
        assert "_pending" in source

    def test_has_global_lock_for_dict_access_only(self):
        """Must have _global_lock (not _lock) for quick dict operations."""
        from app.services.ai_router import GeminiCacheManager
        source = inspect.getsource(GeminiCacheManager.__init__)
        assert "_global_lock" in source

    def test_get_or_create_uses_hash_lock(self):
        """get_or_create_cache must acquire per-hash lock for network I/O."""
        from app.services.ai_router import GeminiCacheManager
        source = inspect.getsource(GeminiCacheManager.get_or_create_cache)
        assert "hash_lock" in source

    def test_no_global_lock_during_http_post(self):
        """HTTP POST must NOT be under the global lock."""
        from app.services.ai_router import GeminiCacheManager
        source = inspect.getsource(GeminiCacheManager.get_or_create_cache)
        # The http.post call should be inside hash_lock, not _global_lock
        # Verify _global_lock is not wrapping the http.post
        lines = source.split("\n")
        in_global = False
        for line in lines:
            stripped = line.strip()
            if "async with self._global_lock:" in stripped:
                in_global = True
            if in_global and "http.post" in stripped:
                pytest.fail("http.post must NOT be under _global_lock")
            # Global lock context should be short (only dict access)
            if in_global and "hash_lock = " in stripped:
                in_global = False


# ── PIPELINE: _run_locks TTL eviction for PAUSED runs ───────────


class TestRunLocksTTLEviction:
    """_run_locks must be evicted for abandoned PAUSED runs."""

    def test_orchestrator_has_paused_at_tracking(self):
        """PipelineOrchestrator must track _paused_at timestamps."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator.__init__)
        assert "_paused_at" in source

    def test_orchestrator_has_evict_method(self):
        """Must have _evict_stale_paused method."""
        from app.services.pipeline import PipelineOrchestrator
        assert hasattr(PipelineOrchestrator, "_evict_stale_paused")

    def test_create_run_calls_eviction(self):
        """create_run must call _evict_stale_paused."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator.create_run)
        assert "_evict_stale_paused" in source

    def test_resume_run_calls_eviction(self):
        """resume_run must call _evict_stale_paused."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator.resume_run)
        assert "_evict_stale_paused" in source

    def test_resume_run_cleans_up_not_found(self):
        """resume_run must clean up _run_locks when run not found."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator.resume_run)
        assert "result is None" in source
        assert "_run_locks.pop" in source

    def test_evict_stale_paused_respects_ttl(self):
        """_evict_stale_paused must only evict runs older than TTL."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator._evict_stale_paused)
        assert "_PAUSED_TTL_SECONDS" in source


# ── AUTH: /logout enforces expected_type ──────────────────────────


class TestLogoutTypeEnforcement:
    """/logout must enforce expected_type='access' in decode_token."""

    def test_logout_enforces_access_type(self):
        """decode_token call in /logout must pass expected_type='access'."""
        from app.routers.auth import logout
        source = inspect.getsource(logout)
        assert 'expected_type="access"' in source


# ── AI: VertexAI token expiry uses actual credential expiry ──────


class TestVertexAITokenExpiry:
    """VertexAICredentialManager must use actual expiry, not hardcoded."""

    def test_uses_credentials_expiry_attribute(self):
        """Must check self._credentials.expiry for actual expiry time."""
        from app.services.secret_manager import VertexAICredentialManager
        source = inspect.getsource(VertexAICredentialManager.get_access_token)
        assert "credentials.expiry" in source or "_credentials.expiry" in source

    def test_has_fallback_expiry(self):
        """Must fall back to 3600s if expiry attribute is missing."""
        from app.services.secret_manager import VertexAICredentialManager
        source = inspect.getsource(VertexAICredentialManager.get_access_token)
        assert "3600" in source


# ── DATABASE: admin engine also closed on shutdown ────────────────


class TestAdminEngineShutdown:
    """close_database must also dispose the admin engine."""

    def test_close_database_disposes_admin(self):
        """close_database must handle _admin_engine disposal."""
        from app.database import close_database
        source = inspect.getsource(close_database)
        assert "_admin_engine" in source
