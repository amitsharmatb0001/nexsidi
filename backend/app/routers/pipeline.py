"""Pipeline API routes: start, status, checkpoint approval, resume.

All routes under /api/v1/pipeline/. Tenant-scoped.
"""

from __future__ import annotations

import asyncio
import io as _io
import posixpath
import uuid as _uuid

import structlog
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.config import get_settings
from app.dependencies import AdminContext, ApiKeyContext, CurrentContext, TenantSession
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


def _simulation_flags(run) -> tuple[bool, dict]:
    """Compute is_partially_simulated and simulation_summary from run context.

    Aggregates is_simulation_* flags set by individual agents:
    - aarav (testing):    is_simulation_sandbox — Docker not available
    - git_agent (git):    is_simulation_git — no real GitHub token/API calls
    - pranav (deploy):    is_simulation_deploy — no real cloud deployment

    Returns (is_partially_simulated, simulation_summary) where
    is_partially_simulated is True if ANY of the three flags is True.
    simulation_summary carries per-subsystem detail for UI banner rendering.
    """
    ctx = getattr(run, "context", None) or {}
    sandbox = bool(ctx.get("aarav", {}).get("is_simulation_sandbox", False))
    git = bool(ctx.get("git_agent", {}).get("is_simulation_git", False))
    deploy = bool(ctx.get("pranav", {}).get("is_simulation_deploy", False))
    partially = sandbox or git or deploy
    summary: dict = {}
    if partially:
        summary = {
            "sandbox_simulated": sandbox,
            "git_simulated": git,
            "deploy_simulated": deploy,
        }
    return partially, summary


@router.post(
    "/start",
    response_model=PipelineStatusResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_pipeline(
    body: PipelineStartRequest,
    ctx: ApiKeyContext,  # F-1-FIX: Accept both JWT and X-Api-Key authentication
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
    # R27-FIX-6: Originally used SELECT FOR UPDATE on the project row to prevent
    # TOCTOU race. REMOVED because _persist_run() uses a SEPARATE session for the
    # PipelineRun INSERT. The FK check (project_id -> core.projects) needs
    # FOR KEY SHARE on the referenced project row, but FOR UPDATE blocks
    # FOR KEY SHARE — causing a cross-session deadlock.
    # The active_run_q count check below is sufficient to prevent concurrent runs.
    # The worst-case TOCTOU race (two near-simultaneous starts) is extremely
    # unlikely and caught by the orchestrator's in-memory _active_runs guard.
    from app.models.pipeline import PipelineRun as DBPipelineRun
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

    # GIT-FIX: Store git_config in context so pipeline can use it for
    # per-stage commits and auto-PR creation.
    if body.git_config:
        run.context["git_config"] = body.git_config

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

    _partially_simulated, _sim_summary = _simulation_flags(run)
    return PipelineStatusResponse(
        run_id=run.run_id,
        project_id=str(body.project_id),
        status=run.status.value,
        current_stage=run.current_stage.value,
        execution_mode=run.execution_mode.value,
        created_at=run.created_at.isoformat(),
        is_partially_simulated=_partially_simulated,
        simulation_summary=_sim_summary,
    )


@router.get("/{run_id}", response_model=PipelineStatusResponse)
async def get_pipeline_status(
    run_id: str,
    ctx: ApiKeyContext,  # F-1-FIX: Accept both JWT and X-Api-Key
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

    _partially_simulated, _sim_summary = _simulation_flags(run)
    return PipelineStatusResponse(
        run_id=run.run_id,
        project_id=run.project_id,
        status=run.status.value,
        current_stage=run.current_stage.value,
        execution_mode=run.execution_mode.value,
        created_at=run.created_at.isoformat(),
        step_results=step_results,
        error=run.error,
        is_partially_simulated=_partially_simulated,
        simulation_summary=_sim_summary,
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

    _partially_simulated, _sim_summary = _simulation_flags(run)
    return PipelineStatusResponse(
        run_id=run.run_id,
        project_id=run.project_id,
        status=run.status.value,
        current_stage=run.current_stage.value,
        execution_mode=run.execution_mode.value,
        created_at=run.created_at.isoformat(),
        error=run.error,
        is_partially_simulated=_partially_simulated,
        simulation_summary=_sim_summary,
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
    _partially_simulated, _sim_summary = _simulation_flags(run)
    return PipelineStatusResponse(
        run_id=run.run_id,
        project_id=run.project_id,
        status=run.status.value,
        current_stage=run.current_stage.value,
        execution_mode=run.execution_mode.value,
        created_at=run.created_at.isoformat(),
        error=run.error,
        is_partially_simulated=_partially_simulated,
        simulation_summary=_sim_summary,
    )


# ── IDE-FIX Phase 1 Endpoints ────────────────────────────────────────────────


def _lang_from_ext(filename: str) -> str:
    """IDE-FIX: Map file extension to language identifier for syntax highlighting."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return {
        "py": "python",
        "ts": "typescript",
        "tsx": "typescript",
        "js": "javascript",
        "jsx": "javascript",
        "sql": "sql",
        "html": "html",
        "css": "css",
        "json": "json",
    }.get(ext, ext)


@router.get("/{run_id}/files/tree")
async def get_file_tree(
    run_id: str,
    ctx: CurrentContext,
) -> dict:
    """Return the hierarchical file tree for a completed/running pipeline run.

    IDE-FIX: Enables the frontend IDE sidebar to show the project file structure
    while the pipeline is running or after it completes.
    """
    run_id = _validate_run_id(run_id)
    orch = get_orchestrator()
    run = orch.get_run(run_id)

    # Also check DB if not in hot cache
    if run is None:
        run = await orch.load_run_metadata(run_id, organization_id=ctx.organization_id)

    if run is None or str(run.organization_id) != str(ctx.organization_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline run not found")

    # Collect files from all agents
    all_files: dict[str, str] = {}
    for agent_key in ("shubham", "aanya", "dhruv"):
        agent_ctx = run.context.get(agent_key, {})
        if isinstance(agent_ctx, dict):
            files = agent_ctx.get("file_contents") or agent_ctx.get("sql_files", {})
            if isinstance(files, dict):
                all_files.update(files)

    # Build hierarchical tree
    def _build_tree(paths: dict[str, str]) -> dict:
        tree: dict = {}
        for file_path, content in paths.items():
            parts = file_path.replace("\\", "/").split("/")
            node = tree
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = {
                "type": "file",
                "size": len(content.encode("utf-8")),
                "language": _lang_from_ext(parts[-1]),
            }
        return tree

    return {
        "run_id": run_id,
        "total_files": len(all_files),
        "tree": _build_tree(all_files),
        "flat": [
            {
                "path": p,
                "size": len(c.encode("utf-8")),
                "language": _lang_from_ext(p.split("/")[-1]),
            }
            for p, c in all_files.items()
        ],
    }


@router.get("/{run_id}/file")
async def get_file_content(
    run_id: str,
    path: str,
    ctx: CurrentContext,
) -> dict:
    """Return the content of a single generated file.

    IDE-FIX: Enables the frontend Monaco/CodeMirror editor to display
    individual files with syntax highlighting.

    Query parameter: ?path=backend/app/models.py
    """
    run_id = _validate_run_id(run_id)
    if not path or len(path) > 500:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="path query parameter required (max 500 chars)",
        )
    # Sanitise path — defeat ALL traversal variants including URL-encoded ones.
    #
    # Why the old ".." string check was insufficient:
    #   FastAPI percent-decodes query params before our handler runs, so
    #   "..%2Fetc%2Fpasswd" arrives as "../etc/passwd" and would be caught —
    #   but double-encoded "..%252Fetc" → first decode gives "..%2Fetc", which
    #   contains ".." and IS caught.  However, variants like "....//",
    #   "..//", or OS-specific separators could still sneak through a simple
    #   string check.  Using posixpath.normpath with an anchored root is the
    #   correct fix: it resolves ALL sequences to a canonical path.
    #
    # Method: prepend "/" to force absolute path, normpath resolves all "../"
    # sequences relative to "/", then strip the leading "/" back off.
    # Any traversal above root collapses to "/" which becomes "" after strip.
    # Null bytes are rejected explicitly (defence against C-string truncation).
    if "\x00" in path:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid path",
        )
    # normalise: forward-slash only, collapse traversal sequences
    normalised = posixpath.normpath("/" + path.replace("\\", "/")).lstrip("/")
    # After normalisation a traversal attempt either collapses to a path inside
    # the virtual root (safe) or to "" / starts with ".." (should not happen
    # after anchored normpath, but guard anyway).
    if not normalised or normalised.startswith(".."):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Path traversal not allowed",
        )
    safe_path = normalised

    orch = get_orchestrator()
    run = orch.get_run(run_id)
    if run is None:
        run = await orch.load_run_metadata(run_id, organization_id=ctx.organization_id)

    if run is None or str(run.organization_id) != str(ctx.organization_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline run not found")

    for agent_key in ("shubham", "aanya", "dhruv"):
        agent_ctx = run.context.get(agent_key, {})
        if isinstance(agent_ctx, dict):
            files = agent_ctx.get("file_contents") or agent_ctx.get("sql_files", {})
            if isinstance(files, dict) and safe_path in files:
                content = files[safe_path]
                return {
                    "path": safe_path,
                    "content": content,
                    "language": _lang_from_ext(safe_path.split("/")[-1]),
                    "size": len(content.encode("utf-8")),
                    "agent": agent_key,
                }

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"File not found: {safe_path}")


@router.get("/{run_id}/download")
async def download_project(
    run_id: str,
    ctx: CurrentContext,
) -> StreamingResponse:
    """Stream the generated project as a ZIP archive.

    IDE-FIX: Allows users to download the complete generated project.
    Rebuilds the ZIP on-demand from the persisted context_snapshot.
    """
    run_id = _validate_run_id(run_id)
    orch = get_orchestrator()
    run = orch.get_run(run_id)
    if run is None:
        run = await orch.load_run_metadata(run_id, organization_id=ctx.organization_id)

    if run is None or str(run.organization_id) != str(ctx.organization_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline run not found")

    if run.status not in (PipelineRunStatus.COMPLETED, PipelineRunStatus.PAUSED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Project download unavailable — pipeline status: {run.status.value}. "
                "Wait for pipeline to complete."
            ),
        )

    # ARTIFACT-FIX: Try DB-stored artifact first (fast path).
    # Falls back to on-demand rebuild from context_snapshot if not stored yet.
    from app.database import get_admin_session_factory, get_session_factory
    from sqlalchemy import text as _text

    factory = get_admin_session_factory() or get_session_factory()
    zip_bytes: bytes | None = None
    async with factory() as _session:
        async with _session.begin():
            row = await _session.execute(
                _text("SELECT artifact_data FROM pipeline.runs WHERE id = :id::uuid"),
                {"id": run_id},
            )
            result = row.fetchone()
            if result and result[0]:
                zip_bytes = bytes(result[0])

    if not zip_bytes:
        # On-demand rebuild from context_snapshot
        from app.engine.delivery import DeliveryEngine  # IDE-FIX: local import avoids circular deps
        engine = DeliveryEngine()
        try:
            package = engine.build_package(run_id, run.context)
            zip_bytes = package.zip_bytes
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to build download package: {exc}",
            ) from exc

    filename = f"nexsidi-project-{run_id[:8]}.zip"

    return StreamingResponse(
        _io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(zip_bytes)),
        },
    )


@router.post("/{run_id}/cancel", status_code=status.HTTP_200_OK)
async def cancel_pipeline(
    run_id: str,
    ctx: CurrentContext,
    session: TenantSession,
) -> dict:
    """Cancel a running or paused pipeline run.

    IDE-FIX: Emergency stop button — prevents users being locked for 2 hours
    if they started a pipeline by mistake or with wrong requirements.
    """
    run_id = _validate_run_id(run_id)
    orch = get_orchestrator()
    cancelled = await orch.cancel_run(run_id, organization_id=ctx.organization_id)
    if cancelled is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline run not found")
    if not cancelled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Pipeline cannot be cancelled — already in terminal state.",
        )
    return {"run_id": run_id, "status": "cancelled", "message": "Pipeline cancellation requested."}


# ── COST-FIX: Token usage and cost breakdown ──────────────────────────────────


@router.get("/{run_id}/cost")
async def get_pipeline_cost(
    run_id: str,
    ctx: CurrentContext,
    session: TenantSession,
) -> dict:
    """Return token usage and cost breakdown per agent for this pipeline run.

    COST-FIX: Reads token counts from pipeline.steps table and calculates
    estimated API cost in USD and INR.
    """
    from sqlalchemy import text

    from app.services.cost_service import calculate_pipeline_cost  # COST-FIX

    run_id = _validate_run_id(run_id)
    # Verify ownership
    orch = get_orchestrator()
    run = orch.get_run(run_id)
    if run is None:
        run = await orch.load_run_metadata(run_id, organization_id=ctx.organization_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline run not found")
    if str(run.organization_id) != str(ctx.organization_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline run not found")

    # Read steps from DB
    rows = await session.execute(
        text(
            "SELECT agent_name, stage, model_used, input_tokens, output_tokens,"
            " cache_read_tokens, cache_write_tokens"
            " FROM pipeline.steps WHERE run_id = :rid::uuid ORDER BY step_order"
        ),
        {"rid": run_id},
    )
    steps_data = [dict(r._mapping) for r in rows.fetchall()]
    cost = calculate_pipeline_cost(run_id, steps_data)

    return {
        "run_id": run_id,
        "total_cost_usd": round(cost.total_cost_usd, 6),
        "total_cost_inr": round(cost.total_cost_inr, 4),
        "estimated_cache_savings_usd": round(cost.estimated_savings_usd, 6),
        "total_input_tokens": cost.total_input_tokens,
        "total_output_tokens": cost.total_output_tokens,
        "total_cache_read_tokens": cost.total_cache_read_tokens,
        "agents": [
            {
                "agent": a.agent,
                "stage": a.stage,
                "model": a.model,
                "input_tokens": a.input_tokens,
                "output_tokens": a.output_tokens,
                "cache_read_tokens": a.cache_read_tokens,
                "cost_usd": round(a.cost_usd, 6),
                "cost_inr": round(a.cost_inr, 4),
            }
            for a in cost.agents
        ],
    }


# ── VERSION-FIX: Stage history / version history view ────────────────────────


@router.get("/{run_id}/stages")
async def get_pipeline_stages(
    run_id: str,
    ctx: CurrentContext,
    session: TenantSession,
) -> dict:
    """Return all completed stages with timing, tokens, and output summary.

    VERSION-FIX: Enables version history view — user can see which agent
    ran which stage, how long it took, and how many tokens it used.
    """
    from sqlalchemy import text

    run_id = _validate_run_id(run_id)
    orch = get_orchestrator()
    run = orch.get_run(run_id)
    if run is None:
        run = await orch.load_run_metadata(run_id, organization_id=ctx.organization_id)
    if run is None or str(run.organization_id) != str(ctx.organization_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline run not found")

    rows = await session.execute(
        text("""
            SELECT stage, agent_name, status, model_used,
                   input_tokens, output_tokens, cache_read_tokens,
                   started_at, completed_at, step_order, cycle,
                   EXTRACT(EPOCH FROM (completed_at - started_at)) as duration_secs
            FROM pipeline.steps WHERE run_id = :rid::uuid
            ORDER BY step_order ASC
        """),
        {"rid": run_id},
    )

    stages = []
    for r in rows.fetchall():
        stages.append(
            {
                "stage": r.stage,
                "agent": r.agent_name,
                "status": r.status,
                "model": r.model_used,
                "input_tokens": r.input_tokens or 0,
                "output_tokens": r.output_tokens or 0,
                "cache_read_tokens": r.cache_read_tokens or 0,
                "duration_secs": round(float(r.duration_secs or 0), 2),
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "cycle": r.cycle or 0,
            }
        )

    return {"run_id": run_id, "total_stages": len(stages), "stages": stages}


# ── PREVIEW-FIX: In-browser static preview ───────────────────────────────────

from fastapi.responses import HTMLResponse  # PREVIEW-FIX


@router.get("/{run_id}/preview", response_class=HTMLResponse)  # PREVIEW-FIX
async def preview_project(run_id: str, ctx: CurrentContext) -> HTMLResponse:
    """Serve a static in-browser preview of the generated frontend.

    PREVIEW-FIX: For projects with a generated index.html, serves it directly
    in the browser as an iframe preview. Only works for static/SSG frontends.
    Falls back to a file listing page for non-static projects.
    """
    run_id = _validate_run_id(run_id)
    orch = get_orchestrator()
    run = orch.get_run(run_id)
    if run is None:
        run = await orch.load_run_metadata(run_id, organization_id=ctx.organization_id)
    if run is None or str(run.organization_id) != str(ctx.organization_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline run not found")

    # Try to find index.html in generated files
    aanya_ctx = run.context.get("aanya", {})
    files: dict[str, str] = aanya_ctx.get("file_contents", {}) if isinstance(aanya_ctx, dict) else {}

    index_html = (
        files.get("index.html") or
        files.get("src/index.html") or
        files.get("public/index.html") or
        files.get("dist/index.html")
    )

    if index_html:
        return HTMLResponse(content=index_html)

    # No index.html — return a styled file listing page
    file_list = "".join(
        f"<li><code>{path}</code> <small>({len(content)} chars)</small></li>"
        for path, content in sorted(files.items())[:50]
    )
    total_files = len(files)
    shubham_ctx = run.context.get("shubham", {})
    backend_files = shubham_ctx.get("file_contents", {}) if isinstance(shubham_ctx, dict) else {}
    all_files = total_files + len(backend_files)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>NexSidi Project Preview — {run_id[:8]}</title>
  <style>
    body {{ font-family: -apple-system, sans-serif; max-width: 800px; margin: 2rem auto; padding: 1rem; background: #0f0f0f; color: #e0e0e0; }}
    h1 {{ color: #7c3aed; }} h2 {{ color: #a78bfa; }}
    code {{ background: #1e1e2e; padding: 2px 6px; border-radius: 4px; font-size: 0.85em; }}
    ul {{ list-style: none; padding: 0; }} li {{ padding: 4px 0; border-bottom: 1px solid #222; }}
    .badge {{ background: #7c3aed; color: white; padding: 2px 8px; border-radius: 12px; font-size: 0.75em; margin-left: 8px; }}
    .tip {{ background: #1e1e2e; border-left: 4px solid #7c3aed; padding: 1rem; margin: 1rem 0; border-radius: 0 8px 8px 0; }}
  </style>
</head>
<body>
  <h1>NexSidi Preview</h1>
  <p>Run <code>{run_id}</code> <span class="badge">{run.status.value}</span></p>
  <div class="tip">This project has <strong>{all_files} generated files</strong>. Use <code>GET /pipeline/{run_id}/download</code> to download the full ZIP or <code>GET /pipeline/{run_id}/files/tree</code> for the file tree.</div>
  <h2>Frontend Files ({total_files})</h2>
  <ul>{file_list or "<li>No frontend files generated yet</li>"}</ul>
  {'<p><em>…and more</em></p>' if total_files > 50 else ''}
</body>
</html>"""
    return HTMLResponse(content=html)
