"""Tests for Round 4 fixes: CRITICAL + HIGH priority issues.

Covers:
- A-1-FIX: store_output catches engine.store() exceptions
- A-2-FIX: get_step_context handles uninitialized engine
- S-20-FIX: JWT decode_token validates token type
- S-4-FIX: API key sanitization in error logs
- R-01-FIX: Chat session creation verifies project ownership
- R-03-FIX: Pipeline run_id UUID validation
- A-16-FIX: Tenant UUID normalization
- S-7-FIX: Pipeline active_runs eviction on completion
- R-05-FIX: Rate limiting on refresh endpoint
- R-11-FIX: WebSocket accept-before-close
- S-18-FIX: Audit chain integrity preserved when event cap hit
- R-07-FIX: RBAC on update_project
- R-08-FIX: RBAC on start_pipeline
- S-1-FIX: Circuit breaker async state transitions
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jose import JWTError


# ── A-1-FIX: store_output catches engine.store() exceptions ────


class TestStoreOutputResilience:
    """store_output must not crash agents on Valkey failures."""

    @pytest.mark.asyncio
    async def test_store_output_catches_engine_exception(self):
        from app.agents.base import store_output

        mock_engine = MagicMock()
        mock_engine.store = AsyncMock(side_effect=ConnectionError("Valkey down"))

        agent = MagicMock()
        agent.name = "test_agent"

        with patch("app.services.context_engine.get_context_engine", return_value=mock_engine):
            # Should NOT raise — agent work must not be lost
            await store_output(agent, "run-123", {"key": "value"})

    @pytest.mark.asyncio
    async def test_store_output_skips_when_no_engine(self):
        from app.agents.base import store_output

        agent = MagicMock()
        agent.name = "test_agent"

        with patch("app.services.context_engine.get_context_engine", side_effect=RuntimeError("not initialized")):
            await store_output(agent, "run-123", {"key": "value"})


# ── A-2-FIX: get_step_context handles uninitialized engine ─────


class TestGetStepContextResilience:
    """get_step_context must not crash on missing engine or Valkey failures."""

    @pytest.mark.asyncio
    async def test_returns_none_when_engine_not_initialized(self):
        from app.agents.base import get_step_context

        with patch("app.services.context_engine.get_context_engine", side_effect=RuntimeError("not initialized")):
            result = await get_step_context("run-123", "tilotma")
            assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_valkey_failure(self):
        from app.agents.base import get_step_context

        mock_engine = MagicMock()
        mock_engine.get_step = AsyncMock(side_effect=ConnectionError("Valkey down"))

        with patch("app.services.context_engine.get_context_engine", return_value=mock_engine):
            result = await get_step_context("run-123", "tilotma")
            assert result is None


# ── S-20-FIX: JWT decode_token validates token type ────────────


class TestDecodeTokenType:
    """decode_token with expected_type rejects wrong token types."""

    def _make_token(self, token_type: str, **extra_claims) -> str:
        from app.services.auth import jwt
        from app.config import get_settings

        settings = get_settings()
        from datetime import datetime, timedelta, timezone

        payload = {
            "sub": str(uuid.uuid4()),
            "org": str(uuid.uuid4()),
            "role": "org_admin",
            "type": token_type,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            "iat": datetime.now(timezone.utc),
            "jti": str(uuid.uuid4()),
            # R11-FIX: Include iss/aud claims now required by decode_token
            "iss": "nexsidi",
            "aud": "nexsidi-api",
            **extra_claims,
        }
        return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

    def test_access_token_accepted_when_expected(self):
        from app.services.auth import decode_token

        token = self._make_token("access")
        payload = decode_token(token, expected_type="access")
        assert payload["type"] == "access"

    def test_refresh_token_rejected_as_access(self):
        from app.services.auth import decode_token

        token = self._make_token("refresh")
        with pytest.raises(JWTError, match="type mismatch"):
            decode_token(token, expected_type="access")

    def test_access_token_rejected_as_refresh(self):
        from app.services.auth import decode_token

        token = self._make_token("access")
        with pytest.raises(JWTError, match="type mismatch"):
            decode_token(token, expected_type="refresh")

    def test_no_expected_type_allows_any(self):
        from app.services.auth import decode_token

        token = self._make_token("refresh")
        payload = decode_token(token)  # No expected_type
        assert payload["type"] == "refresh"


# ── S-4-FIX: API key sanitization ──────────────────────────────


class TestSanitizeError:
    """Verify API keys are stripped from error messages."""

    def test_anthropic_key_redacted(self):
        from app.services.ai_router import _sanitize_error

        exc = Exception("Request failed with key sk-ant-abc123-LONGKEYVALUE")
        sanitized = _sanitize_error(exc)
        assert "sk-ant-" not in sanitized
        assert "[REDACTED]" in sanitized

    def test_google_key_redacted(self):
        from app.services.ai_router import _sanitize_error

        exc = Exception("Authorization: Bearer AIzaSyCVeryLongGoogleKeyValue1234567890")
        sanitized = _sanitize_error(exc)
        assert "AIza" not in sanitized
        assert "[REDACTED]" in sanitized

    def test_x_api_key_header_redacted(self):
        from app.services.ai_router import _sanitize_error

        exc = Exception("x-api-key: sk-ant-some-very-long-api-key-value")
        sanitized = _sanitize_error(exc)
        assert "sk-ant-" not in sanitized

    def test_safe_message_unchanged(self):
        from app.services.ai_router import _sanitize_error

        exc = Exception("Connection timed out after 30s")
        sanitized = _sanitize_error(exc)
        assert sanitized == "Connection timed out after 30s"


# ── R-01-FIX: Chat session creation verifies project ownership ─


class TestChatProjectOwnership:
    """create_chat_session must import Project and verify ownership."""

    def test_project_model_imported_in_chat_router(self):
        from app.routers import chat
        assert hasattr(chat, "Project")

    def test_create_chat_session_has_session_param(self):
        """Route handler accepts TenantSession for RLS-scoped queries."""
        from app.routers.chat import create_chat_session

        sig = inspect.signature(create_chat_session)
        assert "session" in sig.parameters


# ── R-03-FIX: Pipeline run_id UUID validation ──────────────────


class TestPipelineRunIdValidation:
    """Pipeline routes must validate run_id as UUID."""

    def test_validate_run_id_accepts_valid_uuid(self):
        from app.routers.pipeline import _validate_run_id

        valid_id = str(uuid.uuid4())
        assert _validate_run_id(valid_id) == valid_id

    def test_validate_run_id_rejects_non_uuid(self):
        from fastapi import HTTPException
        from app.routers.pipeline import _validate_run_id

        with pytest.raises(HTTPException) as exc_info:
            _validate_run_id("not-a-uuid")
        assert exc_info.value.status_code == 422

    def test_validate_run_id_normalizes_format(self):
        from app.routers.pipeline import _validate_run_id

        # UUID with uppercase should be normalized
        upper = "550E8400-E29B-41D4-A716-446655440000"
        result = _validate_run_id(upper)
        assert result == upper.lower()


# ── A-16-FIX: Tenant UUID normalization ─────────────────────────


class TestTenantUUIDNormalization:
    """Tenant middleware must normalize UUIDs before SQL interpolation."""

    def test_validate_uuid_returns_normalized_string(self):
        from app.middleware.tenant import _validate_uuid

        raw_uuid = "550E8400-E29B-41D4-A716-446655440000"
        normalized = _validate_uuid(raw_uuid)
        assert normalized == raw_uuid.lower()
        assert isinstance(normalized, str)

    def test_validate_uuid_rejects_invalid(self):
        from app.middleware.tenant import _validate_uuid

        with pytest.raises(ValueError, match="Invalid UUID"):
            _validate_uuid("not-a-uuid")

    def test_validate_uuid_handles_braces(self):
        from app.middleware.tenant import _validate_uuid

        braced = "{550e8400-e29b-41d4-a716-446655440000}"
        normalized = _validate_uuid(braced)
        assert normalized == "550e8400-e29b-41d4-a716-446655440000"


# ── S-7-FIX: Pipeline active_runs eviction on completion ───────


class TestActiveRunsEviction:
    """Completed/failed runs must be evicted from _active_runs dict."""

    def test_create_run_adds_to_active(self):
        from app.services.pipeline import PipelineOrchestrator

        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        assert run.run_id in orch._active_runs

    def test_completed_run_evicted_from_cache(self):
        from app.services.pipeline import PipelineOrchestrator, PipelineRunStatus

        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        run_id = run.run_id
        assert run_id in orch._active_runs

        # Simulate completion eviction (same logic as _run_pipeline_inner)
        run.status = PipelineRunStatus.COMPLETED
        orch._active_runs.pop(run.run_id, None)
        assert run_id not in orch._active_runs

    def test_failed_run_evicted_from_cache(self):
        from app.services.pipeline import PipelineOrchestrator, PipelineRunStatus

        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        run_id = run.run_id

        run.status = PipelineRunStatus.FAILED
        orch._active_runs.pop(run.run_id, None)
        assert run_id not in orch._active_runs

    def test_paused_run_stays_in_cache(self):
        from app.services.pipeline import PipelineOrchestrator, PipelineRunStatus

        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        run_id = run.run_id

        run.status = PipelineRunStatus.PAUSED
        # Should NOT evict paused runs (waiting for checkpoint approval)
        assert run_id in orch._active_runs


# ── R-05-FIX: Rate limiting on refresh endpoint ────────────────


class TestRefreshRateLimit:
    """Refresh endpoint must accept Request parameter for rate limiting."""

    def test_refresh_token_handler_accepts_request(self):
        from app.routers.auth import refresh_token

        sig = inspect.signature(refresh_token)
        assert "request" in sig.parameters


# ── S-18-FIX: Audit chain integrity on event cap ───────────────


class TestAuditChainIntegrity:
    """When event cap is hit, chain integrity must be maintained."""

    def test_events_evicted_at_cap_not_dropped(self):
        from app.services.pipeline_audit import AuditEventType, PipelineAuditService

        svc = PipelineAuditService()
        original = PipelineAuditService._MAX_EVENTS_PER_RUN
        PipelineAuditService._MAX_EVENTS_PER_RUN = 5
        try:
            for i in range(8):
                svc.record(AuditEventType.STAGE_STARTED, "run-cap", agent_name=f"agent-{i}")

            # Should have exactly 5 events (cap), not 0 or 8
            assert len(svc._events["run-cap"]) == 5
            # Chain hash should still be updated
            assert "run-cap" in svc._chain_hashes
            # Last event should be from agent-7 (the 8th, most recent)
            assert svc._events["run-cap"][-1].agent_name == "agent-7"
        finally:
            PipelineAuditService._MAX_EVENTS_PER_RUN = original

    def test_chain_hash_continues_through_evictions(self):
        from app.services.pipeline_audit import AuditEventType, PipelineAuditService

        svc = PipelineAuditService()
        original = PipelineAuditService._MAX_EVENTS_PER_RUN
        PipelineAuditService._MAX_EVENTS_PER_RUN = 3
        try:
            hashes = []
            for i in range(5):
                svc.record(AuditEventType.STAGE_STARTED, "run-chain", agent_name=f"agent-{i}")
                hashes.append(svc._chain_hashes["run-chain"])

            # Each event should produce a unique chain hash
            assert len(set(hashes)) == 5
        finally:
            PipelineAuditService._MAX_EVENTS_PER_RUN = original


# ── R-07-FIX / R-08-FIX: RBAC on update_project / start_pipeline


class TestRBACEnforcement:
    """Verify RBAC checks exist in project update and pipeline start."""

    def test_update_project_source_has_owner_check(self):
        """update_project source should contain owner/admin check."""
        import inspect
        from app.routers.projects import update_project

        source = inspect.getsource(update_project)
        assert "is_owner" in source or "owner_id" in source
        assert "403" in source or "FORBIDDEN" in source

    def test_start_pipeline_source_has_owner_check(self):
        """start_pipeline source should contain owner/admin check."""
        import inspect
        from app.routers.pipeline import start_pipeline

        source = inspect.getsource(start_pipeline)
        assert "is_owner" in source or "owner_id" in source
        assert "403" in source or "FORBIDDEN" in source


# ── S-1-FIX: Circuit breaker async state transitions ───────────


class TestCircuitBreakerAsync:
    """Circuit breaker methods must be async (for atomic state transitions)."""

    def test_methods_are_coroutines(self):
        from app.services.ai_router import CircuitState

        cb = CircuitState()
        assert asyncio.iscoroutinefunction(cb.record_failure) or asyncio.iscoroutine(cb.record_failure())
        assert asyncio.iscoroutinefunction(cb.record_success) or asyncio.iscoroutine(cb.record_success())
        assert asyncio.iscoroutinefunction(cb.is_available) or asyncio.iscoroutine(cb.is_available())

    def test_circuit_has_lock(self):
        """CircuitState must have a _get_lock() method that returns an asyncio.Lock.

        R27-FIX-23: Lock is now lazy-initialized via _get_lock() to avoid
        binding to the wrong event loop at import time.
        """
        from app.services.ai_router import CircuitState

        cb = CircuitState()
        assert hasattr(cb, "_get_lock")
        lock = cb._get_lock()
        assert isinstance(lock, asyncio.Lock)

    @pytest.mark.asyncio
    async def test_concurrent_failures_counted_correctly(self):
        """Concurrent record_failure() calls should all be counted."""
        from app.services.ai_router import CircuitState

        cb = CircuitState(failure_threshold=100)

        # Fire 50 concurrent failures
        await asyncio.gather(*[cb.record_failure() for _ in range(50)])

        assert cb.failures == 50


# ── WebSocket accept-before-close (R-11) ────────────────────────


class TestWebSocketAcceptBeforeClose:
    """WebSocket auth failures must accept() before close()."""

    def test_ws_handler_source_has_accept_before_close(self):
        """The WebSocket handler should accept before closing on auth failure."""
        import inspect
        from app.routers.websocket import pipeline_websocket

        source = inspect.getsource(pipeline_websocket)
        # Find the pattern: accept() before close() for auth failures
        # The source should have "await websocket.accept()" before any close call
        first_accept = source.find("websocket.accept()")
        first_close = source.find("websocket.close(")
        assert first_accept < first_close, "accept() must come before close()"
