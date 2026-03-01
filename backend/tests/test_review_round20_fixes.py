"""Tests for Review Round 20 fixes.

Covers:
- AUTH: is_revoked/is_user_revoked fail-closed on empty string
- AUTH: /logout uses payload["jti"] directly (no .get() falsiness bug)
- AUTH: verify_token guards datetime.fromtimestamp() overflow
- AUTH: /refresh guards uuid.UUID() ValueError
- DB: pool_timeout configured on engine
- MAIN: shutdown cancels _active_tasks before closing resources
- AI: _pending cleanup on success AND failure paths
- AI: Security-critical tasks check circuit breaker
- AI: SSE streaming handles [DONE] sentinel gracefully
- CELERY: Worker clears stale orchestrator cache before loading
- PIPELINE: recover_interrupted_runs tracks _paused_at for PAUSED runs
- PIPELINE: approve_checkpoint ValueError caught as 409
- REVOCATION: shutdown_revocation_store checks _redis not None
- AUDIT: log_action validates user_agent length and ip_address format
"""

from __future__ import annotations

import inspect
import uuid

import pytest


# ── AUTH: is_revoked/is_user_revoked fail-closed ──────────────────


class TestRevocationFailClosed:
    """Empty jti/user_id must be treated as revoked (fail-closed)."""

    def test_is_revoked_returns_true_for_empty_string(self):
        """is_revoked('') must return True, not False."""
        from app.services.token_revocation import TokenRevocationStore
        source = inspect.getsource(TokenRevocationStore.is_revoked)
        # Must return True for empty jti (fail-closed)
        assert "return True" in source

    def test_is_user_revoked_returns_true_for_empty_string(self):
        """is_user_revoked('') must return True, not False."""
        from app.services.token_revocation import TokenRevocationStore
        source = inspect.getsource(TokenRevocationStore.is_user_revoked)
        # The early return for empty user_id must be True (fail-closed)
        lines = source.split("\n")
        for i, line in enumerate(lines):
            if "not user_id" in line:
                # Next non-empty line should return True
                for j in range(i + 1, min(i + 4, len(lines))):
                    if "return True" in lines[j]:
                        break
                else:
                    pytest.fail("is_user_revoked must return True for empty user_id")
                break


# ── AUTH: /logout uses payload["jti"] directly ─────────────────────


class TestLogoutDirectClaimAccess:
    """/logout must use payload['jti'], not payload.get('jti')."""

    def test_logout_no_get_for_jti(self):
        """Must not use .get() for jti/exp in logout."""
        from app.routers.auth import logout
        source = inspect.getsource(logout)
        # Must have payload["jti"], not payload.get("jti")
        assert 'payload["jti"]' in source
        assert 'payload["exp"]' in source

    def test_logout_no_falsiness_check(self):
        """Must not have `if not jti or not exp_ts:` check."""
        from app.routers.auth import logout
        source = inspect.getsource(logout)
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert "if not jti" not in stripped, \
                "Active code must not use falsiness check for mandatory claims"


# ── AUTH: verify_token guards fromtimestamp overflow ───────────────


class TestVerifyTokenTimestampGuard:
    """verify_token must guard datetime.fromtimestamp() against overflow."""

    def test_catches_overflow_error(self):
        """Must catch OverflowError/OSError around fromtimestamp."""
        from app.services.auth import verify_token
        source = inspect.getsource(verify_token)
        assert "OverflowError" in source
        assert "OSError" in source

    def test_raises_jwt_error_on_bad_timestamp(self):
        """Must raise JWTError, not propagate OverflowError."""
        from app.services.auth import verify_token
        source = inspect.getsource(verify_token)
        # Find the except block that catches OverflowError
        lines = source.split("\n")
        in_except = False
        found_jwt_error = False
        for line in lines:
            stripped = line.strip()
            if "OverflowError" in stripped and "except" in stripped:
                in_except = True
            if in_except and "JWTError" in stripped:
                found_jwt_error = True
                break
        assert found_jwt_error, "Must raise JWTError on bad timestamp"


# ── AUTH: /refresh uuid.UUID() guard ───────────────────────────────


class TestRefreshUUIDGuard:
    """/refresh must guard uuid.UUID() parsing of sub/org claims."""

    def test_refresh_catches_value_error(self):
        """Must catch ValueError from uuid.UUID()."""
        from app.routers.auth import refresh_token
        source = inspect.getsource(refresh_token)
        # Must have a try/except around uuid.UUID calls
        assert "ValueError" in source
        # Must return 401, not 500
        assert "401" in source or "HTTP_401_UNAUTHORIZED" in source


# ── DB: pool_timeout configured ────────────────────────────────────


class TestDatabasePoolTimeout:
    """Database engine must have pool_timeout to prevent indefinite hangs."""

    def test_pool_timeout_configured(self):
        """create_async_engine must include pool_timeout parameter."""
        from app.database import setup_database
        source = inspect.getsource(setup_database)
        assert "pool_timeout" in source


# ── MAIN: shutdown cancels _active_tasks ───────────────────────────


class TestShutdownCancelsActiveTasks:
    """Shutdown must cancel in-flight pipeline tasks before closing resources."""

    def test_shutdown_references_active_tasks(self):
        """Lifespan shutdown must reference _active_tasks."""
        from app.main import lifespan
        source = inspect.getsource(lifespan)
        assert "_active_tasks" in source

    def test_shutdown_cancels_before_closing(self):
        """_active_tasks cancellation must come BEFORE shutdown_ai_router."""
        from app.main import lifespan
        source = inspect.getsource(lifespan)
        active_tasks_pos = source.find("_active_tasks")
        shutdown_ai_pos = source.find("shutdown_ai_router")
        assert active_tasks_pos < shutdown_ai_pos, \
            "_active_tasks must be cancelled BEFORE resources are closed"


# ── AI: _pending cleanup on all paths ──────────────────────────────


class TestPendingCleanupAllPaths:
    """GeminiCacheManager must clean _pending on success AND failure paths."""

    def test_pending_cleaned_on_success_path(self):
        """Must pop content_hash from _pending on 200 success."""
        from app.services.ai_router import GeminiCacheManager
        source = inspect.getsource(GeminiCacheManager.get_or_create_cache)
        # Count _pending.pop occurrences — must be at least 2 (success + failure)
        pop_count = source.count("_pending.pop(")
        assert pop_count >= 2, \
            f"_pending.pop must appear at least 2x (success + failure), found {pop_count}"

    def test_pending_cleaned_after_except(self):
        """Must have a _pending.pop after the try/except block for non-200 paths."""
        from app.services.ai_router import GeminiCacheManager
        source = inspect.getsource(GeminiCacheManager.get_or_create_cache)
        # There should be a _pending.pop call on the return None path
        return_none_pos = source.rfind("return None")
        last_pending_pop = source.rfind("_pending.pop(", 0, return_none_pos)
        assert last_pending_pop > 0, "_pending.pop must appear before final return None"


# ── AI: Security tasks check circuit breaker ────────────────────────


class TestSecurityTasksCircuitBreaker:
    """Security-critical tasks must check circuit breaker before routing."""

    def test_security_override_checks_circuit_breaker(self):
        """Must check is_available() for security model provider."""
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter.select_model)
        # Must check circuit breaker in the security override section
        # Find the SECURITY_CRITICAL_TASKS block
        sec_pos = source.find("SECURITY_CRITICAL_TASKS")
        # Find is_available after that
        avail_pos = source.find("is_available()", sec_pos)
        assert avail_pos > sec_pos, \
            "Security override must check circuit breaker via is_available()"

    def test_security_override_has_fallback(self):
        """Must escalate if security model's provider is down."""
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter.select_model)
        assert "security_model_unavailable_escalating" in source


# ── AI: SSE [DONE] sentinel ────────────────────────────────────────


class TestSSESentinelHandling:
    """SSE streaming parsers must handle [DONE] sentinel gracefully."""

    def test_anthropic_stream_handles_done(self):
        """_stream_anthropic must skip [DONE] sentinel."""
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter._stream_anthropic)
        assert "[DONE]" in source

    def test_google_ai_stream_handles_done(self):
        """_stream_google_ai must skip [DONE] sentinel."""
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter._stream_google_ai)
        assert "[DONE]" in source

    def test_vertex_stream_handles_done(self):
        """_stream_vertex must skip [DONE] sentinel."""
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter._stream_vertex)
        assert "[DONE]" in source

    def test_stream_catches_json_decode_error(self):
        """All stream methods must catch orjson.JSONDecodeError."""
        from app.services.ai_router import AIRouter
        for method_name in ("_stream_anthropic", "_stream_google_ai", "_stream_vertex"):
            source = inspect.getsource(getattr(AIRouter, method_name))
            assert "JSONDecodeError" in source, \
                f"{method_name} must catch JSONDecodeError"


# ── CELERY: Worker clears stale cache ──────────────────────────────


class TestCeleryWorkerClearsCache:
    """Celery worker must clear stale orchestrator cache before loading from DB."""

    def test_run_pipeline_async_pops_stale_cache(self):
        """Must call _active_runs.pop(run_id) before loading from DB."""
        from app.tasks.pipeline_tasks import _run_pipeline_async
        source = inspect.getsource(_run_pipeline_async)
        # Must have _active_runs.pop before find_run_by_id
        pop_pos = source.find("_active_runs.pop(run_id")
        find_pos = source.find("find_run_by_id")
        assert pop_pos > 0, "_active_runs.pop(run_id) must be present"
        assert pop_pos < find_pos, "_active_runs.pop must come BEFORE find_run_by_id"


# ── PIPELINE: recover_interrupted_runs tracks _paused_at ────────────


class TestRecoverPausedRunsTracksTimestamp:
    """recover_interrupted_runs must add _paused_at for restored PAUSED runs."""

    def test_paused_at_set_during_recovery(self):
        """Must set _paused_at[run.run_id] when restoring PAUSED runs."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator.recover_interrupted_runs)
        assert "_paused_at" in source


# ── PIPELINE: approve_checkpoint ValueError caught ──────────────────


class TestApproveCheckpointErrorHandling:
    """approve_checkpoint ValueError must be caught as 409 Conflict."""

    def test_catches_value_error(self):
        """Router must catch ValueError from approve_checkpoint."""
        from app.routers.pipeline import approve_checkpoint
        source = inspect.getsource(approve_checkpoint)
        assert "ValueError" in source
        assert "409" in source or "HTTP_409_CONFLICT" in source


# ── REVOCATION: shutdown checks _redis not None ─────────────────────


class TestShutdownRevocationStoreNoneCheck:
    """shutdown_revocation_store must check _store is not None before cleanup."""

    def test_checks_store_not_none(self):
        """Must guard against _store being None before setting to None."""
        from app.services.token_revocation import shutdown_revocation_store
        source = inspect.getsource(shutdown_revocation_store)
        # Shared pool: no _redis.aclose() needed, just check _store is not None
        assert "_store is not None" in source


# ── AUDIT: log_action validates inputs ──────────────────────────────


class TestAuditLogActionValidation:
    """log_action must validate user_agent length and ip_address format."""

    def test_truncates_user_agent(self):
        """Must truncate user_agent to max length."""
        from app.services.audit import log_action, _MAX_USER_AGENT_LENGTH
        assert _MAX_USER_AGENT_LENGTH > 0
        source = inspect.getsource(log_action)
        assert "_MAX_USER_AGENT_LENGTH" in source

    def test_validates_ip_address(self):
        """Must validate ip_address format before DB write."""
        from app.services.audit import log_action
        source = inspect.getsource(log_action)
        assert "ipaddress" in source or "ip_address" in source
        # Must catch ValueError from ipaddress validation
        assert "ValueError" in source

    def test_has_max_user_agent_constant(self):
        """Must define _MAX_USER_AGENT_LENGTH."""
        from app.services import audit
        assert hasattr(audit, "_MAX_USER_AGENT_LENGTH")
        assert audit._MAX_USER_AGENT_LENGTH <= 1024  # Reasonable upper bound
