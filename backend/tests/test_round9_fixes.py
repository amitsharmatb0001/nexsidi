"""Tests for Round 9 fixes: hardening from brutal re-review cycle 9.

Covers:
- APPROVE-DEADLOCK-FIX: approve_checkpoint leaves PAUSED, resume_run checks flag
- STEP-BY-STEP-REPLAY-FIX: Step-by-step mode advances before pausing
- CONTINUATION-MSG-FIX: call_ai_with_continuation keeps all messages
- UNHANDLED-EXCEPTION-FIX: run_pipeline catches all exceptions
- STATUS-404-FIX: Status endpoint falls back to DB for evicted runs
- TASK-REF-FIX: Pipeline start stores asyncio task references
- TOKEN-REFRESH-MAXLEN: TokenRefresh.refresh_token has max_length
- JWT-ALGORITHM-FIX: jwt_algorithm constrained to safe algorithms
- FROZENSET-COMPLETE-FIX: render_template uses `is not None` check
- SAFE-FORMAT-HTML-FIX: _safe_format HTML-escapes variable values
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.base import AgentResult, AgentStatus
from app.services.pipeline import (
    CHECKPOINT_STAGES,
    ExecutionMode,
    PipelineOrchestrator,
    PipelineRun,
    PipelineRunStatus,
    PipelineStage,
    StepResult,
)


# ── APPROVE-DEADLOCK-FIX ────────────────────────────────────────────


class TestApproveDeadlockFix:
    """approve_checkpoint must NOT set RUNNING — that blocks resume_run."""

    @pytest.mark.asyncio
    async def test_approve_leaves_paused(self):
        """After approve, status remains PAUSED until resume_run is called."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        run.status = PipelineRunStatus.PAUSED
        run.current_stage = PipelineStage.CHECKPOINT_DESIGN

        await orch.approve_checkpoint(run, approved=True)

        assert run.status == PipelineRunStatus.PAUSED
        assert run.checkpoint_approved is True

    @pytest.mark.asyncio
    async def test_redo_leaves_paused(self):
        """After redo, status remains PAUSED."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        run.status = PipelineRunStatus.PAUSED
        run.current_stage = PipelineStage.CHECKPOINT_DESIGN

        await orch.approve_checkpoint(
            run, approved=False, action="redo", feedback="try again"
        )

        assert run.status == PipelineRunStatus.PAUSED
        assert run.current_stage == PipelineStage.ARCHITECTURE

    @pytest.mark.asyncio
    async def test_resume_proceeds_after_approve(self):
        """resume_run proceeds when checkpoint_approved=True."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        run.status = PipelineRunStatus.PAUSED
        run.current_stage = PipelineStage.CHECKPOINT_DESIGN
        run.checkpoint_approved = True

        with patch.object(orch, "run_pipeline", new_callable=AsyncMock, return_value=run) as mock_rp:
            result = await orch.resume_run(run.run_id)
            mock_rp.assert_called_once_with(run)

    @pytest.mark.asyncio
    async def test_resume_blocks_unapproved_checkpoint(self):
        """resume_run returns without executing when checkpoint NOT approved."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        run.status = PipelineRunStatus.PAUSED
        run.current_stage = PipelineStage.CHECKPOINT_DESIGN
        run.checkpoint_approved = False

        result = await orch.resume_run(run.run_id)
        # Should return the run WITHOUT calling run_pipeline
        assert result is run
        assert result.status == PipelineRunStatus.PAUSED


# ── STEP-BY-STEP-REPLAY-FIX ────────────────────────────────────────


class TestStepByStepReplayFix:
    """Step-by-step mode must advance before pausing to prevent re-execution."""

    def test_source_advances_before_pause(self):
        """In step-by-step branch, advance() must appear before status=PAUSED."""
        source = inspect.getsource(PipelineOrchestrator._run_pipeline_inner)
        # Find the step-by-step section
        sbs_idx = source.find("STEP_BY_STEP")
        assert sbs_idx > 0, "STEP_BY_STEP mode not found in source"

        # After STEP_BY_STEP check, advance must come before PAUSED
        after_sbs = source[sbs_idx:]
        advance_idx = after_sbs.find("advance(run)")
        paused_idx = after_sbs.find("PipelineRunStatus.PAUSED")

        assert advance_idx > 0, "advance(run) not found after STEP_BY_STEP"
        assert paused_idx > 0, "PAUSED not found after STEP_BY_STEP"
        assert advance_idx < paused_idx, "advance must come BEFORE setting PAUSED"


# ── CONTINUATION-MSG-FIX ───────────────────────────────────────────


class TestContinuationMessagesFix:
    """call_ai_with_continuation must keep all original messages."""

    def test_continuation_source_keeps_all_messages(self):
        """Must use list(messages) not messages[0] for continuation context."""
        from app.agents.base import call_ai_with_continuation

        source = inspect.getsource(call_ai_with_continuation)
        # Should use list(messages) to keep all messages
        assert "list(messages)" in source
        # The actual code assignment should use list(messages), not messages[0].
        # We check the assignment line specifically (not comments which mention it).
        lines = [
            l.strip() for l in source.split("\n")
            if l.strip().startswith("current_messages")
        ]
        # At least one assignment line should use list(messages)
        assert any("list(messages)" in l for l in lines), (
            f"No assignment uses list(messages): {lines}"
        )


# ── UNHANDLED-EXCEPTION-FIX ────────────────────────────────────────


class TestUnhandledExceptionFix:
    """run_pipeline must catch all exceptions, not just TimeoutError."""

    @pytest.mark.asyncio
    async def test_crash_sets_failed_status(self):
        """Any exception in _run_pipeline_inner sets status to FAILED."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")

        # Make _run_pipeline_inner raise a ValueError
        with patch.object(
            orch, "_run_pipeline_inner",
            new_callable=AsyncMock,
            side_effect=ValueError("corrupt stage"),
        ):
            result = await orch.run_pipeline(run)

        assert result.status == PipelineRunStatus.FAILED
        assert "ValueError" in result.error
        assert "corrupt stage" in result.error

    @pytest.mark.asyncio
    async def test_crash_evicts_from_cache(self):
        """Crashed runs are evicted from _active_runs."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")

        with patch.object(
            orch, "_run_pipeline_inner",
            new_callable=AsyncMock,
            side_effect=RuntimeError("db driver error"),
        ):
            await orch.run_pipeline(run)

        assert run.run_id not in orch._active_runs

    @pytest.mark.asyncio
    async def test_crash_doesnt_lose_timeout_handling(self):
        """TimeoutError is still properly caught."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")

        with patch.object(
            orch, "_run_pipeline_inner",
            new_callable=AsyncMock,
            side_effect=asyncio.TimeoutError(),
        ):
            result = await orch.run_pipeline(run)

        assert result.status == PipelineRunStatus.FAILED
        assert "timed out" in result.error


# ── STATUS-404-FIX ─────────────────────────────────────────────────


class TestStatusEndpointDbFallback:
    """Status endpoint must fall back to DB for evicted runs."""

    def test_status_source_has_db_fallback(self):
        """get_pipeline_status must call load_run_metadata as fallback."""
        from app.routers.pipeline import get_pipeline_status

        source = inspect.getsource(get_pipeline_status)
        assert "load_run_metadata" in source


# ── TASK-REF-FIX ───────────────────────────────────────────────────


class TestTaskReferenceFix:
    """Pipeline start must store strong references to asyncio tasks."""

    def test_active_tasks_set_exists(self):
        """Module must define _active_tasks set for strong task references."""
        from app.routers import pipeline

        assert hasattr(pipeline, "_active_tasks")
        assert isinstance(pipeline._active_tasks, set)

    def test_done_callback_exists(self):
        """Module must define _pipeline_task_done callback."""
        from app.routers import pipeline

        assert hasattr(pipeline, "_pipeline_task_done")
        assert callable(pipeline._pipeline_task_done)

    def test_start_source_stores_task(self):
        """start_pipeline must store the task reference."""
        from app.routers.pipeline import start_pipeline

        source = inspect.getsource(start_pipeline)
        assert "_active_tasks" in source
        assert "add_done_callback" in source


# ── TOKEN-REFRESH-MAXLEN ───────────────────────────────────────────


class TestTokenRefreshMaxLength:
    """TokenRefresh.refresh_token must have max_length to prevent DoS."""

    def test_refresh_token_has_max_length(self):
        from app.schemas.auth import TokenRefresh

        schema = TokenRefresh.model_json_schema()
        prop = schema["properties"]["refresh_token"]
        assert "maxLength" in prop
        assert prop["maxLength"] <= 4096

    def test_oversized_refresh_token_rejected(self):
        from pydantic import ValidationError
        from app.schemas.auth import TokenRefresh

        with pytest.raises(ValidationError):
            TokenRefresh(refresh_token="x" * 10000)

    def test_normal_refresh_token_accepted(self):
        from app.schemas.auth import TokenRefresh

        token = TokenRefresh(refresh_token="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test")
        assert len(token.refresh_token) > 0


# ── JWT-ALGORITHM-FIX ──────────────────────────────────────────────


class TestJwtAlgorithmConstraint:
    """jwt_algorithm must be constrained to safe algorithms."""

    def test_default_is_hs256(self):
        """Default algorithm should be HS256."""
        from app.config import Settings

        schema = Settings.model_json_schema()
        algo = schema["properties"]["jwt_algorithm"]
        assert algo.get("default") == "HS256"

    def test_none_algorithm_rejected(self):
        """'none' algorithm must not be accepted."""
        from app.config import Settings

        schema = Settings.model_json_schema()
        algo = schema["properties"]["jwt_algorithm"]
        # Must have an enum constraint
        assert "enum" in algo
        assert "none" not in algo["enum"]

    def test_allowed_algorithms(self):
        """Only known-safe algorithms should be allowed."""
        from app.config import Settings

        schema = Settings.model_json_schema()
        algo = schema["properties"]["jwt_algorithm"]
        allowed = set(algo.get("enum", []))
        assert "HS256" in allowed
        # All must be HMAC or RSA based
        for a in allowed:
            assert a.startswith(("HS", "RS")), f"Unexpected algorithm: {a}"


# ── FROZENSET-COMPLETE-FIX ─────────────────────────────────────────


class TestFrozensetCompleteFix:
    """render_template must use `is not None` for allowed_variables check."""

    def test_empty_frozenset_blocks_all_variables(self):
        """frozenset() means 'no variables allowed' — must reject any var."""
        from app.services.prompt_engine import render_template

        with pytest.raises(ValueError, match="Disallowed variables"):
            render_template(
                "Hello {{ name }}",
                variables={"name": "World"},
                allowed_variables=frozenset(),  # empty = none allowed
            )

    def test_none_allowed_variables_permits_all(self):
        """None means 'no restriction' — must allow any var."""
        from app.services.prompt_engine import render_template

        # Template uses {{ var }} syntax (double braces)
        result = render_template(
            "Hello {{ name }}",
            variables={"name": "World"},
            allowed_variables=None,  # None = no restriction
        )
        assert result == "Hello World"

    def test_specific_frozenset_allows_listed(self):
        """frozenset({"name"}) allows only 'name' variable."""
        from app.services.prompt_engine import render_template

        result = render_template(
            "Hello {{ name }}",
            variables={"name": "World"},
            allowed_variables=frozenset({"name"}),
        )
        assert result == "Hello World"

    def test_specific_frozenset_blocks_unlisted(self):
        """frozenset({"name"}) blocks variables not in the set."""
        from app.services.prompt_engine import render_template

        with pytest.raises(ValueError, match="Disallowed variables"):
            render_template(
                "Hello {{ name }} {{ secret }}",
                variables={"name": "World", "secret": "hack"},
                allowed_variables=frozenset({"name"}),
            )


# ── SAFE-FORMAT-HTML-FIX ───────────────────────────────────────────


class TestSafeFormatHtmlEscape:
    """_safe_format must HTML-escape variable values for XSS defense."""

    def test_html_tags_escaped(self):
        from app.services.notification import _safe_format

        result = _safe_format(
            "Project: {project_name}",
            {"project_name": "<script>alert('xss')</script>"},
        )
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_normal_text_unchanged(self):
        from app.services.notification import _safe_format

        result = _safe_format("Hello {name}", {"name": "World"})
        assert result == "Hello World"

    def test_angle_brackets_escaped(self):
        from app.services.notification import _safe_format

        result = _safe_format("{msg}", {"msg": '<img onerror=alert(1) src="">'})
        assert "<img" not in result
        assert "&lt;img" in result

    def test_ampersand_escaped(self):
        from app.services.notification import _safe_format

        result = _safe_format("{text}", {"text": "foo & bar"})
        assert result == "foo &amp; bar"
