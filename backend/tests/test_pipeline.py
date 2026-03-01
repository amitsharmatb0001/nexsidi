"""Tests for pipeline orchestrator: stages, execution, crash recovery, resume."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.base import AgentResult, AgentStatus
from app.services.pipeline import (
    CHECKPOINT_STAGES,
    MAX_CHALLENGE_RETRIES,
    MAX_FIX_RETEST_CYCLES,
    STAGE_AGENTS,
    STAGE_ORDER,
    ExecutionMode,
    PipelineOrchestrator,
    PipelinePersistence,
    PipelineRun,
    PipelineRunStatus,
    PipelineStage,
    ProjectRateLimiter,
    StepResult,
    get_orchestrator,
)


def _mock_get_agent(name: str):
    """Return a mock agent that always succeeds with dummy output."""
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(return_value=AgentResult(
        agent_name=name,
        status=AgentStatus.COMPLETED,
        output={"mock": True, "agent": name},
    ))
    return mock_agent


# -- Stage and Enum Tests ----------------------------------------------------


class TestPipelineStages:
    """Test pipeline stage definitions and ordering."""

    def test_stage_count(self):
        assert len(PipelineStage) == 18

    def test_stage_order_matches_enum(self):
        assert STAGE_ORDER == list(PipelineStage)

    def test_every_stage_has_agent(self):
        for stage in PipelineStage:
            assert stage in STAGE_AGENTS

    def test_checkpoint_stages(self):
        assert PipelineStage.CHECKPOINT_DESIGN in CHECKPOINT_STAGES
        assert PipelineStage.CHECKPOINT_TESTING in CHECKPOINT_STAGES
        assert len(CHECKPOINT_STAGES) == 2

    def test_architecture_review_stage_exists(self):
        assert PipelineStage.ARCHITECTURE_REVIEW in STAGE_AGENTS
        assert STAGE_AGENTS[PipelineStage.ARCHITECTURE_REVIEW] == "challenger"
        # Must come after ARCHITECTURE and before DATABASE_DESIGN
        stages = list(PipelineStage)
        arch_idx = stages.index(PipelineStage.ARCHITECTURE)
        review_idx = stages.index(PipelineStage.ARCHITECTURE_REVIEW)
        db_idx = stages.index(PipelineStage.DATABASE_DESIGN)
        assert arch_idx < review_idx < db_idx

    def test_quality_review_is_parallel(self):
        agents = STAGE_AGENTS[PipelineStage.QUALITY_REVIEW]
        assert isinstance(agents, list)
        assert len(agents) == 3

    def test_execution_modes(self):
        assert len(ExecutionMode) == 3
        assert ExecutionMode.CHECKPOINT.value == "checkpoint"
        assert ExecutionMode.STEP_BY_STEP.value == "step_by_step"
        assert ExecutionMode.DIRECT.value == "direct"

    def test_pipeline_run_status(self):
        assert len(PipelineRunStatus) == 7  # R12-FIX: Added INTERRUPTED
        assert PipelineRunStatus.RUNNING.value == "running"
        assert PipelineRunStatus.PAUSED.value == "paused"
        assert PipelineRunStatus.CANCELLED.value == "cancelled"
        assert PipelineRunStatus.INTERRUPTED.value == "interrupted"


# -- PipelineRun Dataclass Tests ---------------------------------------------


class TestPipelineRun:
    """Test PipelineRun dataclass behavior."""

    def test_default_values(self):
        run = PipelineRun()
        assert run.run_id  # UUID generated
        assert run.status == PipelineRunStatus.CREATED
        assert run.execution_mode == ExecutionMode.CHECKPOINT
        assert run.current_stage == PipelineStage.REQUIREMENTS
        assert run.step_results == []
        assert run.context == {}
        assert run.error is None
        assert run.db_run_id is None

    def test_custom_values(self):
        run = PipelineRun(
            project_id="proj-1",
            organization_id="org-1",
            user_id="user-1",
            execution_mode=ExecutionMode.DIRECT,
        )
        assert run.project_id == "proj-1"
        assert run.organization_id == "org-1"
        assert run.user_id == "user-1"
        assert run.execution_mode == ExecutionMode.DIRECT


# -- Orchestrator Core Tests --------------------------------------------------


class TestPipelineOrchestrator:
    """Test core orchestrator methods."""

    def test_create_run(self):
        orch = PipelineOrchestrator()
        run = orch.create_run(
            project_id="proj-1",
            organization_id="org-1",
            user_id="user-1",
        )
        assert run.run_id in orch._active_runs
        assert run.project_id == "proj-1"
        assert run.status == PipelineRunStatus.CREATED

    def test_get_run_found(self):
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        found = orch.get_run(run.run_id)
        assert found is run

    def test_get_run_not_found(self):
        orch = PipelineOrchestrator()
        assert orch.get_run("nonexistent") is None

    @pytest.mark.asyncio
    async def test_advance_stage(self):
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        assert run.current_stage == PipelineStage.REQUIREMENTS

        next_stage = await orch.advance(run)
        assert next_stage == PipelineStage.ANALYSIS
        assert run.current_stage == PipelineStage.ANALYSIS

    @pytest.mark.asyncio
    async def test_advance_past_last_stage(self):
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        run.current_stage = PipelineStage.COMPLETED
        next_stage = await orch.advance(run)
        assert next_stage is None
        assert run.status == PipelineRunStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_execute_stage_completed(self):
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        run.current_stage = PipelineStage.COMPLETED
        step = await orch.execute_stage(run)
        assert step.agent_name == "__done__"
        assert run.status == PipelineRunStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_execute_checkpoint_direct_mode(self):
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1", ExecutionMode.DIRECT)
        run.current_stage = PipelineStage.CHECKPOINT_DESIGN
        step = await orch.execute_stage(run)
        assert step.skipped is True
        assert run.status != PipelineRunStatus.PAUSED

    @pytest.mark.asyncio
    async def test_execute_checkpoint_pauses(self):
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1", ExecutionMode.CHECKPOINT)
        run.current_stage = PipelineStage.CHECKPOINT_DESIGN
        step = await orch.execute_stage(run)
        assert run.status == PipelineRunStatus.PAUSED
        assert step.result.status == AgentStatus.WAITING_USER

    @pytest.mark.asyncio
    async def test_approve_checkpoint(self):
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        run.status = PipelineRunStatus.PAUSED
        run.current_stage = PipelineStage.CHECKPOINT_DESIGN
        await orch.approve_checkpoint(run, approved=True)
        # R9-FIX: approve now leaves status PAUSED (not RUNNING) to avoid
        # APPROVE-DEADLOCK where resume_run blocks RUNNING status.
        assert run.status == PipelineRunStatus.PAUSED
        assert run.checkpoint_approved is True

    @pytest.mark.asyncio
    async def test_reject_checkpoint(self):
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        run.status = PipelineRunStatus.PAUSED
        # R8-FIX: Must be at an actual checkpoint stage, not requirements
        run.current_stage = PipelineStage.CHECKPOINT_DESIGN
        await orch.approve_checkpoint(run, approved=False)
        assert run.status == PipelineRunStatus.FAILED
        assert run.error == "Checkpoint rejected by user"

    @pytest.mark.asyncio
    async def test_approve_checkpoint_not_paused_raises(self):
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        run.status = PipelineRunStatus.RUNNING
        with pytest.raises(ValueError, match="not paused"):
            await orch.approve_checkpoint(run, approved=True)

    @pytest.mark.asyncio
    async def test_execute_missing_agent_skips(self):
        """Agents that don't exist yet are skipped gracefully."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        # Tilotma exists, so this should not skip
        # But we test with a stage whose agent IS registered:
        step = await orch._execute_agent(run, PipelineStage.REQUIREMENTS, "nonexistent_agent")
        assert step.skipped is True


# -- Pipeline Execution Tests -------------------------------------------------


class TestPipelineExecution:
    """Test full pipeline execution flow."""

    @pytest.mark.asyncio
    @patch("app.services.pipeline.get_agent", side_effect=_mock_get_agent)
    async def test_run_pipeline_stops_at_first_checkpoint(self, mock_agent):
        """Pipeline should pause at CHECKPOINT_DESIGN in CHECKPOINT mode."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1", ExecutionMode.CHECKPOINT)
        result = await orch.run_pipeline(run)

        assert result.status == PipelineRunStatus.PAUSED
        assert result.current_stage == PipelineStage.CHECKPOINT_DESIGN
        # Should have step results for stages before checkpoint
        assert len(result.step_results) > 0

    @pytest.mark.asyncio
    @patch("app.services.pipeline.get_agent", side_effect=_mock_get_agent)
    async def test_run_pipeline_step_by_step_pauses_after_first(self, mock_agent):
        """Step-by-step mode pauses after every stage."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1", ExecutionMode.STEP_BY_STEP)
        result = await orch.run_pipeline(run)

        assert result.status == PipelineRunStatus.PAUSED
        assert len(result.step_results) == 1
        assert result.step_results[0].stage == PipelineStage.REQUIREMENTS

    @pytest.mark.asyncio
    @patch("app.services.pipeline.get_agent", side_effect=_mock_get_agent)
    async def test_run_pipeline_direct_mode_completes(self, mock_agent):
        """Direct mode runs all stages without pausing."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1", ExecutionMode.DIRECT)
        result = await orch.run_pipeline(run)

        assert result.status == PipelineRunStatus.COMPLETED
        assert result.current_stage == PipelineStage.COMPLETED

    @pytest.mark.asyncio
    @patch("app.services.pipeline.get_agent", side_effect=_mock_get_agent)
    async def test_context_accumulates(self, mock_agent):
        """Agent output should accumulate in context dict when agents exist."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1", ExecutionMode.DIRECT)
        result = await orch.run_pipeline(run)

        assert result.status == PipelineRunStatus.COMPLETED
        # Agents that ran should have added output to context
        assert "tilotma" in result.context
        assert result.context["tilotma"]["mock"] is True
        assert len(result.step_results) > 0


# -- PipelinePersistence Tests ------------------------------------------------


class TestPipelinePersistence:
    """Test the persistence layer for crash recovery."""

    def test_rebuild_run_basic(self):
        persistence = PipelinePersistence()
        run_data = {
            "db_run_id": str(uuid.uuid4()),
            "project_id": str(uuid.uuid4()),
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "status": "running",
            "current_step": "architecture",
            "execution_mode": "checkpoint",
            "context_snapshot": {"tilotma": {"requirements": "build an app"}},
            "error_summary": None,
        }
        run = persistence.rebuild_run(run_data)
        assert run.run_id == run_data["db_run_id"]
        assert run.current_stage == PipelineStage.ARCHITECTURE
        assert run.execution_mode == ExecutionMode.CHECKPOINT
        assert run.status == PipelineRunStatus.RUNNING
        assert run.context == {"tilotma": {"requirements": "build an app"}}
        assert run.db_run_id == run_data["db_run_id"]

    def test_rebuild_run_invalid_stage_defaults(self):
        persistence = PipelinePersistence()
        run_data = {
            "db_run_id": str(uuid.uuid4()),
            "project_id": str(uuid.uuid4()),
            "organization_id": str(uuid.uuid4()),
            "status": "running",
            "current_step": "invalid_stage",
            "execution_mode": "invalid_mode",
            "context_snapshot": None,
        }
        run = persistence.rebuild_run(run_data)
        assert run.current_stage == PipelineStage.REQUIREMENTS
        assert run.execution_mode == ExecutionMode.CHECKPOINT

    def test_rebuild_run_paused_status(self):
        persistence = PipelinePersistence()
        run_data = {
            "db_run_id": str(uuid.uuid4()),
            "project_id": str(uuid.uuid4()),
            "organization_id": str(uuid.uuid4()),
            "status": "paused",
            "current_step": "checkpoint_design",
            "execution_mode": "checkpoint",
            "context_snapshot": {},
        }
        run = persistence.rebuild_run(run_data)
        assert run.status == PipelineRunStatus.PAUSED
        assert run.current_stage == PipelineStage.CHECKPOINT_DESIGN

    def test_rebuild_run_empty_context(self):
        persistence = PipelinePersistence()
        run_data = {
            "db_run_id": str(uuid.uuid4()),
            "project_id": str(uuid.uuid4()),
            "organization_id": str(uuid.uuid4()),
            "status": "running",
            "current_step": "requirements",
            "execution_mode": "direct",
            "context_snapshot": None,
        }
        run = persistence.rebuild_run(run_data)
        assert run.context == {}

    def test_rebuild_preserves_all_fields(self):
        persistence = PipelinePersistence()
        db_id = str(uuid.uuid4())
        proj_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        run_data = {
            "db_run_id": db_id,
            "project_id": proj_id,
            "organization_id": org_id,
            "user_id": user_id,
            "status": "failed",
            "current_step": "testing",
            "execution_mode": "step_by_step",
            "context_snapshot": {"agent1": "data"},
            "error_summary": "Something broke",
        }
        run = persistence.rebuild_run(run_data)
        assert run.project_id == proj_id
        assert run.organization_id == org_id
        assert run.user_id == user_id
        assert run.status == PipelineRunStatus.FAILED
        assert run.error == "Something broke"


# -- Fix-Retest Loop Tests ----------------------------------------------------


class TestFixRetestLoop:
    """Test the fix-retest loop (quality gates re-run after fixer)."""

    def test_has_errors_to_fix_with_errors(self):
        """KEY-FIX: Uses actual fixer output keys (errors_remaining, errors_fixed)."""
        orch = PipelineOrchestrator()
        step = StepResult(
            stage=PipelineStage.FIXING,
            agent_name="fixer",
            result=AgentResult(
                agent_name="fixer",
                status=AgentStatus.COMPLETED,
                output={"errors_remaining": 1, "errors_fixed": 3},
            ),
        )
        assert orch._has_errors_to_fix(step) is True

    def test_has_errors_to_fix_no_errors(self):
        orch = PipelineOrchestrator()
        step = StepResult(
            stage=PipelineStage.FIXING,
            agent_name="fixer",
            result=AgentResult(
                agent_name="fixer",
                status=AgentStatus.COMPLETED,
                output={"errors_remaining": 0, "errors_fixed": 0},
            ),
        )
        assert orch._has_errors_to_fix(step) is False

    def test_has_errors_to_fix_no_result(self):
        orch = PipelineOrchestrator()
        step = StepResult(
            stage=PipelineStage.FIXING,
            agent_name="fixer",
            result=None,
        )
        assert orch._has_errors_to_fix(step) is False

    def test_has_errors_to_fix_empty_output(self):
        orch = PipelineOrchestrator()
        step = StepResult(
            stage=PipelineStage.FIXING,
            agent_name="fixer",
            result=AgentResult(
                agent_name="fixer",
                status=AgentStatus.COMPLETED,
                output={},
            ),
        )
        assert orch._has_errors_to_fix(step) is False

    def test_has_errors_all_fixed_no_remaining(self):
        """If all errors fixed and none remaining, no re-test needed."""
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

    @pytest.mark.asyncio
    async def test_fix_retest_loop_runs_once_when_clean(self):
        """If fixer finds no errors, no loop occurs."""

        def mock_agent_clean(name):
            agent = MagicMock()
            if name == "fixer":
                agent.run = AsyncMock(return_value=AgentResult(
                    agent_name="fixer",
                    status=AgentStatus.COMPLETED,
                    output={"errors_remaining": 0, "errors_fixed": 0},
                ))
            else:
                agent.run = AsyncMock(return_value=AgentResult(
                    agent_name=name,
                    status=AgentStatus.COMPLETED,
                    output={"mock": True},
                ))
            return agent

        with patch("app.services.pipeline.get_agent", side_effect=mock_agent_clean):
            orch = PipelineOrchestrator()
            run = orch.create_run("proj-1", "org-1", "user-1", ExecutionMode.DIRECT)
            result = await orch.run_pipeline(run)

        assert result.status == PipelineRunStatus.COMPLETED
        assert result.fix_retest_cycle == 0  # No re-runs

    @pytest.mark.asyncio
    async def test_fix_retest_loop_cycles_with_errors(self):
        """If fixer finds remaining errors, quality gates re-run (up to 3 cycles)."""
        call_count = {"fixer": 0}

        def mock_agent_with_errors(name):
            agent = MagicMock()
            if name == "fixer":
                def make_fixer_run(*args, **kwargs):
                    call_count["fixer"] += 1
                    # First 2 calls: some fixed but some remaining
                    if call_count["fixer"] <= 2:
                        return AgentResult(
                            agent_name="fixer",
                            status=AgentStatus.COMPLETED,
                            output={"errors_remaining": 2, "errors_fixed": 3},
                        )
                    else:
                        # 3rd call: all fixed
                        return AgentResult(
                            agent_name="fixer",
                            status=AgentStatus.COMPLETED,
                            output={"errors_remaining": 0, "errors_fixed": 0},
                        )
                agent.run = AsyncMock(side_effect=make_fixer_run)
            else:
                agent.run = AsyncMock(return_value=AgentResult(
                    agent_name=name,
                    status=AgentStatus.COMPLETED,
                    output={"mock": True},
                ))
            return agent

        with patch("app.services.pipeline.get_agent", side_effect=mock_agent_with_errors):
            orch = PipelineOrchestrator()
            run = orch.create_run("proj-1", "org-1", "user-1", ExecutionMode.DIRECT)
            result = await orch.run_pipeline(run)

        assert result.status == PipelineRunStatus.COMPLETED
        assert result.fix_retest_cycle == 2  # Re-ran 2 times
        assert call_count["fixer"] == 3  # Called 3 times total

    @pytest.mark.asyncio
    async def test_fix_retest_loop_caps_at_max_cycles(self):
        """Fix-retest loop should not exceed MAX_FIX_RETEST_CYCLES."""

        def mock_agent_always_errors(name):
            agent = MagicMock()
            if name == "fixer":
                agent.run = AsyncMock(return_value=AgentResult(
                    agent_name="fixer",
                    status=AgentStatus.COMPLETED,
                    output={"errors_remaining": 2, "errors_fixed": 3},
                ))
            else:
                agent.run = AsyncMock(return_value=AgentResult(
                    agent_name=name,
                    status=AgentStatus.COMPLETED,
                    output={"mock": True},
                ))
            return agent

        with patch("app.services.pipeline.get_agent", side_effect=mock_agent_always_errors):
            orch = PipelineOrchestrator()
            run = orch.create_run("proj-1", "org-1", "user-1", ExecutionMode.DIRECT)
            result = await orch.run_pipeline(run)

        assert result.status == PipelineRunStatus.COMPLETED
        assert result.fix_retest_cycle == 3  # Capped at MAX_FIX_RETEST_CYCLES

    def test_step_result_has_cycle_field(self):
        step = StepResult(
            stage=PipelineStage.FIXING,
            agent_name="fixer",
            cycle=2,
        )
        assert step.cycle == 2

    def test_pipeline_run_has_fix_retest_cycle(self):
        run = PipelineRun()
        assert run.fix_retest_cycle == 0


# -- Architecture Challenge-Retry Loop Tests -----------------------------------


class TestChallengeRetryLoop:
    """Test the architecture challenge-retry loop (Challenger rejects -> Vikram re-generates)."""

    def test_has_critical_challenges_with_critical(self):
        orch = PipelineOrchestrator()
        step = StepResult(
            stage=PipelineStage.ARCHITECTURE_REVIEW,
            agent_name="challenger",
            result=AgentResult(
                agent_name="challenger",
                status=AgentStatus.COMPLETED,
                output={
                    "verdict": "reject",
                    "has_critical": True,
                    "critical_count": 2,
                    "challenges": [{"severity": "critical", "description": "No auth"}],
                },
            ),
        )
        assert orch._has_critical_challenges(step) is True

    def test_has_critical_challenges_no_critical(self):
        orch = PipelineOrchestrator()
        step = StepResult(
            stage=PipelineStage.ARCHITECTURE_REVIEW,
            agent_name="challenger",
            result=AgentResult(
                agent_name="challenger",
                status=AgentStatus.COMPLETED,
                output={
                    "verdict": "pass",
                    "has_critical": False,
                    "critical_count": 0,
                    "challenges": [],
                },
            ),
        )
        assert orch._has_critical_challenges(step) is False

    def test_has_critical_challenges_warn_verdict(self):
        orch = PipelineOrchestrator()
        step = StepResult(
            stage=PipelineStage.ARCHITECTURE_REVIEW,
            agent_name="challenger",
            result=AgentResult(
                agent_name="challenger",
                status=AgentStatus.COMPLETED,
                output={
                    "verdict": "warn",
                    "has_critical": False,
                    "critical_count": 0,
                },
            ),
        )
        assert orch._has_critical_challenges(step) is False

    def test_has_critical_challenges_no_result(self):
        orch = PipelineOrchestrator()
        step = StepResult(
            stage=PipelineStage.ARCHITECTURE_REVIEW,
            agent_name="challenger",
            result=None,
        )
        assert orch._has_critical_challenges(step) is False

    def test_has_critical_challenges_empty_output(self):
        orch = PipelineOrchestrator()
        step = StepResult(
            stage=PipelineStage.ARCHITECTURE_REVIEW,
            agent_name="challenger",
            result=AgentResult(
                agent_name="challenger",
                status=AgentStatus.COMPLETED,
                output={},
            ),
        )
        assert orch._has_critical_challenges(step) is False

    def test_pipeline_run_has_challenge_retry_count(self):
        run = PipelineRun()
        assert run.challenge_retry_count == 0

    def test_max_challenge_retries_constant(self):
        assert MAX_CHALLENGE_RETRIES == 2

    @pytest.mark.asyncio
    async def test_no_retry_when_challenger_passes(self):
        """If challenger finds no critical issues, no retry loop occurs."""

        def mock_agent_pass(name):
            agent = MagicMock()
            if name == "challenger":
                agent.run = AsyncMock(return_value=AgentResult(
                    agent_name="challenger",
                    status=AgentStatus.COMPLETED,
                    output={
                        "verdict": "pass",
                        "has_critical": False,
                        "critical_count": 0,
                        "challenges": [],
                    },
                ))
            else:
                agent.run = AsyncMock(return_value=AgentResult(
                    agent_name=name,
                    status=AgentStatus.COMPLETED,
                    output={"mock": True},
                ))
            return agent

        with patch("app.services.pipeline.get_agent", side_effect=mock_agent_pass):
            orch = PipelineOrchestrator()
            run = orch.create_run("proj-1", "org-1", "user-1", ExecutionMode.DIRECT)
            result = await orch.run_pipeline(run)

        assert result.status == PipelineRunStatus.COMPLETED
        assert result.challenge_retry_count == 0

    @pytest.mark.asyncio
    async def test_retry_when_challenger_rejects(self):
        """If challenger finds critical issues, Vikram re-runs (max 2 retries)."""
        call_count = {"vikram": 0, "challenger": 0}

        def mock_agent_challenge(name):
            agent = MagicMock()
            if name == "challenger":
                def make_challenger_run(*args, **kwargs):
                    call_count["challenger"] += 1
                    # First call rejects, second pass passes
                    if call_count["challenger"] <= 1:
                        return AgentResult(
                            agent_name="challenger",
                            status=AgentStatus.COMPLETED,
                            output={
                                "verdict": "reject",
                                "has_critical": True,
                                "critical_count": 1,
                                "challenges": [{"severity": "critical", "description": "No auth"}],
                            },
                        )
                    else:
                        return AgentResult(
                            agent_name="challenger",
                            status=AgentStatus.COMPLETED,
                            output={
                                "verdict": "pass",
                                "has_critical": False,
                                "critical_count": 0,
                                "challenges": [],
                            },
                        )
                agent.run = AsyncMock(side_effect=make_challenger_run)
            elif name == "vikram":
                def make_vikram_run(*args, **kwargs):
                    call_count["vikram"] += 1
                    return AgentResult(
                        agent_name="vikram",
                        status=AgentStatus.COMPLETED,
                        output={"contract": {"tech_stack": {}}},
                    )
                agent.run = AsyncMock(side_effect=make_vikram_run)
            else:
                agent.run = AsyncMock(return_value=AgentResult(
                    agent_name=name,
                    status=AgentStatus.COMPLETED,
                    output={"mock": True},
                ))
            return agent

        with patch("app.services.pipeline.get_agent", side_effect=mock_agent_challenge):
            orch = PipelineOrchestrator()
            run = orch.create_run("proj-1", "org-1", "user-1", ExecutionMode.DIRECT)
            result = await orch.run_pipeline(run)

        assert result.status == PipelineRunStatus.COMPLETED
        assert result.challenge_retry_count == 1
        assert call_count["vikram"] == 2  # Initial + 1 retry
        assert call_count["challenger"] == 2  # Reviewed twice

    @pytest.mark.asyncio
    async def test_challenge_retry_caps_at_max(self):
        """Challenge-retry loop should not exceed MAX_CHALLENGE_RETRIES."""

        def mock_agent_always_reject(name):
            agent = MagicMock()
            if name == "challenger":
                agent.run = AsyncMock(return_value=AgentResult(
                    agent_name="challenger",
                    status=AgentStatus.COMPLETED,
                    output={
                        "verdict": "reject",
                        "has_critical": True,
                        "critical_count": 2,
                        "challenges": [{"severity": "critical", "description": "Still bad"}],
                    },
                ))
            else:
                agent.run = AsyncMock(return_value=AgentResult(
                    agent_name=name,
                    status=AgentStatus.COMPLETED,
                    output={"mock": True},
                ))
            return agent

        with patch("app.services.pipeline.get_agent", side_effect=mock_agent_always_reject):
            orch = PipelineOrchestrator()
            run = orch.create_run("proj-1", "org-1", "user-1", ExecutionMode.DIRECT)
            result = await orch.run_pipeline(run)

        assert result.status == PipelineRunStatus.COMPLETED
        assert result.challenge_retry_count == MAX_CHALLENGE_RETRIES  # Capped at 2

    @pytest.mark.asyncio
    async def test_challenges_stored_in_context(self):
        """Critical challenges should be stored in context for Vikram."""
        challenges_data = [
            {"severity": "critical", "description": "No auth defined"},
            {"severity": "critical", "description": "No rate limiting"},
        ]

        def mock_agent_with_challenges(name):
            agent = MagicMock()
            if name == "challenger":
                agent.run = AsyncMock(return_value=AgentResult(
                    agent_name="challenger",
                    status=AgentStatus.COMPLETED,
                    output={
                        "verdict": "reject",
                        "has_critical": True,
                        "critical_count": 2,
                        "challenges": challenges_data,
                    },
                ))
            else:
                agent.run = AsyncMock(return_value=AgentResult(
                    agent_name=name,
                    status=AgentStatus.COMPLETED,
                    output={"mock": True},
                ))
            return agent

        with patch("app.services.pipeline.get_agent", side_effect=mock_agent_with_challenges):
            orch = PipelineOrchestrator()
            run = orch.create_run("proj-1", "org-1", "user-1", ExecutionMode.DIRECT)
            result = await orch.run_pipeline(run)

        # After max retries, challenges should still be in context
        assert "__architecture_challenges__" in result.context
        assert len(result.context["__architecture_challenges__"]) == 2


# -- User Feedback Loop Tests -------------------------------------------------


class TestUserFeedbackLoop:
    """Test user feedback at checkpoints: approve, reject, redo."""

    @pytest.mark.asyncio
    async def test_approve_with_feedback(self):
        """Approving with feedback stores it in context."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        run.status = PipelineRunStatus.PAUSED
        run.current_stage = PipelineStage.CHECKPOINT_DESIGN

        await orch.approve_checkpoint(run, approved=True, action="approve", feedback="Looks good, proceed!")
        # R9-FIX: approve now leaves status PAUSED
        assert run.status == PipelineRunStatus.PAUSED
        assert run.checkpoint_approved is True
        assert run.context["__user_feedback__"] == "Looks good, proceed!"
        assert len(run.context["__feedback_history__"]) == 1
        assert run.context["__feedback_history__"][0]["action"] == "approve"

    @pytest.mark.asyncio
    async def test_reject_with_feedback(self):
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        run.status = PipelineRunStatus.PAUSED
        run.current_stage = PipelineStage.CHECKPOINT_DESIGN

        await orch.approve_checkpoint(run, approved=False, action="reject", feedback="Bad design")
        assert run.status == PipelineRunStatus.FAILED
        assert run.context["__user_feedback__"] == "Bad design"

    @pytest.mark.asyncio
    async def test_redo_design_checkpoint_rewinds_to_architecture(self):
        """Redo at CHECKPOINT_DESIGN should rewind to ARCHITECTURE."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        run.status = PipelineRunStatus.PAUSED
        run.current_stage = PipelineStage.CHECKPOINT_DESIGN

        await orch.approve_checkpoint(
            run, approved=False, action="redo",
            feedback="Use microservices instead of monolith"
        )
        # R9-FIX: redo now leaves status PAUSED (resume_run restarts)
        assert run.status == PipelineRunStatus.PAUSED
        assert run.current_stage == PipelineStage.ARCHITECTURE
        assert run.context["__user_feedback__"] == "Use microservices instead of monolith"

    @pytest.mark.asyncio
    async def test_redo_testing_checkpoint_rewinds_to_quality_review(self):
        """Redo at CHECKPOINT_TESTING should rewind to QUALITY_REVIEW."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        run.status = PipelineRunStatus.PAUSED
        run.current_stage = PipelineStage.CHECKPOINT_TESTING

        await orch.approve_checkpoint(
            run, approved=False, action="redo",
            feedback="Fix the auth tests"
        )
        # R9-FIX: redo now leaves status PAUSED
        assert run.status == PipelineRunStatus.PAUSED
        assert run.current_stage == PipelineStage.QUALITY_REVIEW
        assert run.context["__user_feedback__"] == "Fix the auth tests"

    @pytest.mark.asyncio
    async def test_redo_from_non_checkpoint_raises(self):
        """Redo from a non-checkpoint stage raises ValueError.

        R8-FIX: Now the checkpoint stage validation fires first (before
        the redo target lookup), so the error message is different.
        """
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        run.status = PipelineRunStatus.PAUSED
        run.current_stage = PipelineStage.BACKEND_BUILD

        with pytest.raises(ValueError, match="not at a checkpoint stage"):
            await orch.approve_checkpoint(run, approved=False, action="redo")

    @pytest.mark.asyncio
    async def test_feedback_history_accumulates(self):
        """Multiple feedback entries accumulate in __feedback_history__."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        run.status = PipelineRunStatus.PAUSED
        run.current_stage = PipelineStage.CHECKPOINT_DESIGN

        await orch.approve_checkpoint(
            run, approved=False, action="redo", feedback="Try again"
        )
        # Simulate pipeline running back to checkpoint
        run.status = PipelineRunStatus.PAUSED
        run.current_stage = PipelineStage.CHECKPOINT_DESIGN

        await orch.approve_checkpoint(
            run, approved=True, action="approve", feedback="OK now"
        )

        assert len(run.context["__feedback_history__"]) == 2
        assert run.context["__feedback_history__"][0]["feedback"] == "Try again"
        assert run.context["__feedback_history__"][1]["feedback"] == "OK now"

    @pytest.mark.asyncio
    async def test_approve_without_feedback(self):
        """Approving without feedback should not create __user_feedback__ key."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        run.status = PipelineRunStatus.PAUSED
        run.current_stage = PipelineStage.CHECKPOINT_DESIGN

        await orch.approve_checkpoint(run, approved=True, action="approve", feedback="")
        # R9-FIX: approve now leaves status PAUSED
        assert run.status == PipelineRunStatus.PAUSED
        assert "__user_feedback__" not in run.context

    @pytest.mark.asyncio
    async def test_backward_compat_approve_bool(self):
        """Old-style boolean approve still works."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        run.status = PipelineRunStatus.PAUSED
        run.current_stage = PipelineStage.CHECKPOINT_DESIGN

        await orch.approve_checkpoint(run, approved=True)
        # R9-FIX: approve now leaves status PAUSED
        assert run.status == PipelineRunStatus.PAUSED
        assert run.checkpoint_approved is True


# -- Resume Tests -------------------------------------------------------------


class TestPipelineResume:
    """Test pipeline resume behavior."""

    @pytest.mark.asyncio
    @patch("app.services.pipeline.get_agent", side_effect=_mock_get_agent)
    async def test_resume_from_hot_cache(self, mock_agent):
        """Resume a pipeline that's still in the hot cache."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1", ExecutionMode.DIRECT)
        run.status = PipelineRunStatus.FAILED
        run.current_stage = PipelineStage.BACKEND_BUILD
        run.error = "Some transient error"

        resumed = await orch.resume_run(run.run_id)
        assert resumed is not None
        assert resumed.status == PipelineRunStatus.COMPLETED  # Direct mode runs to end

    @pytest.mark.asyncio
    async def test_resume_paused_at_checkpoint_returns_paused(self):
        """Resuming a pipeline paused at checkpoint should not auto-advance."""
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1", ExecutionMode.CHECKPOINT)
        run.status = PipelineRunStatus.PAUSED
        run.current_stage = PipelineStage.CHECKPOINT_DESIGN

        resumed = await orch.resume_run(run.run_id)
        assert resumed is not None
        assert resumed.status == PipelineRunStatus.PAUSED

    @pytest.mark.asyncio
    async def test_resume_nonexistent_returns_none(self):
        orch = PipelineOrchestrator()
        resumed = await orch.resume_run("nonexistent-id")
        assert resumed is None

    @pytest.mark.asyncio
    async def test_resume_completed_returns_as_is(self):
        orch = PipelineOrchestrator()
        run = orch.create_run("proj-1", "org-1", "user-1")
        run.status = PipelineRunStatus.COMPLETED
        resumed = await orch.resume_run(run.run_id)
        assert resumed is not None
        assert resumed.status == PipelineRunStatus.COMPLETED


# -- Recovery Tests -----------------------------------------------------------


class TestCrashRecovery:
    """Test crash recovery (find_interrupted + mark_interrupted + recover)."""

    @pytest.mark.asyncio
    async def test_recover_no_db_returns_zero(self):
        """If DB is not initialized, recovery returns 0."""
        orch = PipelineOrchestrator()
        count = await orch.recover_interrupted_runs()
        assert count == 0


# -- Singleton Tests ----------------------------------------------------------


# -- Resource Isolation Tests --------------------------------------------------


class TestProjectRateLimiter:
    """Test per-project rate limiting (in-memory path).

    R25-FIX: Patch _try_valkey_acquire to return False so these tests
    exercise the in-memory rate limiting path regardless of whether
    a real Valkey is running locally (which would bypass in-memory).
    """

    @pytest.mark.asyncio
    async def test_acquire_within_limit(self):
        limiter = ProjectRateLimiter(calls_per_minute=10)
        with patch.object(limiter, "_try_valkey_acquire", return_value=False):
            for _ in range(10):
                await limiter.acquire("proj-1")
        assert limiter.get_usage("proj-1") == 10

    @pytest.mark.asyncio
    async def test_different_projects_independent(self):
        limiter = ProjectRateLimiter(calls_per_minute=5)
        with patch.object(limiter, "_try_valkey_acquire", return_value=False):
            for _ in range(5):
                await limiter.acquire("proj-1")
            for _ in range(5):
                await limiter.acquire("proj-2")
        assert limiter.get_usage("proj-1") == 5
        assert limiter.get_usage("proj-2") == 5

    def test_reset(self):
        limiter = ProjectRateLimiter(calls_per_minute=10)
        limiter._buckets["proj-1"] = [0.0] * 10
        limiter.reset("proj-1")
        assert limiter.get_usage("proj-1") == 0

    def test_calls_per_minute_property(self):
        limiter = ProjectRateLimiter(calls_per_minute=42)
        assert limiter.calls_per_minute == 42


class TestResourceIsolation:
    """Test orchestrator resource isolation features."""

    def test_orchestrator_has_semaphore(self):
        orch = PipelineOrchestrator(max_concurrent=5)
        assert orch.max_concurrent == 5

    def test_orchestrator_has_rate_limiter(self):
        orch = PipelineOrchestrator(calls_per_minute=20)
        assert orch.rate_limiter is not None
        assert orch.rate_limiter.calls_per_minute == 20

    def test_orchestrator_has_timeout(self):
        orch = PipelineOrchestrator(timeout_minutes=60)
        assert orch.timeout_seconds == 3600

    @pytest.mark.asyncio
    @patch("app.services.pipeline.get_agent", side_effect=_mock_get_agent)
    async def test_pipeline_timeout(self, mock_agent):
        """Pipeline should fail gracefully on timeout."""
        # Use a very short timeout (1 second)
        orch = PipelineOrchestrator(timeout_minutes=1)
        # Override timeout to 0.1 seconds for fast test
        orch._timeout_seconds = 0.1

        # Make one agent sleep longer than timeout
        def slow_agent(name):
            agent = MagicMock()
            async def slow_run(*args, **kwargs):
                await asyncio.sleep(5)  # Will be cancelled by timeout
                return AgentResult(agent_name=name, status=AgentStatus.COMPLETED, output={})
            agent.run = slow_run
            return agent

        with patch("app.services.pipeline.get_agent", side_effect=slow_agent):
            run = orch.create_run("proj-1", "org-1", "user-1", ExecutionMode.DIRECT)
            result = await orch.run_pipeline(run)

        assert result.status == PipelineRunStatus.FAILED
        assert "timed out" in result.error.lower()

    @pytest.mark.asyncio
    @patch("app.services.pipeline.get_agent", side_effect=_mock_get_agent)
    async def test_concurrent_pipeline_limit(self, mock_agent):
        """Semaphore should limit concurrent pipelines."""
        import asyncio
        orch = PipelineOrchestrator(max_concurrent=2)

        runs_started = []

        async def run_one(idx):
            run = orch.create_run(f"proj-{idx}", "org-1", "user-1", ExecutionMode.DIRECT)
            runs_started.append(idx)
            return await orch.run_pipeline(run)

        # Run 3 pipelines concurrently (limit is 2)
        results = await asyncio.gather(run_one(1), run_one(2), run_one(3))

        # All should complete
        assert all(r.status == PipelineRunStatus.COMPLETED for r in results)


# -- Singleton Tests ----------------------------------------------------------


class TestSingleton:
    def test_get_orchestrator_singleton(self):
        o1 = get_orchestrator()
        o2 = get_orchestrator()
        assert o1 is o2

    def test_orchestrator_has_persistence(self):
        orch = get_orchestrator()
        assert orch.persistence is not None
        assert isinstance(orch.persistence, PipelinePersistence)
