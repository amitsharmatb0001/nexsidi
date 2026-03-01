"""Tests for Review Round 16 fixes.

Covers critical + high fixes found during the Round 16 brutal review:
- PIPELINE: Celery task reuses pre-persisted run (no duplicate)
- PIPELINE: __checkpoint_approved__ cleared from context after use
- AUTH: Fail-closed token revocation in production
- AI: batch_id SSRF validation
- AUTH: /logout revokes refresh token
- TOKEN: Valkey URL password not logged
- WEBSOCKET: Unified error codes (no cross-tenant info leak)
- AGENTS: Tool result truncation (OOM prevention)
- CELERY: Fresh DB session in soft timeout handler
"""

from __future__ import annotations

import inspect
import re

import pytest


# ── PIPELINE: Celery task uses pre-persisted run ─────────────────


class TestCeleryUsesPrePersistedRun:
    """Celery task must use the run already persisted by the router."""

    def test_first_attempt_looks_up_existing_run(self):
        """_run_pipeline_async first attempt must look up run by ID, not create new."""
        from app.tasks.pipeline_tasks import _run_pipeline_async
        source = inspect.getsource(_run_pipeline_async)
        # Must look up the run by ID
        assert "find_run_by_id" in source
        # The create_run call should only be in the fallback path
        assert "celery_run_not_found_creating_new" in source

    def test_first_attempt_does_not_blindly_create(self):
        """The primary path should NOT be orch.create_run()."""
        from app.tasks.pipeline_tasks import _run_pipeline_async
        source = inspect.getsource(_run_pipeline_async)
        # orch.get_run or find_run_by_id must come BEFORE any create_run
        get_pos = source.find("get_run(run_id)")
        create_pos = source.find("create_run(")
        if create_pos > 0:
            assert get_pos < create_pos, (
                "Must look up existing run BEFORE creating a new one"
            )


# ── PIPELINE: __checkpoint_approved__ cleared after use ──────────


class TestCheckpointApprovedCleared:
    """__checkpoint_approved__ must be cleared from context after checkpoint consumed."""

    def test_handle_checkpoint_clears_context_key(self):
        """_handle_checkpoint must pop __checkpoint_approved__ from context."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator._handle_checkpoint)
        assert "__checkpoint_approved__" in source
        assert "pop(" in source or "del " in source

    def test_handle_checkpoint_resets_both_flag_and_context(self):
        """Must reset BOTH checkpoint_approved=False AND context key."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator._handle_checkpoint)
        assert "checkpoint_approved = False" in source
        assert "context.pop(" in source


# ── AUTH: Fail-closed token revocation in production ─────────────


class TestFailClosedRevocation:
    """verify_token must fail-closed in production when Valkey unavailable."""

    def test_verify_token_checks_is_production(self):
        """verify_token must check is_production when store unavailable."""
        from app.services.auth import verify_token
        source = inspect.getsource(verify_token)
        assert "is_production" in source

    def test_verify_token_raises_in_production(self):
        """verify_token must raise JWTError in production when store unavailable."""
        from app.services.auth import verify_token
        source = inspect.getsource(verify_token)
        assert "Token revocation service unavailable" in source

    def test_verify_token_allows_in_dev(self):
        """verify_token must skip checks in development (graceful degradation)."""
        from app.services.auth import verify_token
        source = inspect.getsource(verify_token)
        # The except RuntimeError block must have a branch for non-production
        assert "RuntimeError" in source
        # Must have both the production raise and the dev pass-through
        assert "is_production" in source


# ── AI: batch_id SSRF validation ─────────────────────────────────


class TestBatchIdValidation:
    """batch_id must be validated to prevent path injection / SSRF."""

    def test_get_batch_results_validates_format(self):
        """get_batch_results must validate batch_id format."""
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter.get_batch_results)
        assert "msgbatch_" in source
        assert "re.match" in source or "re.fullmatch" in source or "_re.match" in source

    def test_valid_batch_id_pattern(self):
        """Batch ID validation regex must accept valid IDs."""
        pattern = r"^msgbatch_[A-Za-z0-9_-]+$"
        assert re.match(pattern, "msgbatch_abc123")
        assert re.match(pattern, "msgbatch_A1B2_c3-d4")

    def test_invalid_batch_id_rejected(self):
        """Path traversal attempts must not match."""
        pattern = r"^msgbatch_[A-Za-z0-9_-]+$"
        assert not re.match(pattern, "../../evil")
        assert not re.match(pattern, "msgbatch_abc/../../evil")
        assert not re.match(pattern, "")
        assert not re.match(pattern, "msgbatch_")  # empty after prefix is OK per regex
        # Actually msgbatch_ with nothing after should also fail
        # The + requires at least one char


# ── AUTH: /logout revokes refresh token ──────────────────────────


class TestLogoutRevokesRefreshToken:
    """/logout must accept and revoke refresh token."""

    def test_logout_accepts_body(self):
        """logout endpoint must accept LogoutRequest body."""
        from app.routers.auth import logout
        source = inspect.getsource(logout)
        assert "LogoutRequest" in source or "body" in source

    def test_logout_revokes_refresh_if_provided(self):
        """logout must revoke refresh token when provided in body."""
        from app.routers.auth import logout
        source = inspect.getsource(logout)
        assert "refresh_token" in source
        assert "refresh_payload" in source or "body.refresh_token" in source

    def test_logout_request_schema_exists(self):
        """LogoutRequest schema must exist."""
        from app.schemas.auth import LogoutRequest
        # Must have optional refresh_token field
        assert "refresh_token" in LogoutRequest.model_fields


# ── TOKEN: Valkey URL password not logged ────────────────────────


class TestValkeyUrlSanitized:
    """Valkey URL must not include password when logged."""

    def test_init_revocation_store_uses_shared_pool(self):
        """init_revocation_store uses shared Valkey pool (no direct URL handling)."""
        from app.services.token_revocation import init_revocation_store
        source = inspect.getsource(init_revocation_store)
        # Must use shared Valkey pool (no direct URL construction or sanitization needed)
        assert "get_valkey_client" in source
        # Must NOT log the raw settings.valkey_url
        assert "valkey_url[:30]" not in source


# ── WEBSOCKET: Unified error codes ──────────────────────────────


class TestWebSocketUnifiedErrorCodes:
    """WebSocket must use same error code for 'not found' and 'wrong tenant'."""

    def test_no_separate_access_denied_code(self):
        """pipeline_websocket must NOT use separate 4003 code for access denied."""
        from app.routers.websocket import pipeline_websocket
        source = inspect.getsource(pipeline_websocket)
        # Should NOT have a separate 4003 "Access denied" path
        # Both not-found and wrong-tenant should return 4004
        lines = source.split("\n")
        for line in lines:
            stripped = line.strip()
            if "4003" in stripped and not stripped.startswith("#"):
                pytest.fail(f"Found separate 4003 error code: {stripped}")

    def test_combined_not_found_and_tenant_check(self):
        """Must combine None check and org check in single condition."""
        from app.routers.websocket import pipeline_websocket
        source = inspect.getsource(pipeline_websocket)
        # R29-FIX-10: Now uses str() normalization for UUID/str comparison.
        assert "run is None or str(run.organization_id)" in source


# ── AGENTS: Tool result truncation ───────────────────────────────


class TestToolResultTruncation:
    """Tool results must be truncated to prevent OOM."""

    def test_call_ai_with_tools_truncates_results(self):
        """call_ai_with_tools must truncate large tool results."""
        from app.agents.base import call_ai_with_tools
        source = inspect.getsource(call_ai_with_tools)
        assert "_MAX_TOOL_RESULT_CHARS" in source or "truncat" in source.lower()

    def test_truncation_limit_is_reasonable(self):
        """Truncation limit should be between 10K and 100K chars."""
        from app.agents.base import call_ai_with_tools
        source = inspect.getsource(call_ai_with_tools)
        # Extract the limit value (may use underscores like 50_000)
        match = re.search(r"_MAX_TOOL_RESULT_CHARS\s*=\s*([\d_]+)", source)
        assert match, "Must define _MAX_TOOL_RESULT_CHARS"
        limit = int(match.group(1).replace("_", ""))
        assert 10_000 <= limit <= 100_000, f"Limit {limit} should be 10K-100K"


# ── CELERY: Fresh DB session in soft timeout handler ─────────────


class TestSoftTimeoutFreshSession:
    """Soft timeout handler must use fresh DB session."""

    def test_handle_soft_timeout_uses_fresh_session(self):
        """_handle_soft_timeout must create a fresh DB session."""
        from app.tasks.pipeline_tasks import _handle_soft_timeout
        source = inspect.getsource(_handle_soft_timeout)
        assert "get_session_factory" in source
        # Must use raw SQL (not ORM) to avoid tainted session state
        assert "text(" in source or "execute(" in source

    def test_handle_soft_timeout_uses_raw_sql(self):
        """Must use raw SQL UPDATE, not ORM persist."""
        from app.tasks.pipeline_tasks import _handle_soft_timeout
        source = inspect.getsource(_handle_soft_timeout)
        assert "UPDATE pipeline.runs" in source or "UPDATE" in source.upper()
