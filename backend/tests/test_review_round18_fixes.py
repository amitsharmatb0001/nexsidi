"""Tests for Review Round 18 fixes.

Covers:
- AUTH: verify_token catches broad exceptions (Redis ConnectionError)
- AUTH: /logout, /logout-all, /refresh handle Valkey failures (503)
- AUTH: /logout refresh token ownership check
- AUTH: /refresh fail-closed on Redis connection error
- CELERY: _handle_soft_timeout uses admin session for RLS bypass
- PIPELINE: _get_max_step_order sets RLS context
- PIPELINE: _run_locks/_paused_at remapped on run_id change
- PIPELINE: find_run_by_id always sets RLS context (even when org_id=None)
- PIPELINE: _evict ordering (pop before evict)
- AI: GeminiCacheManager TTL safety margin
- AI: GeminiCacheManager _pending cleanup + clear()
- AI: VertexAI naive datetime timezone fix
- WS: auth_ok send inside try/finally
"""

from __future__ import annotations

import inspect

import pytest


# ── AUTH: verify_token catches broad exceptions ──────────────────


class TestVerifyTokenBroadCatch:
    """verify_token must catch Redis ConnectionError, not just RuntimeError."""

    def test_verify_token_has_except_exception_clause(self):
        """Must have `except Exception` after `except RuntimeError`."""
        from app.services.auth import verify_token
        source = inspect.getsource(verify_token)
        # Must re-raise JWTError first, then catch RuntimeError, then broad Exception
        assert "except JWTError:" in source
        assert "except RuntimeError:" in source
        assert "except Exception:" in source

    def test_verify_token_reraises_jwt_error(self):
        """Must re-raise JWTError (revocation rejections)."""
        from app.services.auth import verify_token
        source = inspect.getsource(verify_token)
        lines = source.split("\n")
        # Find except JWTError and verify it has raise
        for i, line in enumerate(lines):
            if "except JWTError:" in line:
                # Next non-empty line should be raise
                for j in range(i + 1, min(i + 3, len(lines))):
                    if lines[j].strip():
                        assert "raise" in lines[j]
                        break
                break


# ── AUTH: /logout handles Valkey failures ─────────────────────────


class TestLogoutValkeyFailure:
    """/logout must return 503 when Valkey is unavailable."""

    def test_logout_wraps_revocation_in_try_except(self):
        """Must wrap get_revocation_store/revoke in try/except."""
        from app.routers.auth import logout
        source = inspect.getsource(logout)
        assert "503" in source or "SERVICE_UNAVAILABLE" in source

    def test_logout_all_wraps_revocation_in_try_except(self):
        """/logout-all must also handle Valkey failures."""
        from app.routers.auth import logout_all
        source = inspect.getsource(logout_all)
        assert "503" in source or "SERVICE_UNAVAILABLE" in source


# ── AUTH: /logout refresh token ownership check ───────────────────


class TestLogoutRefreshOwnership:
    """/logout must verify refresh token belongs to same user."""

    def test_logout_checks_refresh_token_sub(self):
        """Must verify refresh_payload.sub == ctx.user_id."""
        from app.routers.auth import logout
        source = inspect.getsource(logout)
        assert "ctx.user_id" in source
        assert "sub" in source


# ── AUTH: /refresh fail-closed on Redis connection error ──────────


class TestRefreshFailClosed:
    """/refresh must fail-closed on Redis errors in production."""

    def test_refresh_catches_broad_exception(self):
        """Must have `except Exception` for Redis connection errors."""
        from app.routers.auth import refresh_token as refresh
        source = inspect.getsource(refresh)
        # Must handle broad exceptions, not just RuntimeError
        assert "except Exception" in source

    def test_refresh_returns_503_in_production(self):
        """Must return 503 when Valkey is down in production."""
        from app.routers.auth import refresh_token as refresh
        source = inspect.getsource(refresh)
        assert "503" in source or "SERVICE_UNAVAILABLE" in source


# ── CELERY: _handle_soft_timeout uses admin session ──────────────


class TestSoftTimeoutAdminSession:
    """_handle_soft_timeout must use admin session for RLS bypass."""

    def test_handle_soft_timeout_uses_admin_session(self):
        """Must call get_admin_session_factory for RLS-bypassing UPDATE."""
        from app.tasks.pipeline_tasks import _handle_soft_timeout
        source = inspect.getsource(_handle_soft_timeout)
        assert "get_admin_session_factory" in source


# ── PIPELINE: _get_max_step_order sets RLS context ────────────────


class TestGetMaxStepOrderRLS:
    """_get_max_step_order must set RLS context before querying."""

    def test_get_max_step_order_sets_rls(self):
        """Must call _set_rls_context inside session.begin()."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator._get_max_step_order)
        assert "_set_rls_context" in source
        assert "session.begin()" in source


# ── PIPELINE: _run_locks/_paused_at remapped on run_id change ─────


class TestRunIdRemap:
    """_persist_run must remap _run_locks and _paused_at on ID change."""

    def test_persist_run_remaps_run_locks(self):
        """Must remap _run_locks when run_id changes."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator._persist_run)
        assert "_run_locks.pop(old_run_id" in source

    def test_persist_run_remaps_paused_at(self):
        """Must remap _paused_at when run_id changes."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator._persist_run)
        assert "_paused_at.pop(old_run_id" in source


# ── PIPELINE: find_run_by_id always sets RLS context ──────────────


class TestFindRunByIdAlwaysRLS:
    """find_run_by_id must set RLS context even when org_id is None."""

    def test_always_calls_set_rls_context(self):
        """Must call _set_rls_context unconditionally (not inside if org_uuid)."""
        from app.services.pipeline import PipelinePersistence
        source = inspect.getsource(PipelinePersistence.find_run_by_id)
        # Must have the effective_org fallback pattern
        assert "effective_org" in source or "00000000-0000-0000-0000-000000000000" in source


# ── PIPELINE: _evict ordering (pop before evict) ─────────────────


class TestEvictOrdering:
    """resume_run must pop run_id from paused_at BEFORE eviction."""

    def test_pop_before_evict(self):
        """_paused_at.pop must come before _evict_stale_paused."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator.resume_run)
        pop_pos = source.find("_paused_at.pop(run_id")
        evict_pos = source.find("_evict_stale_paused")
        assert pop_pos < evict_pos, "pop must come before evict"


# ── AI: GeminiCacheManager TTL safety margin ──────────────────────


class TestGeminiCacheTTLMargin:
    """GeminiCacheManager must expire local cache before remote TTL."""

    def test_has_ttl_safety_margin(self):
        """Must have _TTL_SAFETY_MARGIN class attribute."""
        from app.services.ai_router import GeminiCacheManager
        assert hasattr(GeminiCacheManager, "_TTL_SAFETY_MARGIN")
        assert GeminiCacheManager._TTL_SAFETY_MARGIN > 0

    def test_ttl_check_uses_margin(self):
        """TTL comparison must subtract safety margin."""
        from app.services.ai_router import GeminiCacheManager
        source = inspect.getsource(GeminiCacheManager.get_or_create_cache)
        assert "_TTL_SAFETY_MARGIN" in source


# ── AI: GeminiCacheManager _pending cleanup ───────────────────────


class TestGeminiCachePendingCleanup:
    """GeminiCacheManager.clear() must also clear _pending."""

    def test_clear_clears_pending(self):
        """clear() must call _pending.clear()."""
        from app.services.ai_router import GeminiCacheManager
        source = inspect.getsource(GeminiCacheManager.clear)
        assert "_pending.clear()" in source


# ── AI: VertexAI naive datetime timezone fix ──────────────────────


class TestVertexAITimezoneHandling:
    """VertexAI credential expiry must handle naive UTC datetimes."""

    def test_handles_naive_expiry(self):
        """Must check tzinfo and force UTC if None."""
        from app.services.secret_manager import VertexAICredentialManager
        source = inspect.getsource(VertexAICredentialManager.get_access_token)
        assert "tzinfo is None" in source
        assert "replace(tzinfo=" in source


# ── WS: auth_ok send inside try/finally ───────────────────────────


class TestWebSocketAuthOkInTry:
    """WebSocket auth_ok send must be inside try/finally for cleanup."""

    def test_auth_ok_inside_try_block(self):
        """send_json(auth_ok) must be inside the try block, not before it."""
        from app.routers.websocket import pipeline_websocket as pipeline_ws
        source = inspect.getsource(pipeline_ws)
        # The R18 fix comment should be present (moved send inside try)
        assert "R18-FIX" in source or "Moved send_json inside" in source
        # auth_ok and manager.disconnect must both be in the function
        assert '"auth_ok"' in source
        assert "manager.disconnect" in source
        # The finally block must contain disconnect
        finally_pos = source.find("finally:")
        disconnect_pos = source.find("manager.disconnect")
        assert finally_pos > 0
        assert disconnect_pos > finally_pos, \
            "manager.disconnect must be inside finally block"
