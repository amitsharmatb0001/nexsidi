"""Pipeline API routes: start, status, checkpoint approval, resume.

All routes under /api/v1/pipeline/. Tenant-scoped.
"""

from __future__ import annotations

import asyncio
import uuid as _uuid

import structlog
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.config import get_settings
from app.dependencies import AdminContext, CurrentContext, TenantSession
from app.models.core import Project
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


def _sanitize_dispatch_error(exc: Exception) -> str:
    """R32-FIX: Strip potential credentials from Celery/broker error messages.

    Celery broker connection errors can include URIs with embedded passwords
    (e.g., redis://:password@host:6379/0). Truncate and redact.
    """
    import re
    msg = str(exc)[:300]
    # Redact any URL-like credential patterns
    msg = re.sub(r"://[^@]*@", "://***@", msg)
    msg = re.sub(r"password['\"]?\s*[:=]\s*['\"]?[^'\";\s]+", "password=***", msg, flags=re.IGNORECASE)
    return msg

router = APIRouter()

# R9-FIX: Strong references to running pipeline tasks. Without these,
# asyncio.create_task() returns a Task that may be garbage-collected
# mid-execution. The done callback cleans up completed tasks.
_active_tasks: set[asyncio.Task] = set()  # type: ignore[type-arg]


def _pipeline_task_done(task: asyncio.Task) -> None:  # type: ignore[type-arg]
    """Log unhandled exceptions from background pipeline tasks.

    DEFERRED-FIX-19: Wrapped in try/except to handle edge cases:
    - task.exception() can raise CancelledError if task was cancelled between
      our cancelled() check and exception() call (race window).
    - str(exc) can contain API keys from httpx errors — use _sanitize_error.
    """
    try:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            from app.services.ai_router import _sanitize_error
            logger.error("pipeline_task_failed", error=_sanitize_error(exc))
    except Exception:
        # Safety net: never let the done callback itself crash
        logger.error("pipeline_task_done_callback_error")


def _validate_run_id(run_id: str) -> str:
    """R-03-FIX: Validate run_id is a well-formed UUID to prevent 500s."""
    try:
        return str(_uuid.UUID(run_id))
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid run_id: must be a valid UUID",
        )


@router.post(
    "/start",
    response_model=PipelineStatusResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_pipeline(
    body: PipelineStartRequest,
    ctx: CurrentContext,
    session: TenantSession,
) -> PipelineStatusResponse:
    """Start a new pipeline run for a project.

    Creates a pipeline run and begins execution from the REQUIREMENTS stage.
    In CHECKPOINT mode (default), pauses at CHECKPOINT_DESIGN and CHECKPOINT_TESTING.

    R-08-FIX: Requires project owner or org admin role — viewers cannot trigger pipelines.
    """
    # VULN-FIX: Verify project belongs to the tenant before starting pipeline.
    # Without this, a user could start a pipeline for another org's project.
    # R28-FIX-6: Exclude archived projects — cannot start pipelines on soft-deleted projects.
    # R31-FIX-IDOR: Defense-in-depth — filter by organization_id at application
    # level. RLS alone is insufficient if the DB user bypasses policies.
    result = await session.execute(
        select(Project).where(
            Project.id == body.project_id,
            Project.organization_id == _uuid.UUID(ctx.organization_id),
            Project.status != "archived",
        )
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    # R-08-FIX: Only project owner or admins can start pipelines
    is_owner = str(project.owner_id) == ctx.user_id
    is_admin = ctx.role in ("org_admin", "super_admin")
    if not (is_owner or is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the project owner or an admin can start pipelines",
        )

    # R26-FIX-11: Prevent concurrent pipeline runs for the same project.
    # R27-FIX-6: Lock the project row with SELECT FOR UPDATE to prevent
    # TOCTOU race between the SELECT COUNT and the INSERT in _persist_run
    # (which uses its own session).  The lock holds until the tenant session
    # commits (endpoint return), by which time _persist_run has committed
    # the new run — so a concurrent request blocked on FOR UPDATE will then
    # see the active run in its COUNT check.
    from app.models.pipeline import PipelineRun as DBPipelineRun
    await session.execute(
        select(Project).where(
            Project.id == body.project_id,
            Project.organization_id == _uuid.UUID(ctx.organization_id),
        ).with_for_update()
    )
    # R35-FIX: Add organization_id filter for defense-in-depth (R31-R34
    # pattern). Without this, a RLS bypass (superuser connection) would
    # count pipeline runs from ALL organizations, causing false 409 Conflict.
    active_run_q = await session.execute(
        select(func.count())
        .select_from(DBPipelineRun)
        .where(
            DBPipelineRun.project_id == body.project_id,
            DBPipelineRun.organization_id == _uuid.UUID(ctx.organization_id),
            DBPipelineRun.status.in_(["running", "paused", "created"]),
        )
    )
    if active_run_q.scalar_one() > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A pipeline is already running for this project",
        )

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

    # R15-FIX: Persist the run to DB BEFORE dispatching to Celery or returning
    # the response. save_run() syncs run.run_id with the DB-generated UUID.
    # Without this, the Celery task receives the old in-memory UUID which
    # doesn't exist in the DB, breaking crash-resume. The HTTP response also
    # returns the old UUID, making the run_id useless for /status and /resume.
    await orch._persist_run(run)

    # R8-FIX: Actually start pipeline execution in the background.
    # Previously, create_run() only created the in-memory PipelineRun
    # object but never called run_pipeline(), leaving the run in
    # CREATED status forever.
    #
    # PHASE-4: When use_celery=True, dispatch to Celery worker for
    # horizontal scaling. Otherwise, use in-process asyncio.create_task().
    settings = get_settings()
    if settings.use_celery:
        from app.tasks.pipeline_tasks import run_pipeline_task

        # REVIEW-FIX: Wrap Celery dispatch in try/except. If the broker is
        # down or apply_async fails, the PipelineRun was already created in
        # the hot cache but never started — an orphan. Clean it up and
        # return 503 Service Unavailable instead of leaving a zombie run.
        try:
            run_pipeline_task.apply_async(
                kwargs={
                    "run_id": run.run_id,
                    "project_id": str(body.project_id),
                    "organization_id": ctx.organization_id,
                    "user_id": ctx.user_id,
                    "execution_mode": mode.value,
                    "requirements": body.requirements or None,
                },
                task_id=run.run_id,  # Use run_id as Celery task ID for correlation
            )
        except Exception as dispatch_exc:
            # Clean up orphan run from hot cache
            orch._active_runs.pop(run.run_id, None)
            # R28-FIX-4: Also mark the DB row as failed so it doesn't
            # permanently block future pipeline starts for this project
            # (the concurrent guard checks for status in running/paused/created).
            try:
                from app.services.pipeline import PipelineRunStatus
                run.status = PipelineRunStatus.FAILED
                run.error = "Celery dispatch failed"
                await orch._persist_run(run)
            except Exception:
                logger.error("dispatch_cleanup_persist_failed", run_id=run.run_id)
            logger.error(
                "celery_dispatch_failed",
                run_id=run.run_id,
                error=_sanitize_dispatch_error(dispatch_exc),
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Pipeline task queue unavailable. Please try again later.",
            )
        logger.info("pipeline_dispatched_to_celery", run_id=run.run_id)
    else:
        # R9-FIX: Store task reference to prevent GC and log unhandled exceptions.
        task = asyncio.create_task(orch.run_pipeline(run))
        task.add_done_callback(_pipeline_task_done)
        _active_tasks.add(task)
        task.add_done_callback(_active_tasks.discard)

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
    run_id = _validate_run_id(run_id)
    orch = get_orchestrator()
    run = orch.get_run(run_id)

    # R9-FIX: Completed/failed runs are evicted from the hot cache.
    # Fall back to DB so users can check final status after completion.
    if run is None:
        run_data = await orch.load_run_metadata(
            run_id, organization_id=ctx.organization_id,
        )
        if run_data is not None:
            run = run_data

    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pipeline run not found",
        )

    # R29-FIX-10: Normalize both sides to str. When run is loaded from DB via
    # load_run_metadata, organization_id may be uuid.UUID, while ctx.organization_id
    # is always str. UUID("x") != "x" in Python → false 404 for all DB-loaded runs.
    if str(run.organization_id) != str(ctx.organization_id):
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
    ctx: AdminContext,
) -> PipelineStatusResponse:
    """Approve or reject the current checkpoint.

    VULN-1-FIX: Requires org_admin or super_admin role.
    """
    run_id = _validate_run_id(run_id)
    orch = get_orchestrator()
    run = orch.get_run(run_id)

    # REVIEW-FIX: Fall back to DB if run not in hot cache. Paused runs
    # should be in cache, but after server restart they're evicted.
    # load_run_metadata reconstructs the PipelineRun from DB so the
    # approve call can proceed.
    if run is None:
        run = await orch.load_run_metadata(run_id, organization_id=ctx.organization_id)
        if run is not None:
            # Re-add to hot cache so approve_checkpoint can persist changes
            orch._active_runs[run.run_id] = run

    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pipeline run not found",
        )

    # R29-FIX-10: Same UUID vs str normalization as get_pipeline_status.
    if str(run.organization_id) != str(ctx.organization_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pipeline run not found",
        )

    if run.status != PipelineRunStatus.PAUSED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pipeline is not paused at a checkpoint",
        )

    # R20-FIX: Catch ValueError from service layer TOCTOU race.
    # Between the status check above and approve_checkpoint(), a concurrent
    # request or Celery task can change run.status/current_stage. The service
    # raises ValueError, which propagated as unhandled 500 (client retries
    # endlessly thinking it's transient). Map to 409 Conflict instead.
    try:
        await orch.approve_checkpoint(
            run,
            approved=body.approved,
            action=body.action,
            feedback=body.feedback,
        )
    except ValueError as exc:
        # R26-FIX-33: Don't leak internal stage names in error detail.
        logger.warning("approve_checkpoint_conflict", run_id=run_id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Pipeline is not in an approvable state",
        )

    logger.info(
        "checkpoint_decision",
        run_id=run_id,
        action=body.action,
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


@router.post("/{run_id}/resume", response_model=PipelineStatusResponse)
async def resume_pipeline(
    run_id: str,
    ctx: AdminContext,
) -> PipelineStatusResponse:
    """Resume an interrupted or failed pipeline from the last completed stage.

    After a server crash, pipelines are marked as 'interrupted'. This endpoint
    rebuilds the pipeline state from the database and resumes execution.

    VULN-1-FIX: Requires org_admin or super_admin role.
    """
    run_id = _validate_run_id(run_id)
    orch = get_orchestrator()

    # C3-FIX: Verify tenant ownership BEFORE executing resume.
    # Previously, when `run` was None in hot cache (e.g. after restart),
    # `orch.resume_run()` would rebuild AND start executing the pipeline
    # before the ownership check — allowing cross-tenant pipeline execution.
    #
    # Now we always verify ownership first:
    # 1. Check hot cache
    # 2. If not cached, load metadata from DB (without executing)
    # 3. Verify ownership
    # 4. Only THEN resume execution
    run = orch.get_run(run_id)

    if run is None:
        # Not in hot cache — load from DB to check ownership before resuming
        # RLS-READ-FIX: Pass organization_id for tenant-scoped DB lookup
        run = await orch.load_run_metadata(run_id, organization_id=ctx.organization_id)

    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pipeline run not found or not resumable",
        )

    # R29-FIX-10: Same UUID vs str normalization.
    if str(run.organization_id) != str(ctx.organization_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pipeline run not found",
        )

    # R28-FIX-13: Validate that the run is in a resumable state BEFORE
    # dispatching to Celery. Without this, COMPLETED/CANCELLED runs get
    # a Celery task dispatched (wasting broker resources), and RUNNING runs
    # risk double-execution if the Celery worker's resume_run() TOCTOU
    # window is hit. The in-process path (asyncio.create_task → resume_run)
    # validates internally, but Celery dispatch is fire-and-forget.
    _RESUMABLE_STATUSES = {
        PipelineRunStatus.PAUSED,
        PipelineRunStatus.FAILED,
        PipelineRunStatus.INTERRUPTED,
    }
    if run.status not in _RESUMABLE_STATUSES:
        if run.status == PipelineRunStatus.RUNNING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Pipeline is already running",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            # R29-FIX-9: Don't leak internal status value (R26-FIX-33 pattern).
            detail="Pipeline cannot be resumed from its current status",
        )

    # Ownership verified — safe to resume execution.
    # R15-FIX: Resume must dispatch to background (Celery or asyncio.create_task),
    # NOT await inline. resume_run() executes the entire remaining pipeline
    # (potentially hours of AI calls). Awaiting it blocks the HTTP request,
    # causing reverse proxy timeouts (502/504) and making resume unusable.
    settings = get_settings()
    if settings.use_celery:
        from app.tasks.pipeline_tasks import resume_pipeline_task

        # R33-FIX: Set status=RUNNING and persist BEFORE Celery dispatch.
        # Without this, two rapid /resume calls both see status=PAUSED,
        # both pass the _RESUMABLE_STATUSES check, and both dispatch Celery
        # tasks — causing duplicate pipeline execution. Setting RUNNING first
        # ensures the second request sees RUNNING → 409 Conflict above.
        original_status = run.status
        run.status = PipelineRunStatus.RUNNING
        orch._active_runs[run.run_id] = run
        try:
            await orch._persist_run(run)
        except Exception:
            # Revert on persist failure
            run.status = original_status
            logger.error("resume_persist_failed", run_id=run_id)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Failed to update pipeline state.",
            )

        try:
            resume_pipeline_task.apply_async(
                kwargs={
                    "run_id": run_id,
                    "organization_id": ctx.organization_id,
                },
                task_id=f"resume-{run_id}",
            )
        except Exception as dispatch_exc:
            # R33-FIX: Revert status on Celery dispatch failure so the run
            # can be retried. Without revert, a failed dispatch leaves the run
            # permanently stuck in RUNNING (not resumable, not retryable).
            run.status = original_status
            try:
                await orch._persist_run(run)
            except Exception:
                logger.error("resume_revert_persist_failed", run_id=run_id)
            logger.error(
                "celery_resume_dispatch_failed",
                run_id=run_id,
                error=_sanitize_dispatch_error(dispatch_exc),
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Pipeline task queue unavailable. Please try again later.",
            )
        logger.info("pipeline_resume_dispatched_to_celery", run_id=run_id)
    else:
        # R35-FIX: Apply same double-execution guard as Celery path (R33-FIX).
        # Without this, two rapid /resume calls in non-Celery mode both see
        # status=PAUSED, both pass _RESUMABLE_STATUSES, and both dispatch
        # asyncio.create_task — causing duplicate pipeline execution, doubled
        # AI costs, and race conditions corrupting pipeline state.
        original_status = run.status
        run.status = PipelineRunStatus.RUNNING
        orch._active_runs[run.run_id] = run
        try:
            await orch._persist_run(run)
        except Exception:
            run.status = original_status
            logger.error("resume_persist_failed", run_id=run_id)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Failed to update pipeline state.",
            )

        # RLS-READ-FIX: Pass org_id for defense-in-depth tenant scoping
        task = asyncio.create_task(
            orch.resume_run(run_id, organization_id=ctx.organization_id)
        )
        task.add_done_callback(_pipeline_task_done)
        _active_tasks.add(task)
        task.add_done_callback(_active_tasks.discard)
        logger.info("pipeline_resume_dispatched", run_id=run_id)

    # Return current run state (execution continues in background)
    return PipelineStatusResponse(
        run_id=run.run_id,
        project_id=run.project_id,
        status=run.status.value,
        current_stage=run.current_stage.value,
        execution_mode=run.execution_mode.value,
        created_at=run.created_at.isoformat(),
        error=run.error,
    )
