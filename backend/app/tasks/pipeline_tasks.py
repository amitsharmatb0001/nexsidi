"""Celery tasks for pipeline execution with checkpoint-resume.

When ``use_celery=True``, pipeline runs are dispatched to Celery workers
instead of being executed in-process via ``asyncio.create_task()``.

KEY DESIGN: Crash-resilient checkpoint-resume
─────────────────────────────────────────────
The pipeline orchestrator already persists state to the database after
EVERY stage via ``_persist_run()`` + ``_persist_step()``.  This means
the DB always has the last-completed stage.

If a Celery worker crashes mid-pipeline:
1. ``acks_late=True`` → Celery re-delivers the task to another worker.
2. On re-delivery, ``self.request.retries > 0`` tells us this is a retry.
3. We call ``orch.resume_run()`` instead of ``orch.run_pipeline()`` —
   this rebuilds from the DB and continues from the last-completed stage.
4. No work is wasted: stages already persisted are skipped automatically.

If a ``SoftTimeLimitExceeded`` fires before the hard kill:
1. We catch it, mark the run as INTERRUPTED, persist to DB.
2. The user can later call ``/resume`` (or Celery auto-retries) to pick up.

Pause support:
- The pipeline orchestrator already supports PAUSED status for checkpoint
  and step-by-step modes.  When the pipeline returns with status=PAUSED,
  the Celery task completes normally with a "paused" result.
- A separate ``resume_pipeline_task`` picks up from the pause point.

Event loop management:
- REVIEW-FIX: Uses a persistent per-worker event loop instead of
  ``asyncio.run()`` which creates and destroys a new loop each time.
  This prevents destruction of DB/Redis connection pools that are bound
  to the event loop. Services initialized on the first task remain valid
  for all subsequent tasks in the same worker process.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import structlog

from app.celery_app import celery_app

try:
    from celery.exceptions import SoftTimeLimitExceeded
except ImportError:
    # Graceful fallback if Celery not fully installed (e.g., test env)
    class SoftTimeLimitExceeded(Exception):  # type: ignore[no-redef]
        pass

logger = structlog.get_logger(__name__)

# ── Worker-level event loop management ───────────────────────────
#
# REVIEW-FIX: asyncio.run() creates a new event loop per call and
# CLOSES it when done.  This destroys any DB/Redis connection pools
# that were initialized on that loop.  On the next task (same worker),
# _services_initialized is True but all connections are dead.
#
# Fix: Use a persistent event loop per worker process.  The loop lives
# for the entire worker lifetime.  Services initialized on it remain
# valid across all tasks.

_worker_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def _get_worker_loop() -> asyncio.AbstractEventLoop:
    """Get or create the persistent event loop for this worker process.

    Thread-safe: Celery prefork workers are single-threaded per process,
    but using a lock provides safety if gevent/eventlet pools are used.
    """
    global _worker_loop
    with _loop_lock:
        if _worker_loop is None or _worker_loop.is_closed():
            _worker_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(_worker_loop)
    return _worker_loop


# R27-FIX-24: Return type was `any` (builtin function), not `Any` (type hint).
def _run_async(coro: Any) -> Any:
    """Run an async coroutine on the persistent worker event loop.

    Unlike asyncio.run(), this does NOT create/destroy the event loop,
    preserving connection pools across task invocations.
    """
    loop = _get_worker_loop()
    return loop.run_until_complete(coro)


# ── Service lifecycle for Celery workers ─────────────────────────

# Track whether services are initialized for this worker process.
# This avoids re-initializing on every task (Celery reuses processes).
_services_initialized = False


async def _ensure_services() -> None:
    """Initialize async services (idempotent, once per worker process).

    Celery workers don't run FastAPI lifespan events, so we must
    manually initialize the services that the pipeline depends on.
    """
    global _services_initialized
    if _services_initialized:
        return

    from app.config import get_settings
    from app.database import setup_database

    settings = get_settings()
    setup_database(settings)
    # R19-FIX: Do NOT run migrations in Celery workers. Migrations must only
    # run in the web server process (FastAPI lifespan). When multiple Celery
    # workers start simultaneously (common in Kubernetes pod scaling), concurrent
    # `alembic upgrade head` causes DDL conflicts (CREATE TABLE already exists),
    # Alembic version table races, and worker startup crashes.

    # R38-FIX: Only mark initialized if all critical services succeeded.
    # Previously set True even when Valkey services failed, permanently
    # disabling token revocation for the worker's lifetime.
    all_ok = True
    for init_name, init_fn_path in [
        ("context_engine", "app.services.context_engine.init_context_engine"),
        ("prompt_engine", "app.services.prompt_engine.init_prompt_engine"),
        ("revocation_store", "app.services.token_revocation.init_revocation_store"),
        # Agentic services — gracefully skipped if not yet available
        ("agent_message_bus", "app.services.agent_message_bus.init_agent_message_bus"),
        ("pipeline_events", "app.services.pipeline_events.init_pipeline_events"),
    ]:
        try:
            module_path, fn_name = init_fn_path.rsplit(".", 1)
            import importlib
            mod = importlib.import_module(module_path)
            await getattr(mod, fn_name)()
        except Exception as exc:
            logger.warning(f"celery_{init_name}_init_failed", error=str(exc))
            all_ok = False

    _services_initialized = all_ok


async def _shutdown_services() -> None:
    """Gracefully shutdown async services.  Called on worker shutdown."""
    global _services_initialized
    if not _services_initialized:
        return

    from app.services.ai_router import shutdown_ai_router
    from app.services.context_engine import shutdown_context_engine
    from app.services.prompt_engine import shutdown_prompt_engine
    from app.services.token_revocation import shutdown_revocation_store
    from app.services.valkey_pool import shutdown_valkey_client
    from app.database import close_database

    await shutdown_ai_router()
    await shutdown_context_engine()
    await shutdown_prompt_engine()
    await shutdown_revocation_store()

    # Agentic services — gracefully skip if not initialized
    try:
        from app.services.agent_message_bus import shutdown_agent_message_bus
        await shutdown_agent_message_bus()
    except Exception:
        pass

    try:
        from app.services.pipeline_events import shutdown_pipeline_events
        await shutdown_pipeline_events()
    except Exception:
        pass

    # R26-FIX-35: Close shared Valkey pool AFTER services that use it,
    # but BEFORE DB close. Previously leaked 20 connections per worker shutdown.
    await shutdown_valkey_client()
    await close_database()
    _services_initialized = False


def _make_result(run) -> dict:
    """Convert a PipelineRun to a JSON-serializable result dict."""
    return {
        "run_id": run.run_id,
        "status": run.status.value,
        "current_stage": run.current_stage.value,
        "error": run.error,
    }


# ── Core async pipeline execution ────────────────────────────────


async def _run_pipeline_async(
    run_id: str,
    project_id: str,
    organization_id: str,
    user_id: str,
    execution_mode: str,
    requirements: str | None = None,
    is_retry: bool = False,
) -> dict:
    """Execute or resume a pipeline run.

    On first attempt (``is_retry=False``), creates a new pipeline run.
    On retry (``is_retry=True``), resumes from the last persisted stage.

    The pipeline orchestrator's ``run_pipeline()`` already saves state to
    DB after every stage.  If we crash mid-stage, ``resume_run()`` picks
    up from the last COMPLETED stage — the partially-executed stage is
    re-run (agents are idempotent by design).
    """
    await _ensure_services()

    from app.services.pipeline import ExecutionMode, get_orchestrator

    orch = get_orchestrator()

    if is_retry:
        # ── RETRY PATH: Resume from last checkpoint ──────────
        logger.info(
            "celery_pipeline_resuming_after_crash",
            run_id=run_id,
            organization_id=organization_id,
        )
        result = await orch.resume_run(run_id, organization_id=organization_id)

        if result is None:
            # Run not found in DB — it may never have been persisted.
            # Fall through to create a new run.
            logger.warning(
                "celery_resume_not_found_creating_new",
                run_id=run_id,
            )
        else:
            return _make_result(result)

    # ── FIRST ATTEMPT (or retry that couldn't find persisted state) ──
    #
    # R16-FIX: The router already created and persisted the run to DB
    # BEFORE dispatching to Celery. Look it up instead of creating a
    # duplicate. Creating a new run here would: (a) orphan the original
    # DB record in CREATED status, (b) give the Celery run a different
    # run_id than what the HTTP response returned to the user, making
    # /status and /resume calls 404.
    # R20-FIX: Always clear stale cache entry before loading from DB.
    # Celery worker's orchestrator singleton has its own _active_runs dict,
    # separate from the web process. If the same worker handles a resume for
    # a run_id it previously executed, it returns a STALE PipelineRun object
    # (frozen from the first load: status=RUNNING instead of PAUSED, old
    # context, old step_results). The resume logic then sees status=RUNNING
    # and returns immediately without resuming — permanently stuck pipeline.
    orch._active_runs.pop(run_id, None)
    run = None

    # Load fresh state from DB
    run_data = await orch._persistence.find_run_by_id(
        run_id, organization_id=organization_id,
    )
    if run_data is not None:
        run = orch._persistence.rebuild_run(run_data)
        orch._active_runs[run.run_id] = run

    if run is None:
        # Truly not found — fallback: create new run (should be rare)
        logger.warning("celery_run_not_found_creating_new", run_id=run_id)
        mode = ExecutionMode(execution_mode)
        run = orch.create_run(
            project_id=project_id,
            organization_id=organization_id,
            user_id=user_id,
            execution_mode=mode,
        )
        if requirements:
            run.context["__requirements__"] = requirements
    else:
        # Ensure requirements are set (may have been persisted in context)
        if requirements and "__requirements__" not in run.context:
            run.context["__requirements__"] = requirements

    logger.info(
        "celery_pipeline_starting",
        run_id=run.run_id,
        project_id=project_id,
        execution_mode=execution_mode,
    )

    # run_pipeline() persists state after every stage.  If the worker
    # crashes, the DB has the latest state for resume.
    result = await orch.run_pipeline(run)

    logger.info(
        "celery_pipeline_completed",
        run_id=result.run_id,
        status=result.status.value,
        current_stage=result.current_stage.value,
    )

    return _make_result(result)


async def _resume_pipeline_async(
    run_id: str,
    organization_id: str,
) -> dict:
    """Resume an interrupted/paused/failed pipeline."""
    await _ensure_services()

    from app.services.pipeline import get_orchestrator

    orch = get_orchestrator()

    logger.info("celery_pipeline_resuming", run_id=run_id)

    result = await orch.resume_run(run_id, organization_id=organization_id)

    if result is None:
        return {"run_id": run_id, "status": "not_found", "error": "Run not resumable"}

    logger.info(
        "celery_pipeline_resumed",
        run_id=result.run_id,
        status=result.status.value,
    )

    return _make_result(result)


async def _handle_soft_timeout(run_id: str, organization_id: str) -> dict:
    """Handle SoftTimeLimitExceeded: mark run as INTERRUPTED for later resume.

    The pipeline orchestrator's ``_persist_run()`` saves state after each
    stage, so the DB already has the latest checkpoint.  We just need to
    update the status to INTERRUPTED so ``/resume`` knows to pick it up.

    REVIEW-FIX: Wrapped in try/except so a failure here (e.g., DB down,
    services not initialized) doesn't prevent the Celery retry.

    R16-FIX: Uses a fresh DB session via raw SQL instead of going through
    the ORM/orchestrator. The SoftTimeLimitExceeded may have fired during
    a DB write, leaving the existing session in a dirty/error state. Using
    a fresh connection avoids inheriting that tainted state.
    """
    try:
        await _ensure_services()

        from app.services.pipeline import PipelineRunStatus, get_orchestrator

        orch = get_orchestrator()

        # Update in-memory hot cache if available
        run = orch.get_run(run_id)
        if run is not None and run.status not in (
            PipelineRunStatus.COMPLETED,
            PipelineRunStatus.FAILED,
        ):
            run.status = PipelineRunStatus.INTERRUPTED
            run.error = "Celery task soft time limit exceeded — resume to continue"

        # R16-FIX: Use a FRESH DB session for the persist operation.
        # The SoftTimeLimitExceeded signal may have interrupted the previous
        # DB operation mid-transaction, leaving the connection pool tainted.
        # A fresh session guarantees a clean connection.
        #
        # R18-FIX: Prefer admin session factory to bypass RLS. The app user
        # (nexsidi_app) has FORCE ROW LEVEL SECURITY, and raw SQL UPDATE
        # without SET LOCAL app.current_tenant matches zero rows. The admin
        # session (postgres/nexsidi_admin user) bypasses RLS for this
        # system-level operation, matching find_interrupted_runs pattern.
        from app.database import get_admin_session_factory, get_session_factory
        from sqlalchemy import text

        admin_factory = get_admin_session_factory()
        factory = admin_factory if admin_factory is not None else get_session_factory()
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "UPDATE pipeline.runs SET status = 'interrupted', "
                        "error_summary = 'Soft time limit exceeded — resume to continue' "
                        "WHERE id = :run_id::uuid AND status NOT IN ('completed', 'failed')"
                    ),
                    {"run_id": run_id},
                )
            # R17-FIX: `run` may be None in Celery workers (separate process,
            # hot cache doesn't have this run). Guard against AttributeError.
            if run is not None:
                logger.warning(
                    "celery_soft_timeout_interrupted",
                    run_id=run_id,
                    current_stage=run.current_stage.value,
                )
                return _make_result(run)
            else:
                logger.warning(
                    "celery_soft_timeout_interrupted",
                    run_id=run_id,
                    current_stage="unknown",
                )
                return {
                    "run_id": run_id,
                    "status": "interrupted",
                    "current_stage": "unknown",
                    "error": "Soft time limit exceeded — resume to continue",
                }
    except Exception as exc:
        # REVIEW-FIX: If persisting INTERRUPTED status fails (DB down, etc.),
        # log and continue. The Celery retry will attempt resume anyway,
        # and the DB may still have the last good checkpoint from _persist_run()
        # during normal execution.
        logger.error(
            "celery_soft_timeout_handler_failed",
            run_id=run_id,
            error=str(exc)[:300],
        )

    return {"run_id": run_id, "status": "interrupted", "error": "Soft time limit exceeded"}


# ── Worker lifecycle signals ──────────────────────────────────────
#
# REVIEW-FIX: _shutdown_services() was defined but never called.
# Without this, the persistent event loop's DB/Redis connections are
# abandoned on worker shutdown (not cleanly closed), potentially
# causing connection pool exhaustion in the DB server.

try:
    from celery.signals import worker_process_shutdown

    @worker_process_shutdown.connect
    def _on_worker_shutdown(**kwargs: Any) -> None:
        """Gracefully shutdown async services when the Celery worker exits."""
        global _worker_loop
        if _worker_loop is not None and not _worker_loop.is_closed():
            try:
                _worker_loop.run_until_complete(_shutdown_services())
            except Exception as exc:
                logger.warning("celery_shutdown_services_failed", error=str(exc)[:200])
            finally:
                _worker_loop.close()
                _worker_loop = None
        logger.info("celery_worker_shutdown_complete")
except ImportError:
    # Celery not installed — skip signal registration
    pass


# ── Celery task definitions ──────────────────────────────────────


@celery_app.task(
    bind=True,
    name="nexsidi.run_pipeline",
    max_retries=2,  # Up to 2 retries → 3 total attempts (crash-resume)
    acks_late=True,
    track_started=True,
    time_limit=7200,  # 2h hard kill
    soft_time_limit=7000,  # Soft limit → SoftTimeLimitExceeded → save state
    reject_on_worker_lost=True,  # Re-deliver if worker process dies (OOM kill)
)
def run_pipeline_task(
    self,
    run_id: str,
    project_id: str,
    organization_id: str,
    user_id: str,
    execution_mode: str = "checkpoint",
    requirements: str | None = None,
) -> dict:
    """Execute a pipeline run with crash-resilient checkpoint-resume.

    Lifecycle:
    1. First attempt: Creates a new pipeline run and executes it.
       The orchestrator persists state to DB after every stage.
    2. On crash + re-delivery: ``self.request.retries > 0`` triggers
       resume from the last persisted stage (no work re-done).
    3. On SoftTimeLimitExceeded: Marks run as INTERRUPTED, persists,
       then Celery auto-retries which triggers a resume.
    4. On unrecoverable failure: Marks as FAILED in DB.

    REVIEW-FIX: Uses _run_async() (persistent event loop) instead of
    asyncio.run() which destroys DB/Redis connection pools after each task.

    Returns:
        Dict with run_id, status, current_stage, and error (if any).
    """
    is_retry = self.request.retries > 0

    logger.info(
        "celery_task_started",
        task_id=self.request.id,
        run_id=run_id,
        project_id=project_id,
        attempt=self.request.retries + 1,
        is_retry=is_retry,
    )

    try:
        return _run_async(
            _run_pipeline_async(
                run_id=run_id,
                project_id=project_id,
                organization_id=organization_id,
                user_id=user_id,
                execution_mode=execution_mode,
                requirements=requirements,
                is_retry=is_retry,
            )
        )

    except SoftTimeLimitExceeded:
        # ── Soft timeout: save state as INTERRUPTED, then retry ──
        logger.warning(
            "celery_soft_timeout",
            task_id=self.request.id,
            run_id=run_id,
            attempt=self.request.retries + 1,
        )
        # Persist INTERRUPTED status so resume can pick up.
        # REVIEW-FIX: Uses _run_async (same loop) so DB connections still work.
        # Also wrapped in try/except inside _handle_soft_timeout.
        try:
            _run_async(_handle_soft_timeout(run_id, organization_id))
        except Exception as timeout_exc:
            logger.error(
                "celery_soft_timeout_persist_failed",
                run_id=run_id,
                error=str(timeout_exc)[:200],
            )
        # R26-FIX-12: Wrap self.retry() in try/except. If max retries exhausted,
        # self.retry() raises MaxRetriesExceededError. Since we're inside
        # `except SoftTimeLimitExceeded:`, the sibling `except MaxRetriesExceededError:`
        # does NOT catch it (Python doesn't match sibling except blocks from within
        # another except handler).
        try:
            raise self.retry(countdown=10, max_retries=self.max_retries)
        except self.MaxRetriesExceededError:
            return {
                "run_id": run_id,
                "status": "failed",
                "error": "Soft time limit exceeded after all retries",
                "current_stage": "unknown",
            }

    except self.MaxRetriesExceededError:
        # All retries exhausted — mark as failed
        logger.error(
            "celery_task_max_retries",
            task_id=self.request.id,
            run_id=run_id,
        )
        return {
            "run_id": run_id,
            "status": "failed",
            "error": "Max retries exceeded",
            "current_stage": "unknown",
        }

    except Exception as exc:
        # R22-FIX: Sanitize exception to prevent API key leakage in Celery
        # result backend. Return values are stored in Redis/DB and may be
        # accessible to clients querying task status.
        from app.services.ai_router import _sanitize_error
        safe_err = _sanitize_error(exc)
        logger.error(
            "celery_task_failed",
            task_id=self.request.id,
            run_id=run_id,
            error=safe_err[:500],
            attempt=self.request.retries + 1,
        )
        # R26-FIX-36: Pass serializable exception wrapper instead of raw exc.
        # Raw exceptions (e.g., httpx.HTTPStatusError with Response objects)
        # fail JSON serialization, causing a secondary error that masks the
        # original and prevents retry.
        try:
            raise self.retry(
                exc=Exception(safe_err[:500]),
                countdown=30 * (2 ** self.request.retries),
                max_retries=self.max_retries,
            )
        except self.MaxRetriesExceededError:
            return {
                "run_id": run_id,
                "status": "failed",
                "error": f"Failed after {self.request.retries + 1} attempts: {safe_err[:200]}",
                "current_stage": "unknown",
            }


@celery_app.task(
    bind=True,
    name="nexsidi.resume_pipeline",
    max_retries=2,
    acks_late=True,
    track_started=True,
    time_limit=7200,
    soft_time_limit=7000,
    reject_on_worker_lost=True,
)
def resume_pipeline_task(
    self,
    run_id: str,
    organization_id: str,
) -> dict:
    """Resume an interrupted/failed pipeline in a Celery worker.

    Uses the same crash-resilient checkpoint-resume as run_pipeline_task:
    - On crash + re-delivery, resumes from last persisted stage.
    - On SoftTimeLimitExceeded, marks as INTERRUPTED for later resume.
    """
    is_retry = self.request.retries > 0

    logger.info(
        "celery_resume_task_started",
        task_id=self.request.id,
        run_id=run_id,
        attempt=self.request.retries + 1,
        is_retry=is_retry,
    )

    try:
        return _run_async(
            _resume_pipeline_async(
                run_id=run_id,
                organization_id=organization_id,
            )
        )

    except SoftTimeLimitExceeded:
        logger.warning("celery_resume_soft_timeout", run_id=run_id)
        try:
            _run_async(_handle_soft_timeout(run_id, organization_id))
        except Exception as timeout_exc:
            logger.error(
                "celery_resume_soft_timeout_persist_failed",
                run_id=run_id,
                error=str(timeout_exc)[:200],
            )
        # R26-FIX-12: Same fix as run_pipeline_task — handle MaxRetriesExceededError
        # inline since sibling except blocks don't catch from within another handler.
        try:
            raise self.retry(countdown=10, max_retries=self.max_retries)
        except self.MaxRetriesExceededError:
            return {
                "run_id": run_id,
                "status": "failed",
                "error": "Resume soft time limit exceeded after all retries",
                "current_stage": "unknown",
            }

    except self.MaxRetriesExceededError:
        return {
            "run_id": run_id,
            "status": "failed",
            "error": "Resume max retries exceeded",
            "current_stage": "unknown",
        }

    except Exception as exc:
        # R22-FIX: Sanitize exception (same as run_pipeline_task above).
        from app.services.ai_router import _sanitize_error
        safe_err = _sanitize_error(exc)
        logger.error(
            "celery_resume_task_failed",
            task_id=self.request.id,
            run_id=run_id,
            error=safe_err[:500],
        )
        try:
            # R27-FIX-8: Wrap exc in serializable Exception (same as R26-FIX-36
            # applied to run_pipeline_task). Raw exc may contain non-JSON-
            # serializable objects (e.g., httpx.Response), causing Celery
            # to raise a secondary TypeError on retry persistence.
            raise self.retry(
                exc=Exception(safe_err[:500]),
                countdown=30 * (2 ** self.request.retries),
                max_retries=self.max_retries,
            )
        except self.MaxRetriesExceededError:
            return {
                "run_id": run_id,
                "status": "failed",
                "error": f"Resume failed after retries: {safe_err[:200]}",
                "current_stage": "unknown",
            }
