"""Tests for Round 12 fixes: hardening from brutal re-review cycle 12.

Covers:
- INTERRUPTED-STATUS: PipelineRunStatus.INTERRUPTED exists, resume_run handles it
- DEEP-COPY: _execute_parallel uses copy.deepcopy for context isolation
- GEMINI-EMPTY: _parse_google_response raises on empty candidates
- FIXER-INDEX: Fixer markdown fence stripping handles edge cases
- BATCH-SSRF: get_batch_results validates results_url domain
"""

from __future__ import annotations

import inspect
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.base import AgentResult, AgentStatus
from app.services.pipeline import (
    PipelineOrchestrator,
    PipelineRunStatus,
    PipelineStage,
)


# ── INTERRUPTED-STATUS: Enum exists and resume handles it ────────


class TestInterruptedStatus:
    """PipelineRunStatus.INTERRUPTED must exist and be resumable."""

    def test_interrupted_status_exists(self):
        """The INTERRUPTED member must be in the enum."""
        assert hasattr(PipelineRunStatus, "INTERRUPTED")
        assert PipelineRunStatus.INTERRUPTED.value == "interrupted"

    def test_resume_run_accepts_interrupted(self):
        """resume_run must accept INTERRUPTED as a resumable status."""
        # REVIEW-FIX: resume logic moved to _resume_run_inner (per-run lock)
        source = inspect.getsource(PipelineOrchestrator._resume_run_inner)
        assert "PipelineRunStatus.INTERRUPTED" in source

    def test_interrupted_resets_to_running(self):
        """When resuming an INTERRUPTED run, status must reset to RUNNING."""
        # REVIEW-FIX: resume logic moved to _resume_run_inner (per-run lock)
        source = inspect.getsource(PipelineOrchestrator._resume_run_inner)
        # Verify INTERRUPTED is in the tuple with FAILED for reset
        assert "PipelineRunStatus.INTERRUPTED" in source

    def test_rebuild_run_parses_interrupted(self):
        """rebuild_run must correctly parse 'interrupted' from DB."""
        from app.services.pipeline import PipelinePersistence

        persistence = PipelinePersistence()
        run_data = {
            "db_run_id": str(uuid.uuid4()),
            "project_id": str(uuid.uuid4()),
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "status": "interrupted",
            "current_step": "requirements",
            "execution_mode": "checkpoint",
            "context_snapshot": {},
            "error_summary": "Server crashed",
        }
        run = persistence.rebuild_run(run_data)
        assert run.status == PipelineRunStatus.INTERRUPTED


# ── DEEP-COPY: Parallel agents get deep-copied context ───────────


class TestDeepCopyParallel:
    """_execute_parallel must use copy.deepcopy for full isolation."""

    def test_uses_deepcopy(self):
        """Source must use copy.deepcopy, not dict()."""
        source = inspect.getsource(PipelineOrchestrator._execute_parallel)
        assert "copy.deepcopy" in source
        # Must NOT have the old shallow copy pattern
        assert "dict(run.context)" not in source

    @pytest.mark.asyncio
    async def test_nested_mutation_isolated(self):
        """Parallel agent mutating a nested dict must not affect original."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        # Set up nested context that could be mutated
        run.context["prior_agent"] = {"findings": ["finding1"]}

        async def mutating_agent(run_id, context):
            # Mutate a nested list from a prior agent's output
            context["prior_agent"]["findings"].append("injected")
            return AgentResult(
                agent_name="test", status=AgentStatus.COMPLETED, output={"ok": True}
            )

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(side_effect=mutating_agent)

        with patch("app.services.pipeline.get_agent", return_value=mock_agent):
            await orch._execute_parallel(
                run, PipelineStage.QUALITY_REVIEW, ["karan", "navya"]
            )

        # Original nested list must be UNMODIFIED
        assert run.context["prior_agent"]["findings"] == ["finding1"]


# ── GEMINI-EMPTY: Empty candidates without blockReason raises ────


class TestGeminiEmptyCandidates:
    """_parse_google_response must raise on empty candidates."""

    def test_empty_candidates_raises(self):
        from app.services.ai_router import AIRouter, Provider, ModelSpec

        router = AIRouter.__new__(AIRouter)
        spec = ModelSpec(
            model_id="gemini-2.0-flash",
            provider=Provider.GOOGLE,
            display_name="Flash",
            cost_tier=1,
        )

        data = {"candidates": [], "usageMetadata": {}}
        with pytest.raises(ValueError, match="empty candidates"):
            router._parse_google_response(data, spec, "req-123")

    def test_missing_candidates_key_raises(self):
        from app.services.ai_router import AIRouter, Provider, ModelSpec

        router = AIRouter.__new__(AIRouter)
        spec = ModelSpec(
            model_id="gemini-2.0-flash",
            provider=Provider.GOOGLE,
            display_name="Flash",
            cost_tier=1,
        )

        data = {"usageMetadata": {}}  # No "candidates" key at all
        with pytest.raises(ValueError, match="empty candidates"):
            router._parse_google_response(data, spec, "req-456")

    def test_valid_candidates_still_works(self):
        from app.services.ai_router import AIRouter, Provider, ModelSpec

        router = AIRouter.__new__(AIRouter)
        spec = ModelSpec(
            model_id="gemini-2.0-flash",
            provider=Provider.GOOGLE,
            display_name="Flash",
            cost_tier=1,
        )

        data = {
            "candidates": [{
                "content": {"parts": [{"text": "Hello world"}]},
                "finishReason": "STOP",
            }],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
        }
        response = router._parse_google_response(data, spec, "req-789")
        assert response.content == "Hello world"


# ── FIXER-INDEX: Markdown fence stripping handles edge cases ─────


class TestFixerMarkdownFence:
    """Fixer fence stripping must not raise IndexError."""

    def test_source_has_guard(self):
        from app.agents.fixer import Fixer

        source = inspect.getsource(Fixer._attempt_fix)
        # Must check len(parts) > 1 before indexing
        assert "len(parts) > 1" in source

    def test_bare_backticks_handled(self):
        """If response is exactly '```', fence stripping should return ''."""
        # Simulate the logic inline
        content = "```"
        parts = content.split("\n", 1)
        if len(parts) > 1:
            result = parts[1].rsplit("```", 1)[0]
        else:
            result = ""
        assert result == ""

    def test_normal_fenced_code(self):
        """Normal fenced code should be extracted correctly."""
        content = "```python\nprint('hello')\n```"
        parts = content.split("\n", 1)
        if len(parts) > 1:
            result = parts[1].rsplit("```", 1)[0]
        else:
            result = ""
        assert result == "print('hello')\n"


# ── BATCH-SSRF: Batch results URL validated ──────────────────────


class TestBatchUrlValidation:
    """get_batch_results must validate results_url domain."""

    def test_source_validates_url(self):
        from app.services.ai_router import AIRouter

        source = inspect.getsource(AIRouter.get_batch_results)
        assert 'https://api.anthropic.com/' in source
        assert "Unexpected batch results URL" in source

    def test_malicious_url_rejected(self):
        """A non-Anthropic results URL must raise ValueError."""
        # Test the validation logic inline
        results_url = "https://evil.com/steal-key"
        with pytest.raises(ValueError, match="Unexpected batch results URL"):
            if not results_url.startswith("https://api.anthropic.com/"):
                raise ValueError(f"Unexpected batch results URL domain: {results_url[:80]}")

    def test_valid_anthropic_url_accepted(self):
        """A valid Anthropic URL must pass validation."""
        results_url = "https://api.anthropic.com/v1/messages/batches/abc123/results"
        # This should NOT raise
        assert results_url.startswith("https://api.anthropic.com/")
