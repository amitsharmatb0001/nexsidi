"""Pipeline orchestrator: manages the 17-step agent execution chain.

Coordinates agent execution order, checkpoint pauses, parallel quality gates,
and the fixer retry loop. Designed to work with Temporal for durable execution
(crash recovery, non-blocking waits) in production.

Execution modes:
- STEP_BY_STEP: Pauses after every agent (for debugging/demo)
- CHECKPOINT: Pauses at CHECKPOINT 1 and CHECKPOINT 2 (default)
- DIRECT: No pauses (for trusted automated runs)

Pipeline stages:
    REQUIREMENTS → ANALYSIS → ARCHITECTURE → DATABASE_DESIGN →
    UI_DESIGN → CHECKPOINT_DESIGN → BACKEND_BUILD → FRONTEND_BUILD →
    QUALITY_REVIEW (parallel) → TESTING → SECURITY_AUDIT →
    COMPLIANCE_CHECK → FIXING (loop) → CHECKPOINT_TESTING →
    DEPLOYMENT → DELIVERY → COMPLETED
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog

from app.agents.base import AgentResult, AgentStatus, get_agent

logger = structlog.get_logger(__name__)


# ── Pipeline Stages ─────────────────────────────────────────────────


class PipelineStage(str, Enum):
    """Ordered stages of the NexSidi pipeline."""

    REQUIREMENTS = "requirements"
    ANALYSIS = "analysis"
    ARCHITECTURE = "architecture"
    DATABASE_DESIGN = "database_design"
    UI_DESIGN = "ui_design"
    CHECKPOINT_DESIGN = "checkpoint_design"
    BACKEND_BUILD = "backend_build"
    FRONTEND_BUILD = "frontend_build"
    QUALITY_REVIEW = "quality_review"
    TESTING = "testing"
    SECURITY_AUDIT = "security_audit"
    COMPLIANCE_CHECK = "compliance_check"
    FIXING = "fixing"
    CHECKPOINT_TESTING = "checkpoint_testing"
    DEPLOYMENT = "deployment"
    DELIVERY = "delivery"
    COMPLETED = "completed"


# Stage → agent name mapping
STAGE_AGENTS: dict[PipelineStage, str | list[str]] = {
    PipelineStage.REQUIREMENTS: "tilotma",
    PipelineStage.ANALYSIS: "saanvi",
    PipelineStage.ARCHITECTURE: "vikram",
    PipelineStage.DATABASE_DESIGN: "dhruv",
    PipelineStage.UI_DESIGN: "vanya",
    PipelineStage.CHECKPOINT_DESIGN: "__checkpoint_1__",
    PipelineStage.BACKEND_BUILD: "shubham",
    PipelineStage.FRONTEND_BUILD: "aanya",
    PipelineStage.QUALITY_REVIEW: ["karan", "navya", "deepika"],  # Parallel
    PipelineStage.TESTING: "aarav",
    PipelineStage.SECURITY_AUDIT: "karan",
    PipelineStage.COMPLIANCE_CHECK: "karan",
    PipelineStage.FIXING: "fixer",
    PipelineStage.CHECKPOINT_TESTING: "__checkpoint_2__",
    PipelineStage.DEPLOYMENT: "pranav",
    PipelineStage.DELIVERY: "__delivery__",
    PipelineStage.COMPLETED: "__done__",
}

# Stages that are checkpoints (require user approval)
CHECKPOINT_STAGES: frozenset[PipelineStage] = frozenset({
    PipelineStage.CHECKPOINT_DESIGN,
    PipelineStage.CHECKPOINT_TESTING,
})

# Ordered stage list for sequential execution
STAGE_ORDER: list[PipelineStage] = list(PipelineStage)


# ── Execution Mode ──────────────────────────────────────────────────


class ExecutionMode(str, Enum):
    STEP_BY_STEP = "step_by_step"  # Pause after every agent
    CHECKPOINT = "checkpoint"  # Pause at checkpoints only (default)
    DIRECT = "direct"  # No pauses


# ── Pipeline Run State ──────────────────────────────────────────────


class PipelineRunStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"  # Waiting at checkpoint
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class StepResult:
    """Result of a single pipeline step execution."""

    stage: PipelineStage
    agent_name: str
    result: AgentResult | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    skipped: bool = False


@dataclass(slots=True)
class PipelineRun:
    """State of a single pipeline execution."""

    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str = ""
    organization_id: str = ""
    user_id: str = ""
    status: PipelineRunStatus = PipelineRunStatus.CREATED
    execution_mode: ExecutionMode = ExecutionMode.CHECKPOINT
    current_stage: PipelineStage = PipelineStage.REQUIREMENTS
    step_results: list[StepResult] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error: str | None = None

    # Checkpoint state
    checkpoint_data: dict[str, Any] | None = None
    checkpoint_approved: bool = False


# ── Pipeline Orchestrator ───────────────────────────────────────────


class PipelineOrchestrator:
    """Orchestrates the 17-step agent pipeline.

    In production this will be backed by Temporal for durable execution.
    This implementation provides the core sequencing logic.
    """

    def __init__(self) -> None:
        self._active_runs: dict[str, PipelineRun] = {}

    def create_run(
        self,
        project_id: str,
        organization_id: str,
        user_id: str,
        execution_mode: ExecutionMode = ExecutionMode.CHECKPOINT,
    ) -> PipelineRun:
        """Create a new pipeline run."""
        run = PipelineRun(
            project_id=project_id,
            organization_id=organization_id,
            user_id=user_id,
            execution_mode=execution_mode,
        )
        self._active_runs[run.run_id] = run
        logger.info(
            "pipeline_created",
            run_id=run.run_id,
            project_id=project_id,
            mode=execution_mode.value,
        )
        return run

    def get_run(self, run_id: str) -> PipelineRun | None:
        """Get a pipeline run by ID."""
        return self._active_runs.get(run_id)

    async def execute_stage(self, run: PipelineRun) -> StepResult:
        """Execute the current stage of the pipeline.

        Returns the StepResult. If the stage is a checkpoint, pauses
        the run and returns a WAITING_USER result.
        """
        stage = run.current_stage
        agent_name = STAGE_AGENTS.get(stage)

        # Handle checkpoints
        if stage in CHECKPOINT_STAGES:
            return await self._handle_checkpoint(run, stage)

        # Handle completion
        if stage == PipelineStage.COMPLETED:
            run.status = PipelineRunStatus.COMPLETED
            return StepResult(
                stage=stage,
                agent_name="__done__",
                completed_at=datetime.now(timezone.utc),
            )

        # Handle special stages
        if isinstance(agent_name, str) and agent_name.startswith("__"):
            return StepResult(
                stage=stage,
                agent_name=agent_name,
                completed_at=datetime.now(timezone.utc),
                skipped=True,
            )

        # Handle parallel stages (quality review)
        if isinstance(agent_name, list):
            return await self._execute_parallel(run, stage, agent_name)

        # Execute single agent
        return await self._execute_agent(run, stage, agent_name)

    async def advance(self, run: PipelineRun) -> PipelineStage | None:
        """Advance to the next stage. Returns the new stage or None if complete."""
        current_idx = STAGE_ORDER.index(run.current_stage)
        if current_idx >= len(STAGE_ORDER) - 1:
            run.status = PipelineRunStatus.COMPLETED
            return None

        next_stage = STAGE_ORDER[current_idx + 1]
        run.current_stage = next_stage

        logger.info(
            "pipeline_advance",
            run_id=run.run_id,
            from_stage=STAGE_ORDER[current_idx].value,
            to_stage=next_stage.value,
        )
        return next_stage

    async def approve_checkpoint(self, run: PipelineRun, approved: bool) -> None:
        """Approve or reject the current checkpoint."""
        if run.status != PipelineRunStatus.PAUSED:
            raise ValueError(f"Pipeline {run.run_id} is not paused at a checkpoint")

        run.checkpoint_approved = approved
        if approved:
            run.status = PipelineRunStatus.RUNNING
            logger.info("checkpoint_approved", run_id=run.run_id, stage=run.current_stage.value)
        else:
            run.status = PipelineRunStatus.FAILED
            run.error = "Checkpoint rejected by user"
            logger.info("checkpoint_rejected", run_id=run.run_id, stage=run.current_stage.value)

    async def run_pipeline(self, run: PipelineRun) -> PipelineRun:
        """Execute the full pipeline from current stage to completion or pause.

        Runs stages sequentially, stopping at checkpoints (in CHECKPOINT mode)
        or after every stage (in STEP_BY_STEP mode).
        """
        run.status = PipelineRunStatus.RUNNING

        while run.current_stage != PipelineStage.COMPLETED:
            step_result = await self.execute_stage(run)
            run.step_results.append(step_result)

            # Check if we need to pause
            if run.status == PipelineRunStatus.PAUSED:
                break

            # Check for failure
            if step_result.result and step_result.result.status == AgentStatus.FAILED:
                run.status = PipelineRunStatus.FAILED
                run.error = step_result.result.error
                break

            # Step-by-step mode: pause after every stage
            if run.execution_mode == ExecutionMode.STEP_BY_STEP:
                run.status = PipelineRunStatus.PAUSED
                break

            # Advance to next stage
            next_stage = await self.advance(run)
            if next_stage is None:
                break

        return run

    # ── Internal Methods ────────────────────────────────────────────

    async def _execute_agent(
        self, run: PipelineRun, stage: PipelineStage, agent_name: str
    ) -> StepResult:
        """Execute a single agent."""
        step = StepResult(
            stage=stage,
            agent_name=agent_name,
            started_at=datetime.now(timezone.utc),
        )

        try:
            agent = get_agent(agent_name)
        except KeyError:
            # Agent not yet implemented — skip with warning
            logger.warning("agent_not_found", agent=agent_name, stage=stage.value)
            step.skipped = True
            step.completed_at = datetime.now(timezone.utc)
            return step

        result = await agent.run(run.run_id, run.context)
        step.result = result
        step.completed_at = datetime.now(timezone.utc)

        # Add output to accumulated context
        if result.status == AgentStatus.COMPLETED and result.output:
            run.context[agent_name] = result.output

        return step

    async def _execute_parallel(
        self, run: PipelineRun, stage: PipelineStage, agent_names: list[str]
    ) -> StepResult:
        """Execute multiple agents in parallel (e.g., quality gates)."""
        import asyncio

        step = StepResult(
            stage=stage,
            agent_name=",".join(agent_names),
            started_at=datetime.now(timezone.utc),
        )

        tasks = []
        for name in agent_names:
            try:
                agent = get_agent(name)
                tasks.append(agent.run(run.run_id, run.context))
            except KeyError:
                logger.warning("agent_not_found", agent=name, stage=stage.value)

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            combined_output: dict[str, Any] = {}
            for name, res in zip(agent_names, results):
                if isinstance(res, AgentResult):
                    combined_output[name] = res.output
                elif isinstance(res, Exception):
                    combined_output[name] = {"error": str(res)}

            step.result = AgentResult(
                agent_name=step.agent_name,
                status=AgentStatus.COMPLETED,
                output=combined_output,
            )
        else:
            step.skipped = True

        step.completed_at = datetime.now(timezone.utc)
        return step

    async def _handle_checkpoint(
        self, run: PipelineRun, stage: PipelineStage
    ) -> StepResult:
        """Handle a checkpoint stage — pause pipeline for user approval."""
        step = StepResult(
            stage=stage,
            agent_name="__checkpoint__",
            started_at=datetime.now(timezone.utc),
        )

        if run.execution_mode == ExecutionMode.DIRECT:
            # Direct mode: auto-approve
            step.completed_at = datetime.now(timezone.utc)
            step.skipped = True
            return step

        # Pause for user approval
        run.status = PipelineRunStatus.PAUSED
        run.checkpoint_data = {
            "stage": stage.value,
            "context_summary": {k: type(v).__name__ for k, v in run.context.items()},
        }

        step.result = AgentResult(
            agent_name="__checkpoint__",
            status=AgentStatus.WAITING_USER,
            output={"checkpoint": stage.value, "message": "Waiting for user approval"},
        )
        step.completed_at = datetime.now(timezone.utc)

        logger.info(
            "checkpoint_paused",
            run_id=run.run_id,
            stage=stage.value,
        )
        return step


# ── Singleton ───────────────────────────────────────────────────────

_orchestrator: PipelineOrchestrator | None = None


def get_orchestrator() -> PipelineOrchestrator:
    """Get or create the pipeline orchestrator singleton."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = PipelineOrchestrator()
    return _orchestrator
