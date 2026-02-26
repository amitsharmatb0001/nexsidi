"""Pipeline API routes: start, status, checkpoint approval.

All routes under /api/v1/pipeline/. Tenant-scoped.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, status

from app.dependencies import CurrentContext
from app.schemas.project import (
    CheckpointApprovalRequest,
    PipelineStartRequest,
    PipelineStatusResponse,
)
from app.services.pipeline import (
    ExecutionMode,
    PipelineRunStatus,
    get_orchestrator,
)

logger = structlog.get_logger(__name__)

router = APIRouter()


@router.post(
    "/start",
    response_model=PipelineStatusResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_pipeline(
    body: PipelineStartRequest,
    ctx: CurrentContext,
) -> PipelineStatusResponse:
    """Start a new pipeline run for a project.

    Creates a pipeline run and begins execution from the REQUIREMENTS stage.
    In CHECKPOINT mode (default), pauses at CHECKPOINT_DESIGN and CHECKPOINT_TESTING.
    """
    orch = get_orchestrator()

    mode = ExecutionMode(body.execution_mode)
    run = orch.create_run(
        project_id=str(body.project_id),
        organization_id=ctx.organization_id,
        user_id=ctx.user_id,
        execution_mode=mode,
    )

    # Store requirements in context if provided
    if body.requirements:
        run.context["__requirements__"] = body.requirements

    logger.info(
        "pipeline_start_requested",
        run_id=run.run_id,
        project_id=str(body.project_id),
        mode=mode.value,
    )

    return PipelineStatusResponse(
        run_id=run.run_id,
        project_id=str(body.project_id),
        status=run.status.value,
        current_stage=run.current_stage.value,
        execution_mode=run.execution_mode.value,
        created_at=run.created_at.isoformat(),
    )


@router.get("/{run_id}", response_model=PipelineStatusResponse)
async def get_pipeline_status(
    run_id: str,
    ctx: CurrentContext,
) -> PipelineStatusResponse:
    """Get the current status of a pipeline run."""
    orch = get_orchestrator()
    run = orch.get_run(run_id)

    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pipeline run not found",
        )

    # Verify tenant ownership
    if run.organization_id != ctx.organization_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pipeline run not found",
        )

    step_results = [
        {
            "stage": sr.stage.value,
            "agent": sr.agent_name,
            "status": sr.result.status.value if sr.result else ("skipped" if sr.skipped else "pending"),
            "started_at": sr.started_at.isoformat() if sr.started_at else None,
            "completed_at": sr.completed_at.isoformat() if sr.completed_at else None,
        }
        for sr in run.step_results
    ]

    return PipelineStatusResponse(
        run_id=run.run_id,
        project_id=run.project_id,
        status=run.status.value,
        current_stage=run.current_stage.value,
        execution_mode=run.execution_mode.value,
        created_at=run.created_at.isoformat(),
        step_results=step_results,
        error=run.error,
    )


@router.post("/{run_id}/approve")
async def approve_checkpoint(
    run_id: str,
    body: CheckpointApprovalRequest,
    ctx: CurrentContext,
) -> PipelineStatusResponse:
    """Approve or reject the current checkpoint."""
    orch = get_orchestrator()
    run = orch.get_run(run_id)

    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pipeline run not found",
        )

    if run.organization_id != ctx.organization_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pipeline run not found",
        )

    if run.status != PipelineRunStatus.PAUSED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pipeline is not paused at a checkpoint",
        )

    await orch.approve_checkpoint(run, body.approved)

    logger.info(
        "checkpoint_decision",
        run_id=run_id,
        approved=body.approved,
        user_id=ctx.user_id,
    )

    return PipelineStatusResponse(
        run_id=run.run_id,
        project_id=run.project_id,
        status=run.status.value,
        current_stage=run.current_stage.value,
        execution_mode=run.execution_mode.value,
        created_at=run.created_at.isoformat(),
        error=run.error,
    )
