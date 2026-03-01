"""Tests for Round 7 fixes: hardening from brutal re-review cycle 7.

Covers:
- KEY-FIX: _has_errors_to_fix uses correct fixer output keys
- CTX-FIX: Parallel agents store individual outputs in run.context
- VERTEX-FIX: _get_vertex_token is async (runs blocking refresh in thread pool)
- RETRY-CAP-FIX: Retry-After header capped at 120 seconds
- CACHE-LOCK-FIX: GeminiCacheManager has asyncio.Lock
- RLS-READ-FIX: find_run_by_id accepts organization_id filter
- CHECKPOINT-FIX: Checkpoint handler respects checkpoint_approved flag
- RATE-TOCTOU-FIX: Notification rate check + increment is atomic
- FROZENSET-FIX: Prompt engine handles empty frozenset correctly
"""

from __future__ import annotations

import asyncio
import inspect
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.base import AgentResult, AgentStatus
from app.services.pipeline import (
    ExecutionMode,
    PipelineOrchestrator,
    PipelineRun,
    PipelineRunStatus,
    PipelineStage,
    StepResult,
)


# ── KEY-FIX: _has_errors_to_fix uses correct fixer keys ────────


class TestFixerKeyAlignment:
    """_has_errors_to_fix must check the actual fixer output keys."""

    def test_errors_remaining_and_fixed_triggers_retest(self):
        """When fixer has remaining errors AND applied fixes, re-test."""
        orch = PipelineOrchestrator()
        step = StepResult(
            stage=PipelineStage.FIXING,
            agent_name="fixer",
            result=AgentResult(
                agent_name="fixer",
                status=AgentStatus.COMPLETED,
                output={"errors_remaining": 2, "errors_fixed": 3},
            ),
        )
        assert orch._has_errors_to_fix(step) is True

    def test_no_remaining_errors_no_retest(self):
        """When all errors are fixed (none remaining), no re-test."""
        orch = PipelineOrchestrator()
        step = StepResult(
            stage=PipelineStage.FIXING,
            agent_name="fixer",
            result=AgentResult(
                agent_name="fixer",
                status=AgentStatus.COMPLETED,
                output={"errors_remaining": 0, "errors_fixed": 5},
            ),
        )
        assert orch._has_errors_to_fix(step) is False

    def test_remaining_but_no_fixes_no_retest(self):
        """If errors remain but zero were fixed, don't loop forever."""
        orch = PipelineOrchestrator()
        step = StepResult(
            stage=PipelineStage.FIXING,
            agent_name="fixer",
            result=AgentResult(
                agent_name="fixer",
                status=AgentStatus.COMPLETED,
                output={"errors_remaining": 3, "errors_fixed": 0},
            ),
        )
        assert orch._has_errors_to_fix(step) is False

    def test_old_keys_do_not_trigger(self):
        """Old keys (errors_found, fixes_applied) must NOT trigger re-test."""
        orch = PipelineOrchestrator()
        step = StepResult(
            stage=PipelineStage.FIXING,
            agent_name="fixer",
            result=AgentResult(
                agent_name="fixer",
                status=AgentStatus.COMPLETED,
                output={"errors_found": 3, "fixes_applied": 3, "has_errors": True},
            ),
        )
        # Old keys don't match the new implementation
        assert orch._has_errors_to_fix(step) is False


# ── CTX-FIX: Parallel agents store individual outputs ──────────


class TestParallelContextStorage:
    """Parallel agents must store output under individual keys in context."""

    @pytest.mark.asyncio
    async def test_parallel_agents_store_individual_context(self):
        """After parallel execution, each agent's output is in run.context."""

        def mock_agent(name):
            agent = MagicMock()
            agent.name = name
            agent.run = AsyncMock(return_value=AgentResult(
                agent_name=name,
                status=AgentStatus.COMPLETED,
                output={f"{name}_data": True},
            ))
            return agent

        with patch("app.services.pipeline.get_agent", side_effect=mock_agent):
            orch = PipelineOrchestrator()
            run = PipelineRun(
                project_id="p1",
                organization_id="o1",
                user_id="u1",
                current_stage=PipelineStage.QUALITY_REVIEW,
            )
            step = await orch._execute_parallel(
                run, PipelineStage.QUALITY_REVIEW, ["karan", "navya", "deepika"]
            )

        # CTX-FIX: Each agent's output should be under its own key
        assert "karan" in run.context
        assert "navya" in run.context
        assert "deepika" in run.context
        assert run.context["karan"] == {"karan_data": True}
        assert run.context["navya"] == {"navya_data": True}


# ── VERTEX-FIX: _get_vertex_token is async ─────────────────────


class TestVertexTokenAsync:
    """_get_vertex_token must be async to avoid blocking event loop."""

    def test_get_vertex_token_is_coroutine(self):
        from app.services.ai_router import AIRouter

        assert asyncio.iscoroutinefunction(AIRouter._get_vertex_token)

    def test_get_vertex_token_source_uses_to_thread(self):
        """Must use asyncio.to_thread for the blocking token refresh."""
        from app.services.ai_router import AIRouter

        source = inspect.getsource(AIRouter._get_vertex_token)
        assert "asyncio.to_thread" in source


# ── RETRY-CAP-FIX: Retry-After capped at 120s ─────────────────


class TestRetryAfterCap:
    """Retry-After header must be capped to prevent unbounded sleep."""

    def test_retry_logic_source_has_cap(self):
        """The retry loop should cap the delay at 120 seconds."""
        from app.services.ai_router import AIRouter

        source = inspect.getsource(AIRouter.call)
        assert "120.0" in source  # Cap value
        assert "min(" in source  # Uses min() to cap


# ── CACHE-LOCK-FIX: GeminiCacheManager has lock ───────────────


class TestGeminiCacheLock:
    """GeminiCacheManager must have a lock to prevent duplicate API calls."""

    def test_has_lock(self):
        from app.services.ai_router import GeminiCacheManager

        mgr = GeminiCacheManager()
        # R17-FIX: Renamed _lock → _global_lock (fast dict access only).
        # Per-hash locks now in _pending dict for concurrent I/O.
        assert hasattr(mgr, "_global_lock")
        assert isinstance(mgr._global_lock, asyncio.Lock)


# ── RLS-READ-FIX: find_run_by_id accepts org filter ───────────


class TestFindRunByIdOrgFilter:
    """find_run_by_id must support organization_id for tenant filtering."""

    def test_signature_has_organization_id(self):
        from app.services.pipeline import PipelinePersistence

        sig = inspect.signature(PipelinePersistence.find_run_by_id)
        assert "organization_id" in sig.parameters

    def test_load_run_metadata_passes_org_id(self):
        """load_run_metadata must forward organization_id to find_run_by_id."""
        from app.services.pipeline import PipelineOrchestrator

        source = inspect.getsource(PipelineOrchestrator.load_run_metadata)
        assert "organization_id" in source

    def test_resume_run_has_org_id_param(self):
        from app.services.pipeline import PipelineOrchestrator

        sig = inspect.signature(PipelineOrchestrator.resume_run)
        assert "organization_id" in sig.parameters


# ── CHECKPOINT-FIX: Checkpoint respects approved flag ──────────


class TestCheckpointApprovedFlag:
    """Checkpoint handler must skip pause when checkpoint_approved is True."""

    @pytest.mark.asyncio
    async def test_checkpoint_skips_when_approved(self):
        """If checkpoint_approved=True, handler should skip the pause."""
        orch = PipelineOrchestrator()
        run = PipelineRun(
            project_id="p1",
            organization_id="o1",
            user_id="u1",
            current_stage=PipelineStage.CHECKPOINT_DESIGN,
            execution_mode=ExecutionMode.CHECKPOINT,
            checkpoint_approved=True,  # Pre-approved
        )

        step = await orch._handle_checkpoint(run, PipelineStage.CHECKPOINT_DESIGN)

        # Should skip, not pause
        assert step.skipped is True
        assert run.status != PipelineRunStatus.PAUSED
        # Flag should be reset for next checkpoint
        assert run.checkpoint_approved is False

    @pytest.mark.asyncio
    async def test_checkpoint_pauses_when_not_approved(self):
        """Normal checkpoint (not pre-approved) should pause."""
        orch = PipelineOrchestrator()
        run = PipelineRun(
            project_id="p1",
            organization_id="o1",
            user_id="u1",
            current_stage=PipelineStage.CHECKPOINT_DESIGN,
            execution_mode=ExecutionMode.CHECKPOINT,
            checkpoint_approved=False,
        )

        step = await orch._handle_checkpoint(run, PipelineStage.CHECKPOINT_DESIGN)

        assert run.status == PipelineRunStatus.PAUSED
        assert step.result is not None
        assert step.result.status == AgentStatus.WAITING_USER


# ── RATE-TOCTOU-FIX: Atomic rate check + increment ────────────


class TestNotificationRateAtomic:
    """Notification rate limit must be atomic (check + increment together)."""

    def test_has_check_and_increment_method(self):
        from app.services.notification import NotificationService

        svc = NotificationService()
        assert hasattr(svc, "_check_and_increment_rate")

    def test_atomic_rate_increments_on_check(self):
        """_check_and_increment_rate should increment counter on success."""
        from app.services.notification import NotificationService

        svc = NotificationService()
        assert svc._check_and_increment_rate("user-1") is True
        # Counter should be 1 now
        assert len(svc._rate_windows.get("user-1", [])) == 1

    def test_atomic_rate_rejects_at_limit(self):
        """At rate limit, _check_and_increment_rate returns False."""
        from app.services.notification import NotificationService

        svc = NotificationService()
        svc._max_per_hour = 2
        assert svc._check_and_increment_rate("user-1") is True
        assert svc._check_and_increment_rate("user-1") is True
        assert svc._check_and_increment_rate("user-1") is False
        # Should not have incremented past the limit
        assert len(svc._rate_windows["user-1"]) == 2


# ── FROZENSET-FIX: Empty frozenset handled correctly ──────────


class TestFrozensetFalsy:
    """Prompt engine must distinguish empty frozenset from None."""

    def test_prompt_engine_source_uses_is_not_none(self):
        """Must use 'is not None' for allowed_variables check."""
        from app.services.prompt_engine import PromptEngine

        source = inspect.getsource(PromptEngine.render)
        assert "is not None" in source
