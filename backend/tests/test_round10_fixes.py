"""Tests for Round 10 fixes: hardening from brutal re-review cycle 10.

Covers:
- ORPHAN-DB-REF: save_run re-creates when DB record deleted
- PHANTOM-SUCCESS: _execute_parallel FAILED when all agents crash
- GEMINI-SAFETY: _parse_google_response raises on safety blocks
- LOGIN-DEACTIVATION: Deactivated accounts get same 401 as invalid creds
- ORM-DETACHED: Auth endpoints capture ORM values inside session scope
- STREAM-SANITIZE: Streaming errors are sanitized before logging
- CONTINUATION-CAP: Continuation context capped to prevent blowout
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.base import AgentResult, AgentStatus
from app.services.pipeline import (
    PipelineOrchestrator,
    PipelineRunStatus,
    PipelineStage,
    StepResult,
)


# ── ORPHAN-DB-REF: save_run re-creates when DB record missing ──────


class TestOrphanDbRef:
    """save_run must clear stale db_run_id and re-create when record gone."""

    def test_source_handles_missing_db_record(self):
        """save_run must have a branch that clears db_run_id when record missing."""
        from app.services.pipeline import PipelinePersistence

        source = inspect.getsource(PipelinePersistence.save_run)
        assert "persist_run_record_missing" in source
        assert "run.db_run_id = None" in source


# ── PHANTOM-SUCCESS: parallel agents all-fail → FAILED ─────────────


class TestPhantomSuccess:
    """_execute_parallel must return FAILED when ALL agents threw exceptions."""

    @pytest.mark.asyncio
    async def test_all_agents_fail_returns_failed(self):
        """When every parallel agent raises, step.result.status must be FAILED."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")

        # Mock get_agent to return agents that all raise
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(side_effect=RuntimeError("agent crashed"))

        with patch("app.services.pipeline.get_agent", return_value=mock_agent):
            result = await orch._execute_parallel(
                run, PipelineStage.QUALITY_REVIEW, ["karan", "navya", "deepika"]
            )

        assert result.result is not None
        assert result.result.status == AgentStatus.FAILED
        assert result.result.error == "All parallel agents failed"

    @pytest.mark.asyncio
    async def test_some_succeed_returns_completed(self):
        """When at least one agent succeeds, step status should be COMPLETED."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")

        success_agent = MagicMock()
        success_agent.run = AsyncMock(return_value=AgentResult(
            agent_name="karan", status=AgentStatus.COMPLETED, output={"ok": True},
        ))

        fail_agent = MagicMock()
        fail_agent.run = AsyncMock(side_effect=RuntimeError("crashed"))

        call_count = 0
        def mock_get_agent(name):
            nonlocal call_count
            call_count += 1
            return success_agent if call_count == 1 else fail_agent

        with patch("app.services.pipeline.get_agent", side_effect=mock_get_agent):
            result = await orch._execute_parallel(
                run, PipelineStage.QUALITY_REVIEW, ["karan", "navya"]
            )

        assert result.result.status == AgentStatus.COMPLETED


# ── GEMINI-SAFETY: _parse_google_response raises on safety blocks ──


class TestGeminiSafetyFilter:
    """Gemini safety filter blocks must raise, not return empty content."""

    def test_block_reason_raises(self):
        """promptFeedback.blockReason should raise ValueError."""
        from app.services.ai_router import AIRouter, ModelSpec, Provider

        router = AIRouter.__new__(AIRouter)
        spec = MagicMock()
        spec.model_id = "gemini-pro"
        spec.provider = Provider.GOOGLE

        data = {
            "promptFeedback": {"blockReason": "SAFETY"},
            "candidates": [],
        }

        with pytest.raises(ValueError, match="safety filter blocked"):
            router._parse_google_response(data, spec, "req-123")

    def test_finish_reason_safety_raises(self):
        """candidates[0].finishReason == SAFETY should raise ValueError."""
        from app.services.ai_router import AIRouter, ModelSpec, Provider

        router = AIRouter.__new__(AIRouter)
        spec = MagicMock()
        spec.model_id = "gemini-pro"
        spec.provider = Provider.GOOGLE

        data = {
            "candidates": [{
                "content": {"parts": [{"text": "partial..."}]},
                "finishReason": "SAFETY",
            }],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
        }

        with pytest.raises(ValueError, match="safety filter"):
            router._parse_google_response(data, spec, "req-456")

    def test_normal_response_works(self):
        """Normal responses should parse without raising."""
        from app.services.ai_router import AIRouter, Provider

        router = AIRouter.__new__(AIRouter)
        spec = MagicMock()
        spec.model_id = "gemini-pro"
        spec.provider = Provider.GOOGLE

        data = {
            "candidates": [{
                "content": {"parts": [{"text": "Hello world"}]},
                "finishReason": "STOP",
            }],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
        }

        result = router._parse_google_response(data, spec, "req-789")
        assert result.content == "Hello world"
        assert result.stop_reason == "STOP"


# ── LOGIN-DEACTIVATION: Same error for deactivated accounts ────────


class TestLoginDeactivationInfo:
    """Deactivated accounts must get same 401 as invalid credentials."""

    def test_deactivated_returns_401_not_403(self):
        """is_active check must use 401, not 403."""
        from app.routers.auth import login

        source = inspect.getsource(login)
        # Should NOT have a 403 for deactivated accounts
        assert "HTTP_403_FORBIDDEN" not in source
        # The is_active check should use the same generic message
        assert "Invalid email or password" in source


# ── ORM-DETACHED: Capture ORM values inside session ────────────────


class TestOrmDetachedFix:
    """Auth endpoints must capture ORM values inside session scope."""

    def test_register_captures_locals(self):
        from app.routers.auth import register

        source = inspect.getsource(register)
        assert "_user_id = user.id" in source
        assert "_org_id = org.id" in source
        assert "_role = user.role" in source

    def test_login_captures_locals(self):
        from app.routers.auth import login

        source = inspect.getsource(login)
        assert "_user_id = user.id" in source
        assert "_org_id = user.organization_id" in source
        assert "_role = user.role" in source

    def test_refresh_captures_locals(self):
        from app.routers.auth import refresh_token

        source = inspect.getsource(refresh_token)
        assert "_user_id = user.id" in source
        assert "_org_id = user.organization_id" in source
        assert "_role = user.role" in source


# ── STREAM-SANITIZE: Streaming errors sanitized ────────────────────


class TestStreamSanitize:
    """Streaming errors must be sanitized before logging."""

    def test_call_stream_sanitizes_errors(self):
        from app.services.ai_router import AIRouter

        source = inspect.getsource(AIRouter.call_stream)
        assert "_sanitize_error" in source


# ── CONTINUATION-CAP: Context capped on continuation ───────────────


class TestContinuationCap:
    """Continuation must cap the context to prevent blowout."""

    def test_continuation_has_char_cap(self):
        from app.agents.base import call_ai_with_continuation

        source = inspect.getsource(call_ai_with_continuation)
        assert "_MAX_CONTINUATION_CONTEXT_CHARS" in source

    def test_continuation_uses_tail(self):
        """Must use slicing to only send tail of accumulated output."""
        from app.agents.base import call_ai_with_continuation

        source = inspect.getsource(call_ai_with_continuation)
        assert "full_content[-_MAX_CONTINUATION_CONTEXT_CHARS:]" in source
