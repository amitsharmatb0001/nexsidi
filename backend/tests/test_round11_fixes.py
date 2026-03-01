"""Tests for Round 11 fixes: hardening from brutal re-review cycle 11.

Covers:
- CREDENTIAL-LEAK: run_agent, call_ai_with_tools, fixer _attempt_fix sanitize exceptions
- PARALLEL-CONTEXT: _execute_parallel passes context copies to parallel agents
- EXECUTE-AGENT-CRASH: _execute_agent wraps agent.run() in try/except
- RUN-ID-SYNC: save_run syncs run_id with db_run_id after first save
- FIND-RUN-UUID: find_run_by_id handles invalid UUIDs gracefully
- SANITIZE-YA29: _sanitize_error catches Google OAuth ya29.* tokens
- JWT-ISS-AUD: Tokens include iss/aud claims; decode validates them
- HEALTH-CHECK: readiness check returns generic error, no exception class names
- UNKNOWN-IP: _client_ip logs warning when request.client is None
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.base import AgentResult, AgentStatus
from app.services.pipeline import (
    PipelineOrchestrator,
    PipelineRunStatus,
    PipelineStage,
    StepResult,
)


# ── CREDENTIAL-LEAK: Sanitized exceptions in run_agent ──────────


class TestCredentialLeakRunAgent:
    """run_agent must sanitize exceptions before logging/returning."""

    def test_run_agent_uses_sanitize_error(self):
        """run_agent must call _sanitize_error on exceptions."""
        from app.agents.base import run_agent

        source = inspect.getsource(run_agent)
        assert "_sanitize_error" in source
        # Must NOT have raw str(exc) in the error field
        assert "error=str(exc)" not in source


class TestCredentialLeakToolHandler:
    """call_ai_with_tools must sanitize tool handler exceptions."""

    def test_tool_handler_uses_sanitize_error(self):
        from app.agents.base import call_ai_with_tools

        source = inspect.getsource(call_ai_with_tools)
        assert "_sanitize_error" in source


class TestCredentialLeakFixer:
    """Fixer _attempt_fix must sanitize exceptions in output."""

    def test_attempt_fix_uses_sanitize_error(self):
        from app.agents.fixer import Fixer

        source = inspect.getsource(Fixer._attempt_fix)
        assert "_sanitize_error" in source


# ── PARALLEL-CONTEXT: Parallel agents get context copies ─────────


class TestParallelContextIsolation:
    """_execute_parallel must pass shallow copies to each agent."""

    @pytest.mark.asyncio
    async def test_parallel_agents_get_context_copy(self):
        """Each parallel agent should receive dict(run.context), not the original."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        run.context["shared_key"] = "original_value"

        # Track what context each agent receives
        received_contexts: list[dict] = []

        async def capture_context_agent(run_id, context):
            received_contexts.append(context)
            # Mutate to prove isolation
            context["shared_key"] = "mutated"
            return AgentResult(
                agent_name="test", status=AgentStatus.COMPLETED, output={"ok": True}
            )

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(side_effect=capture_context_agent)

        with patch("app.services.pipeline.get_agent", return_value=mock_agent):
            await orch._execute_parallel(
                run, PipelineStage.QUALITY_REVIEW, ["karan", "navya"]
            )

        # Each agent should have gotten a COPY (id differs from original)
        for ctx in received_contexts:
            assert ctx is not run.context, "Agent received the original context, not a copy"

        # Original context should be unmodified by agent mutations
        assert run.context["shared_key"] == "original_value"


# ── EXECUTE-AGENT-CRASH: _execute_agent catches exceptions ──────


class TestExecuteAgentCrash:
    """_execute_agent must catch exceptions and return FAILED StepResult."""

    @pytest.mark.asyncio
    async def test_agent_crash_returns_failed_step(self):
        """When agent.run() raises, step.result should be FAILED (not propagate)."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(side_effect=RuntimeError("agent exploded"))

        with patch("app.services.pipeline.get_agent", return_value=mock_agent):
            step = await orch._execute_agent(
                run, PipelineStage.REQUIREMENTS, "tilotma"
            )

        assert step.result is not None
        assert step.result.status == AgentStatus.FAILED
        assert "agent exploded" in step.result.error

    def test_execute_agent_has_try_except(self):
        """Source must have try/except around agent.run()."""
        source = inspect.getsource(PipelineOrchestrator._execute_agent)
        assert "except Exception" in source
        assert "_sanitize_error" in source


# ── RUN-ID-SYNC: save_run syncs run_id with db_run_id ───────────


class TestRunIdSync:
    """save_run must set run.run_id = db_run_id after first save."""

    def test_save_run_syncs_run_id(self):
        """Source must assign run.run_id = str(db_run.id) after create."""
        from app.services.pipeline import PipelinePersistence

        source = inspect.getsource(PipelinePersistence.save_run)
        assert "run.run_id = str(db_run.id)" in source


# ── FIND-RUN-UUID: Invalid UUID returns None ─────────────────────


class TestFindRunUuidValidation:
    """find_run_by_id must handle invalid UUIDs gracefully."""

    def test_source_has_uuid_validation(self):
        from app.services.pipeline import PipelinePersistence

        source = inspect.getsource(PipelinePersistence.find_run_by_id)
        assert "except (ValueError, AttributeError)" in source


# ── SANITIZE-YA29: _sanitize_error catches Google OAuth tokens ───


class TestSanitizeYa29:
    """_sanitize_error must redact ya29.* Google OAuth tokens."""

    def test_ya29_token_redacted(self):
        from app.services.ai_router import _sanitize_error

        exc = Exception(
            "Request failed: Authorization: Bearer ya29.a0AfH6SMBxyz123_LONGTOKENVALUE.extra"
        )
        result = _sanitize_error(exc)
        assert "ya29." not in result
        assert "[REDACTED]" in result

    def test_vertex_token_redacted(self):
        """Vertex AI uses ya29 tokens in error messages."""
        from app.services.ai_router import _sanitize_error

        msg = "403 Forbidden: token=ya29.c.b0AXv0zTPabcdefghijklmnop"
        result = _sanitize_error(Exception(msg))
        assert "ya29." not in result

    def test_normal_text_unchanged(self):
        """Normal error messages should not be altered."""
        from app.services.ai_router import _sanitize_error

        exc = Exception("Connection timeout after 30s")
        result = _sanitize_error(exc)
        assert result == "Connection timeout after 30s"


# ── JWT-ISS-AUD: Tokens include iss/aud; decode validates them ───


class TestJwtIssAud:
    """JWT tokens must include iss/aud claims for cross-env protection."""

    def test_access_token_has_iss_aud(self):
        from app.services.auth import create_access_token, decode_token

        token = create_access_token(uuid.uuid4(), uuid.uuid4(), "member")
        payload = decode_token(token)
        assert payload["iss"] == "nexsidi"
        assert payload["aud"] == "nexsidi-api"

    def test_refresh_token_has_iss_aud(self):
        from app.services.auth import create_refresh_token, decode_token

        token = create_refresh_token(uuid.uuid4(), uuid.uuid4())
        payload = decode_token(token)
        assert payload["iss"] == "nexsidi"
        assert payload["aud"] == "nexsidi-api"

    def test_decode_rejects_wrong_issuer(self):
        """Token from another service (wrong iss) must be rejected."""
        from jose import JWTError, jwt
        from app.config import get_settings
        from app.services.auth import decode_token
        from datetime import datetime, timedelta, timezone

        settings = get_settings()
        payload = {
            "sub": str(uuid.uuid4()),
            "org": str(uuid.uuid4()),
            "type": "access",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            "iat": datetime.now(timezone.utc),
            "jti": str(uuid.uuid4()),
            "iss": "other-service",
            "aud": "nexsidi-api",
        }
        token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
        with pytest.raises(JWTError):
            decode_token(token)

    def test_decode_rejects_wrong_audience(self):
        """Token for another audience must be rejected."""
        from jose import JWTError, jwt
        from app.config import get_settings
        from app.services.auth import decode_token
        from datetime import datetime, timedelta, timezone

        settings = get_settings()
        payload = {
            "sub": str(uuid.uuid4()),
            "org": str(uuid.uuid4()),
            "type": "access",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            "iat": datetime.now(timezone.utc),
            "jti": str(uuid.uuid4()),
            "iss": "nexsidi",
            "aud": "other-api",
        }
        token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
        with pytest.raises(JWTError):
            decode_token(token)


# ── HEALTH-CHECK: Readiness returns generic error ────────────────


class TestHealthCheckLeak:
    """Readiness endpoint must not leak exception class names in health response."""

    def test_readiness_does_not_expose_exc_class_in_checks(self):
        """The readiness check must return generic 'error', not type(exc).__name__."""
        from app.main import create_app

        source = inspect.getsource(create_app)
        # The old pattern was: checks["database"] = f"error: {type(exc).__name__}"
        # The new pattern is: checks["database"] = "error"
        # Verify the specific readiness response line is fixed
        assert 'checks["database"] = "error"' in source


# ── UNKNOWN-IP: _client_ip logs warning ──────────────────────────


class TestUnknownIpWarning:
    """_client_ip must log a warning when request.client is None."""

    def test_client_ip_logs_warning_on_none(self):
        from app.routers.auth import _client_ip

        source = inspect.getsource(_client_ip)
        assert "client_ip_missing" in source
        assert 'logger.warning' in source


# ── PARALLEL-EXCEPTION-SANITIZE: Parallel agent exceptions ───────


class TestParallelExceptionSanitize:
    """Parallel agent exception messages must be sanitized."""

    def test_parallel_exception_uses_sanitize_error(self):
        source = inspect.getsource(PipelineOrchestrator._execute_parallel)
        assert "_sanitize_error" in source
        # Must NOT have raw str(res) in the combined_output
        assert 'combined_output[name] = {"error": str(res)}' not in source
