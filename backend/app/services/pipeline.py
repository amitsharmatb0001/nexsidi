"""Pipeline orchestrator: manages the 18-step agent execution chain.

Coordinates agent execution order, checkpoint pauses, parallel quality gates,
the fixer retry loop, and the architecture challenge-retry loop.

Now with crash recovery: state is persisted to the database after every stage
so interrupted pipelines can resume.

Execution modes:
- STEP_BY_STEP: Pauses after every agent (for debugging/demo)
- CHECKPOINT: Pauses at CHECKPOINT 1 and CHECKPOINT 2 (default)
- DIRECT: No pauses (for trusted automated runs)

Pipeline stages:
    REQUIREMENTS -> ANALYSIS -> ARCHITECTURE -> ARCHITECTURE_REVIEW ->
    DATABASE_DESIGN -> UI_DESIGN -> CHECKPOINT_DESIGN ->
    BACKEND_BUILD -> FRONTEND_BUILD ->
    QUALITY_REVIEW (parallel) -> TESTING -> SECURITY_AUDIT ->
    COMPLIANCE_CHECK -> TILOTMA_REVIEW (GO/NO-GO) -> FIXING (loop) -> CHECKPOINT_TESTING ->
    DEPLOYMENT -> DELIVERY -> COMPLETED
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog

from app.agents.base import AgentInterruptRequest, AgentResult, AgentStatus, get_agent
from app.services.ai_router import (  # COST-CAP-FIX + F6-FIX
    PipelineCostLimitError,
    ProjectCostTracker,
    register_cost_tracker,
    unregister_cost_tracker,
)

logger = structlog.get_logger(__name__)

# COST-AGG-FIX: Module-level registry mapping run_id → shared ProjectCostTracker.
# Agents that want to contribute to the pipeline-level cost cap call
# get_run_cost_tracker(pipeline_run_id) and record() on the result.
# Keyed by run_id (str UUID); entries are removed when the run reaches a
# terminal state so the dict doesn't grow unboundedly.
_RUN_COST_TRACKERS: dict[str, ProjectCostTracker] = {}


def get_run_cost_tracker(run_id: str) -> ProjectCostTracker | None:
    """Return the shared cost tracker for a running pipeline, or None.

    Agents call this to contribute their AI costs to the pipeline-level cap.
    Returns None for runs that haven't started yet (safe to ignore — agents
    fall back to their own local tracking).
    """
    return _RUN_COST_TRACKERS.get(run_id)


# -- Pipeline Stages ----------------------------------------------------------


class PipelineStage(str, Enum):
    """Ordered stages of the NexSidi pipeline."""

    REQUIREMENTS = "requirements"
    ANALYSIS = "analysis"
    ARCHITECTURE = "architecture"
    ARCHITECTURE_REVIEW = "architecture_review"
    DATABASE_DESIGN = "database_design"
    UI_DESIGN = "ui_design"
    CHECKPOINT_DESIGN = "checkpoint_design"
    BACKEND_BUILD = "backend_build"
    FRONTEND_BUILD = "frontend_build"
    QUALITY_REVIEW = "quality_review"
    TESTING = "testing"
    SECURITY_AUDIT = "security_audit"
    COMPLIANCE_CHECK = "compliance_check"
    TILOTMA_REVIEW = "tilotma_review"  # Tilotma GO/NO-GO before fixing
    FIXING = "fixing"
    CHECKPOINT_TESTING = "checkpoint_testing"
    DEPLOYMENT = "deployment"
    DELIVERY = "delivery"
    COMPLETED = "completed"


# Stage -> agent name mapping
STAGE_AGENTS: dict[PipelineStage, str | list[str]] = {
    PipelineStage.REQUIREMENTS: "tilotma",
    PipelineStage.ANALYSIS: "saanvi",
    PipelineStage.ARCHITECTURE: "vikram",
    PipelineStage.ARCHITECTURE_REVIEW: "challenger",
    PipelineStage.DATABASE_DESIGN: "dhruv",
    PipelineStage.UI_DESIGN: "vanya",
    PipelineStage.CHECKPOINT_DESIGN: "__checkpoint_1__",
    PipelineStage.BACKEND_BUILD: "shubham",
    PipelineStage.FRONTEND_BUILD: "aanya",
    PipelineStage.QUALITY_REVIEW: ["karan", "navya", "deepika"],  # Parallel
    PipelineStage.TESTING: "aarav",
    PipelineStage.SECURITY_AUDIT: "karan",
    PipelineStage.COMPLIANCE_CHECK: "karan",
    PipelineStage.TILOTMA_REVIEW: "tilotma",  # Tilotma reviews all reports, GO/NO-GO
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

# I2-FIX: Agent context dependency map ────────────────────────────
# Each agent only receives the prior agent outputs it actually reads,
# plus all metadata keys (__*__).  This eliminates the "context bloat
# blackhole" where agents received MB-sized dicts they never touched.
# Unknown agents fall back to full context (safe default).
_AGENT_CONTEXT_DEPS: dict[str, list[str]] = {
    "tilotma":    [],                                    # reads __requirements__
    "saanvi":     ["tilotma"],
    "vikram":     ["tilotma", "saanvi"],
    "challenger": ["vikram"],
    "dhruv":      ["vikram"],
    "vanya":      ["vikram", "saanvi"],
    "shubham":    ["vikram", "dhruv"],
    "aanya":      ["vikram", "shubham", "vanya"],
    "karan":      ["vikram", "shubham", "aanya"],
    "navya":      ["vikram", "shubham", "aanya"],
    "deepika":    ["vikram", "shubham", "aanya"],
    "aarav":      ["vikram", "shubham", "aanya"],
    "fixer":      ["vikram", "shubham", "aanya", "aarav", "karan", "navya", "deepika"],
    "pranav":     ["vikram", "shubham", "aanya"],
}


def _build_filtered_context(ctx: dict[str, Any], agent_name: str) -> dict[str, Any]:
    """Return a context dict containing only the keys *agent_name* needs.

    Metadata keys (``__*__``) are always included.  If *agent_name* is not
    in ``_AGENT_CONTEXT_DEPS``, the full context is returned (safe default).
    """
    deps = _AGENT_CONTEXT_DEPS.get(agent_name)
    if deps is None:
        return ctx  # Unknown agent — give everything
    return {
        k: v
        for k, v in ctx.items()
        if k.startswith("__") or k in deps
    }

# Redo rewind targets: which stage to jump back to when user says "redo"
CHECKPOINT_REDO_TARGETS: dict[PipelineStage, PipelineStage] = {
    PipelineStage.CHECKPOINT_DESIGN: PipelineStage.ARCHITECTURE,
    PipelineStage.CHECKPOINT_TESTING: PipelineStage.QUALITY_REVIEW,
}


# -- Execution Mode -----------------------------------------------------------


class ExecutionMode(str, Enum):
    STEP_BY_STEP = "step_by_step"  # Pause after every agent
    CHECKPOINT = "checkpoint"  # Pause at checkpoints only (default)
    DIRECT = "direct"  # No pauses


# -- Pipeline Run State -------------------------------------------------------


class PipelineRunStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"  # Waiting at checkpoint
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    # R12-FIX: mark_interrupted() writes "interrupted" to DB. Without this
    # member, rebuild_run() falls back to RUNNING and resume_run() hits the
    # RUNNING guard, making interrupted runs permanently un-resumable.
    INTERRUPTED = "interrupted"


# Max cycles for the fix-retest loop
MAX_FIX_RETEST_CYCLES = 3

# Max retries for the architecture challenge-retry loop
MAX_CHALLENGE_RETRIES = 2

# Stages that are re-run in the fix-retest loop
FIX_RETEST_STAGES: list[PipelineStage] = [
    PipelineStage.QUALITY_REVIEW,
    PipelineStage.TESTING,
    PipelineStage.SECURITY_AUDIT,
    PipelineStage.COMPLIANCE_CHECK,
    PipelineStage.TILOTMA_REVIEW,
    PipelineStage.FIXING,
]


@dataclass(slots=True)
class StepResult:
    """Result of a single pipeline step execution."""

    stage: PipelineStage
    agent_name: str
    result: AgentResult | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    skipped: bool = False
    cycle: int = 0  # Which fix-retest cycle (0 = first pass)


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

    # Fix-retest loop tracking
    fix_retest_cycle: int = 0

    # Architecture challenge-retry tracking
    challenge_retry_count: int = 0

    # Database record ID (set after first persist)
    db_run_id: str | None = None


# -- Pipeline Persistence (Crash Recovery) ------------------------------------


# -- Project Rate Limiter (Token Bucket) --------------------------------------


class ProjectRateLimiter:
    """Per-project rate limiter using token bucket algorithm.

    Limits the number of AI calls per project per minute.
    If rate limit hit, sleeps and retries rather than failing.

    BUCKET-FIX: Evicts stale project buckets periodically to prevent
    unbounded memory growth when many distinct project IDs are seen.
    """

    _MAX_BUCKETS: int = 5_000  # hard cap on tracked projects
    _EVICTION_INTERVAL: int = 100  # check every N acquires

    def __init__(self, calls_per_minute: int = 30) -> None:
        self._calls_per_minute = calls_per_minute
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._acquire_count: int = 0

    @property
    def calls_per_minute(self) -> int:
        return self._calls_per_minute

    async def acquire(self, project_id: str) -> None:
        """Wait until a token is available for this project.

        Sleeps if the rate limit is reached instead of failing.

        DEFERRED-FIX-3: Attempts Valkey-backed rate limiting first for
        cross-worker consistency. Falls back to in-memory if Valkey
        unavailable (dev mode / Valkey outage).
        """
        # Try distributed rate limiting via Valkey
        if await self._try_valkey_acquire(project_id):
            return

        # Fallback: in-memory rate limiting (single-process only)
        now = time.monotonic()
        window = 60.0  # 1 minute window

        # BUCKET-FIX: Periodic eviction of stale project buckets
        self._acquire_count += 1
        if self._acquire_count >= self._EVICTION_INTERVAL:
            self._evict_stale_buckets(now, window)
            self._acquire_count = 0

        # Clean old entries outside window
        self._buckets[project_id] = [
            t for t in self._buckets[project_id] if now - t < window
        ]

        if len(self._buckets[project_id]) >= self._calls_per_minute:
            # Wait for oldest entry to expire
            oldest = self._buckets[project_id][0]
            sleep_time = window - (now - oldest) + 0.1
            if sleep_time > 0:
                logger.info(
                    "rate_limit_wait",
                    project_id=project_id,
                    sleep_seconds=round(sleep_time, 1),
                )
                await asyncio.sleep(sleep_time)
            # Clean again after sleeping
            now = time.monotonic()
            self._buckets[project_id] = [
                t for t in self._buckets[project_id] if now - t < window
            ]

        self._buckets[project_id].append(time.monotonic())

    async def _try_valkey_acquire(self, project_id: str) -> bool:
        """Attempt Valkey-backed sliding window rate limit (cross-worker safe).

        Returns True if acquired (caller should proceed), False if Valkey
        unavailable (caller should fall back to in-memory).
        Raises nothing — failures fall back to in-memory.
        """
        try:
            from app.services.token_revocation import get_revocation_store
            store = get_revocation_store()
            redis = store._redis  # noqa: SLF001 — reuse existing pool
            if redis is None:
                return False
            key = f"rl:{project_id}"
            now_ms = int(time.time() * 1000)
            window_ms = 60_000
            # Lua sliding window: ZREMRANGEBYSCORE + ZCARD + ZADD
            lua = """
            redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, ARGV[1] - ARGV[2])
            local count = redis.call('ZCARD', KEYS[1])
            if count < tonumber(ARGV[3]) then
                redis.call('ZADD', KEYS[1], ARGV[1], ARGV[1] .. ':' .. math.random(1000000))
                redis.call('EXPIRE', KEYS[1], 120)
                return 0
            else
                return 1
            end
            """
            result = await redis.eval(lua, 1, key, now_ms, window_ms, self._calls_per_minute)
            if result == 1:
                # R25-FIX-1: Rate limited — sleep and retry. If STILL denied
                # after retry, return False to fall back to in-memory limiter
                # which correctly blocks. Previously returned True regardless,
                # allowing rate-limited requests to proceed.
                await asyncio.sleep(2.0)
                result = await redis.eval(lua, 1, key, int(time.time() * 1000), window_ms, self._calls_per_minute)
                if result == 1:
                    # Still rate-limited after retry — let in-memory handle blocking
                    return False
            return True  # Valkey handled it (acquired successfully)
        except Exception:
            return False  # Fall back to in-memory

    def _evict_stale_buckets(self, now: float, window: float) -> None:
        """Remove project buckets with no active entries."""
        stale = [
            pid for pid, times in self._buckets.items()
            if not times or (now - times[-1]) >= window
        ]
        for pid in stale:
            del self._buckets[pid]

        # Hard cap: if still too many, drop oldest
        if len(self._buckets) > self._MAX_BUCKETS:
            sorted_pids = sorted(
                self._buckets,
                key=lambda p: self._buckets[p][-1] if self._buckets[p] else 0,
            )
            for pid in sorted_pids[: len(self._buckets) - self._MAX_BUCKETS]:
                del self._buckets[pid]

    def get_usage(self, project_id: str) -> int:
        """Get current usage count for a project in the current window."""
        now = time.monotonic()
        window = 60.0
        # R8-FIX: Use .get() to avoid defaultdict creating empty entries.
        # Previously, reading non-existent project_ids populated _buckets,
        # bypassing eviction logic and allowing unbounded dict growth.
        entries = self._buckets.get(project_id)
        if entries is None:
            return 0
        filtered = [t for t in entries if now - t < window]
        if filtered:
            self._buckets[project_id] = filtered
        else:
            del self._buckets[project_id]
        return len(filtered)

    def reset(self, project_id: str) -> None:
        """Reset rate limit for a project."""
        self._buckets.pop(project_id, None)


# -- Pipeline Persistence (Crash Recovery) ------------------------------------


class PipelinePersistence:
    """Persists pipeline state to the database for crash recovery.

    After each stage completes, saves:
    - Run status, current_stage, context_snapshot to pipeline.runs
    - Step result to pipeline.steps

    On startup, finds interrupted runs (status=RUNNING) and rebuilds
    PipelineRun objects from the database so they can be resumed.

    RLS-FIX: All DB operations set tenant context so Row-Level Security
    policies are enforced. Pipeline persistence runs in background tasks
    (not HTTP requests), so we must set tenant context manually.
    """

    @staticmethod
    async def _set_rls_context(
        session: Any, organization_id: str, user_id: str
    ) -> None:
        """Set RLS context for pipeline persistence sessions.

        Pipeline persistence runs outside HTTP request scope, so we must
        set tenant context manually for every session.
        """
        from app.middleware.tenant import TenantContext, set_tenant_context

        # Use a nil UUID if user_id is missing (e.g., system-initiated runs)
        safe_user_id = user_id if user_id else "00000000-0000-0000-0000-000000000000"
        ctx = TenantContext(
            organization_id=organization_id,
            user_id=safe_user_id,
            role="org_admin",  # Pipeline operations require admin-level access
        )
        await set_tenant_context(session, ctx)

    async def save_run(self, run: PipelineRun) -> str:
        """Create or update the pipeline run record in the database.

        Returns the database record ID.
        """
        from app.database import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            async with session.begin():
                from sqlalchemy import select, text
                from app.models.pipeline import PipelineRun as PipelineRunModel

                # RLS-FIX: Set tenant context for RLS enforcement
                await self._set_rls_context(session, run.organization_id, run.user_id)

                if run.db_run_id:
                    # Update existing record
                    result = await session.execute(
                        select(PipelineRunModel).where(
                            PipelineRunModel.id == uuid.UUID(run.db_run_id)
                        )
                    )
                    db_run = result.scalar_one_or_none()
                    if db_run:
                        db_run.status = run.status.value
                        db_run.current_step = run.current_stage.value
                        db_run.execution_mode = run.execution_mode.value
                        db_run.context_snapshot = run.context
                        db_run.error_summary = run.error
                        if run.status in (PipelineRunStatus.COMPLETED, PipelineRunStatus.FAILED):
                            db_run.completed_at = datetime.now(timezone.utc)
                        await session.flush()
                        return run.db_run_id
                    else:
                        # R10-FIX: DB record was deleted (admin purge, cascade,
                        # RLS mismatch). Clear stale ref so we re-create below.
                        logger.warning(
                            "persist_run_record_missing",
                            run_id=run.run_id,
                            stale_db_id=run.db_run_id,
                        )
                        run.db_run_id = None

                # Create new record (first save or re-create after deletion)
                if not run.db_run_id:
                    db_run = PipelineRunModel(
                        organization_id=uuid.UUID(run.organization_id),
                        project_id=uuid.UUID(run.project_id),
                        user_id=uuid.UUID(run.user_id) if run.user_id else None,
                        status=run.status.value,
                        current_step=run.current_stage.value,
                        execution_mode=run.execution_mode.value,
                        context_snapshot=run.context,
                    )
                    session.add(db_run)
                    await session.flush()
                    run.db_run_id = str(db_run.id)
                    # R11-FIX: Sync run_id with db_run_id so resume works.
                    # R15-FIX: Also update _active_runs cache key so get_run()
                    # can find the run by the new (DB-stable) ID. Previously
                    # the cache held the old UUID4 key but the object's run_id
                    # was changed to the DB UUID, making the run invisible to
                    # get_run() and leaving an orphan in the cache.
                    old_run_id = run.run_id
                    run.run_id = str(db_run.id)
                    return str(db_run.id)

        return run.db_run_id or ""

    async def save_step(self, run: PipelineRun, step: StepResult, step_order: int) -> None:
        """Save a completed step to the database."""
        from app.database import get_session_factory
        from app.models.pipeline import PipelineStep as PipelineStepModel

        if not run.db_run_id:
            return

        factory = get_session_factory()
        async with factory() as session:
            async with session.begin():
                # RLS-FIX: Set tenant context for RLS enforcement
                await self._set_rls_context(session, run.organization_id, run.user_id)
                output_data = None
                if step.result and step.result.output:
                    output_data = step.result.output

                db_step = PipelineStepModel(
                    organization_id=uuid.UUID(run.organization_id),
                    run_id=uuid.UUID(run.db_run_id),
                    agent_name=step.agent_name,
                    stage=step.stage.value,
                    status=(
                        step.result.status.value if step.result
                        else ("skipped" if step.skipped else "pending")
                    ),
                    step_order=step_order,
                    output_data=output_data,
                    output_summary=(
                        {"keys": list(step.result.output.keys())} if step.result and step.result.output
                        else None
                    ),
                    model_used=step.result.model_used if step.result else None,
                    input_tokens=step.result.input_tokens if step.result else None,
                    output_tokens=step.result.output_tokens if step.result else None,
                    started_at=step.started_at,
                    completed_at=step.completed_at,
                    error_message=step.result.error if step.result else None,
                )
                session.add(db_step)

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        """Convert a PipelineRunModel row to a dict for rebuild_run()."""
        return {
            "db_run_id": str(row.id),
            "project_id": str(row.project_id),
            "organization_id": str(row.organization_id),
            "user_id": str(row.user_id) if row.user_id else "",
            "status": row.status,
            "current_step": row.current_step,
            "execution_mode": row.execution_mode or "checkpoint",
            "context_snapshot": row.context_snapshot or {},
            "error_summary": row.error_summary,
        }

    async def find_run_by_id(
        self, run_id: str, organization_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Find a single pipeline run by ID (any resumable status).

        REFIX: Direct ID lookup (O(1)) that also includes failed/interrupted
        runs so they can be auth-checked and potentially resumed.

        RLS-READ-FIX: Optionally filters by organization_id to prevent
        cross-tenant data reads. Without this, any run_id UUID can be looked up
        regardless of tenant. The caller (resume_pipeline, load_run_metadata)
        should always pass organization_id for defense-in-depth.
        """
        from app.database import get_session_factory

        try:
            factory = get_session_factory()
        except RuntimeError:
            return None

        async with factory() as session:
            from sqlalchemy import select
            from app.models.pipeline import PipelineRun as PipelineRunModel

            # R11-FIX: Validate UUID before query to return clean None
            # instead of unhandled ValueError → 500.
            try:
                run_uuid = uuid.UUID(run_id)
            except (ValueError, AttributeError):
                return None
            try:
                org_uuid = uuid.UUID(organization_id) if organization_id else None
            except (ValueError, AttributeError):
                return None

            # R17-FIX: Must set RLS context so the query doesn't return zero
            # rows under FORCE ROW LEVEL SECURITY. Without this, the RLS
            # policy evaluates organization_id = '' (empty current_tenant)
            # which never matches, making resume/status/websocket DB fallback
            # silently return None.
            async with session.begin():
                # R18-FIX: Always set RLS context, even when org_uuid is None.
                # Using nil UUID when org is unknown ensures the RLS policy
                # evaluates cleanly (no match) instead of undefined behavior.
                effective_org = str(org_uuid) if org_uuid else "00000000-0000-0000-0000-000000000000"
                await self._set_rls_context(
                    session, effective_org,
                    "00000000-0000-0000-0000-000000000000",
                )

                query = select(PipelineRunModel).where(
                    PipelineRunModel.id == run_uuid
                )
                if org_uuid:
                    query = query.where(
                        PipelineRunModel.organization_id == org_uuid
                    )
                result = await session.execute(query)
                row = result.scalar_one_or_none()
                if row is None:
                    return None
                return self._row_to_dict(row)

    async def find_interrupted_runs(self) -> list[dict[str, Any]]:
        """Find pipeline runs that were interrupted (status=RUNNING or PAUSED).

        Called on server startup to identify runs that need recovery.
        Returns a list of run data dicts.

        REVIEW-FIX: This is an intentional cross-tenant admin operation.
        Crash recovery MUST see all interrupted runs regardless of org,
        because the server needs to mark/restore them all. The API layer
        enforces tenant isolation when users access these runs.

        R17-FIX: Uses admin session factory (if available) to bypass RLS.
        The app session factory connects as nexsidi_app which has
        FORCE ROW LEVEL SECURITY — without setting app.current_tenant,
        the query returns zero rows, silently disabling crash recovery.

        Safety: Limited to 100 rows to prevent OOM on corrupt data.
        """
        from app.database import get_admin_session_factory, get_session_factory

        # R17-FIX: Prefer admin session (bypasses RLS for cross-tenant access)
        try:
            admin_factory = get_admin_session_factory()
            factory = admin_factory if admin_factory is not None else get_session_factory()
        except RuntimeError:
            # Database not initialized yet
            return []

        async with factory() as session:
            from sqlalchemy import select
            from app.models.pipeline import PipelineRun as PipelineRunModel

            # REVIEW-FIX: Limit to prevent unbounded memory on corrupt DB
            result = await session.execute(
                select(PipelineRunModel).where(
                    PipelineRunModel.status.in_(["running", "paused"])
                ).limit(100)
            )
            rows = result.scalars().all()
            return [self._row_to_dict(row) for row in rows]

    async def mark_interrupted(self, db_run_id: str) -> None:
        """Mark an interrupted run as failed with crash recovery note.

        R17-FIX: Uses admin session factory to bypass RLS, matching
        find_interrupted_runs() which also needs cross-tenant access.
        """
        from app.database import get_admin_session_factory, get_session_factory
        from app.models.pipeline import PipelineRun as PipelineRunModel

        # R17-FIX: Prefer admin session for cross-tenant write
        admin_factory = get_admin_session_factory()
        factory = admin_factory if admin_factory is not None else get_session_factory()
        async with factory() as session:
            async with session.begin():
                from sqlalchemy import select

                result = await session.execute(
                    select(PipelineRunModel).where(
                        PipelineRunModel.id == uuid.UUID(db_run_id)
                    )
                )
                db_run = result.scalar_one_or_none()
                if db_run:
                    db_run.status = "interrupted"
                    db_run.error_summary = (
                        f"Server crashed while at stage: {db_run.current_step}. "
                        "Use POST /api/v1/pipeline/{run_id}/resume to restart."
                    )

    def rebuild_run(self, run_data: dict[str, Any]) -> PipelineRun:
        """Rebuild a PipelineRun from database data for resume."""
        current_step = run_data.get("current_step", "requirements")
        try:
            stage = PipelineStage(current_step)
        except ValueError:
            stage = PipelineStage.REQUIREMENTS

        try:
            mode = ExecutionMode(run_data.get("execution_mode", "checkpoint"))
        except ValueError:
            mode = ExecutionMode.CHECKPOINT

        try:
            status = PipelineRunStatus(run_data.get("status", "running"))
        except ValueError:
            status = PipelineRunStatus.RUNNING

        context = run_data.get("context_snapshot") or {}

        run = PipelineRun(
            run_id=run_data["db_run_id"],
            project_id=run_data["project_id"],
            organization_id=run_data["organization_id"],
            user_id=run_data.get("user_id", ""),
            status=status,
            execution_mode=mode,
            current_stage=stage,
            context=context,
            error=run_data.get("error_summary"),
            db_run_id=run_data["db_run_id"],
        )

        # R15-FIX: Restore checkpoint_approved from persisted context.
        # Previously this was only an in-memory field, lost on restart.
        if context.get("__checkpoint_approved__"):
            run.checkpoint_approved = True

        # R21-FIX: Restore retry counters from persisted context.
        # Without this, resuming a run resets these to 0 (dataclass defaults),
        # allowing unlimited fix-retest cycles and challenge retries.
        run.fix_retest_cycle = context.get("__fix_retest_cycle__", 0)
        run.challenge_retry_count = context.get("__challenge_retry_count__", 0)

        return run


# -- Pipeline Orchestrator ----------------------------------------------------


class PipelineOrchestrator:
    """Orchestrates the 18-step agent pipeline.

    State is persisted to the database after every stage for crash recovery.
    The in-memory `_active_runs` dict is a hot cache; the DB is source of truth.

    Resource isolation:
    - Global semaphore limits concurrent pipelines (default: 10)
    - Per-pipeline timeout prevents runaway pipelines (default: 120 min)
    - Per-project rate limiter prevents one project from starving others
    """

    def __init__(
        self,
        max_concurrent: int = 10,
        calls_per_minute: int = 30,
        timeout_minutes: int = 120,
    ) -> None:
        self._active_runs: dict[str, PipelineRun] = {}
        self._persistence = PipelinePersistence()
        # DEFERRED-FIX-4: Lazy-init asyncio.Semaphore. Creating it at
        # construction binds to the current event loop. If the orchestrator
        # singleton is created before the loop starts (import time, or from
        # a different thread), it raises RuntimeError in Python 3.10+.
        self._semaphore: asyncio.Semaphore | None = None
        self._max_concurrent = max_concurrent
        self._rate_limiter = ProjectRateLimiter(calls_per_minute)
        self._timeout_seconds = timeout_minutes * 60
        # REVIEW-FIX: Per-run locks to prevent concurrent resume race condition.
        # Without this, two simultaneous /resume requests for the same run_id
        # both pass the status==RUNNING guard (both see PAUSED), both call
        # run_pipeline() on the SAME PipelineRun object, causing interleaved
        # stage execution, duplicated step results, and corrupted context.
        self._run_locks: dict[str, asyncio.Lock] = {}
        # R17-FIX: Track when runs enter PAUSED state for TTL eviction.
        # Without eviction, abandoned PAUSED runs (user never resumes) leak
        # Lock and PipelineRun objects in _active_runs and _run_locks forever.
        self._paused_at: dict[str, float] = {}
        self._PAUSED_TTL_SECONDS = 86400  # 24 hours

    def _get_semaphore(self) -> asyncio.Semaphore:
        """DEFERRED-FIX-4: Lazy-init semaphore on first use (inside event loop)."""
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._max_concurrent)
        return self._semaphore

    async def _acquire_distributed_slot(self, run_id: str) -> bool:
        """REVIEW-FIX: Acquire a slot in the distributed concurrency semaphore.

        Uses Valkey INCR + TTL to count active pipelines across all workers.
        Each worker's local asyncio.Semaphore only limits within that process.
        This distributed counter limits TOTAL concurrent pipelines cluster-wide.

        Returns True if a slot was acquired, False if at max capacity.
        """
        try:
            from app.services.valkey_pool import get_valkey_client
            client = await get_valkey_client()
            counter_key = "pipeline:active_count"

            # Lua script: atomically check < max, increment, and set TTL
            lua = """
            local count = tonumber(redis.call('GET', KEYS[1]) or 0)
            if count >= tonumber(ARGV[1]) then
                return 0
            end
            redis.call('INCR', KEYS[1])
            redis.call('EXPIRE', KEYS[1], ARGV[2])
            return 1
            """
            result = await client.eval(
                lua, 1, counter_key,
                str(self._max_concurrent),
                str(self._timeout_seconds + 60),  # TTL = timeout + buffer
            )
            if result == 1:
                # Also track this run's slot for cleanup
                slot_key = f"pipeline:slot:{run_id}"
                await client.set(slot_key, "1", ex=self._timeout_seconds + 60)
                return True
            return False
        except Exception as exc:
            logger.debug("distributed_semaphore_unavailable", error=str(exc))
            return True  # Fall back to local semaphore only

    async def _release_distributed_slot(self, run_id: str) -> None:
        """REVIEW-FIX: Release a slot in the distributed concurrency semaphore."""
        try:
            from app.services.valkey_pool import get_valkey_client
            client = await get_valkey_client()

            # Only decrement if this run's slot key exists (prevent double-release)
            slot_key = f"pipeline:slot:{run_id}"
            was_deleted = await client.delete(slot_key)
            if was_deleted:
                counter_key = "pipeline:active_count"
                # DECR, but never go below 0
                lua = """
                local count = tonumber(redis.call('GET', KEYS[1]) or 0)
                if count > 0 then
                    redis.call('DECR', KEYS[1])
                end
                return count - 1
                """
                await client.eval(lua, 1, counter_key)
        except Exception:
            pass  # Best-effort; slots have TTL for auto-cleanup

    @property
    def persistence(self) -> PipelinePersistence:
        """Expose persistence layer for testing and startup recovery."""
        return self._persistence

    @property
    def rate_limiter(self) -> ProjectRateLimiter:
        """Expose rate limiter for testing."""
        return self._rate_limiter

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    @property
    def timeout_seconds(self) -> int:
        return self._timeout_seconds

    def create_run(
        self,
        project_id: str,
        organization_id: str,
        user_id: str,
        execution_mode: ExecutionMode = ExecutionMode.CHECKPOINT,
    ) -> PipelineRun:
        """Create a new pipeline run."""
        # R17-FIX: Periodically evict abandoned paused runs
        self._evict_stale_paused()
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
        """Get a pipeline run by ID (hot cache only — sync method).

        REVIEW-FIX: For cases where the run may not be in the local cache
        (multi-process deployment), callers should use get_run_or_load() instead.
        This method is kept sync for backward compat (tests, sync callers).
        """
        return self._active_runs.get(run_id)

    async def get_run_or_load(
        self, run_id: str, organization_id: str | None = None,
    ) -> PipelineRun | None:
        """Get a pipeline run — checks hot cache first, then DB.

        REVIEW-FIX: In multi-process deployments (Cloud Run, multiple Uvicorn
        workers), a run may live in another worker's _active_runs cache but not
        ours. Always fall back to DB to find it. DB is the source of truth.
        """
        run = self._active_runs.get(run_id)
        if run is not None:
            return run

        # DB fallback — O(1) indexed lookup by run_id
        run_data = await self._persistence.find_run_by_id(
            run_id, organization_id=organization_id,
        )
        if run_data is not None:
            run = self._persistence.rebuild_run(run_data)
            self._active_runs[run.run_id] = run  # Warm the cache
            return run

        return None

    async def load_run_metadata(
        self, run_id: str, organization_id: str | None = None,
    ) -> PipelineRun | None:
        """Load run from DB WITHOUT executing it (for auth checks).

        Returns a PipelineRun object with metadata only, or None if not found.
        This does NOT start execution — callers should use resume_run() after
        verifying authorization.

        REFIX: Queries by run_id directly instead of scanning ALL interrupted
        runs (O(1) instead of O(N)).  Also includes "failed" and "interrupted"
        statuses — the previous implementation only found "running"/"paused",
        so failed runs couldn't be verified or resumed.

        RLS-READ-FIX: Pass organization_id to filter by tenant at the DB layer.
        """
        run_data = await self._persistence.find_run_by_id(run_id, organization_id=organization_id)
        if run_data is not None:
            return self._persistence.rebuild_run(run_data)
        return None

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

        # Contract coherence check: fail-fast before Shubham consumes the contract.
        # If FK references or endpoint paths are broken, stop now instead of
        # generating thousands of lines of incorrect code.
        if stage == PipelineStage.BACKEND_BUILD:
            contract = run.context.get("vikram", {}).get("contract", {})
            if contract:
                from app.services.contract_validator import validate_contract_coherence

                coherence_errors = validate_contract_coherence(contract)
                if coherence_errors:
                    error_summary = "; ".join(coherence_errors[:5])
                    if len(coherence_errors) > 5:
                        error_summary += f" … and {len(coherence_errors) - 5} more"
                    logger.error(
                        "contract_coherence_fail_fast",
                        run_id=run.run_id,
                        error_count=len(coherence_errors),
                        errors=coherence_errors,
                    )
                    from app.agents.base import AgentResult, AgentStatus

                    failed_result = AgentResult(
                        agent_name="contract_coherence_validator",
                        status=AgentStatus.FAILED,
                        error=(
                            f"Architecture contract has {len(coherence_errors)} coherence "
                            f"error(s): {error_summary}"
                        ),
                    )
                    step = StepResult(
                        stage=stage,
                        agent_name="contract_coherence_validator",
                        result=failed_result,
                        completed_at=datetime.now(timezone.utc),
                    )
                    await self._persist_step(run, step)
                    run.status = PipelineRunStatus.FAILED
                    run.error = failed_result.error
                    return step

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

    async def approve_checkpoint(
        self,
        run: PipelineRun,
        approved: bool,
        action: str = "approve",
        feedback: str = "",
    ) -> None:
        """Approve, reject, or redo the current checkpoint.

        Actions:
        - approve: Continue pipeline (with optional advisory feedback)
        - reject: Fail the pipeline
        - redo: Rewind to a previous stage and re-run with user feedback

        Redo targets:
        - CHECKPOINT_DESIGN -> rewinds to ARCHITECTURE (Vikram regenerates)
        - CHECKPOINT_TESTING -> rewinds to QUALITY_REVIEW (re-run quality + test + fix)
        """
        if run.status != PipelineRunStatus.PAUSED:
            raise ValueError(f"Pipeline {run.run_id} is not paused at a checkpoint")

        # R8-FIX: Validate the run is actually at a checkpoint stage.
        # In STEP_BY_STEP mode, the pipeline pauses after EVERY stage.
        # Without this check, approving a non-checkpoint pause would set
        # checkpoint_approved=True, which auto-approves the NEXT checkpoint.
        if run.current_stage not in CHECKPOINT_STAGES:
            raise ValueError(
                f"Pipeline {run.run_id} is paused at {run.current_stage.value}, "
                f"not at a checkpoint stage"
            )

        # Store feedback in context for downstream agents
        # R27-FIX-25: Cap feedback length and history size to prevent unbounded
        # growth via repeated redo cycles (each appends to the list).
        _MAX_FEEDBACK_LEN = 2000
        _MAX_FEEDBACK_HISTORY = 20
        if feedback:
            run.context["__user_feedback__"] = feedback[:_MAX_FEEDBACK_LEN]
            history = run.context.setdefault("__feedback_history__", [])
            history.append({
                "stage": run.current_stage.value,
                "action": action,
                "feedback": feedback[:_MAX_FEEDBACK_LEN],
            })
            if len(history) > _MAX_FEEDBACK_HISTORY:
                run.context["__feedback_history__"] = history[-_MAX_FEEDBACK_HISTORY:]

        if action == "redo":
            # Rewind to appropriate stage
            rewind_target = CHECKPOINT_REDO_TARGETS.get(run.current_stage)
            if rewind_target is None:
                raise ValueError(f"Cannot redo from {run.current_stage.value}")

            # R9-FIX: Leave status PAUSED. resume_run() will set RUNNING.
            # Setting RUNNING here caused the same APPROVE-DEADLOCK because
            # resume_run blocks on RUNNING status.
            run.current_stage = rewind_target
            run.checkpoint_approved = False
            # R34-FIX: Clear the persisted checkpoint_approved flag. Without
            # this, a server crash after redo causes rebuild_run() to read
            # the stale True flag from context, auto-approving the checkpoint
            # without user consent — a quality gate bypass.
            run.context.pop("__checkpoint_approved__", None)
            logger.info(
                "checkpoint_redo",
                run_id=run.run_id,
                rewind_to=rewind_target.value,
                feedback=feedback[:100] if feedback else "",
            )
        elif action == "reject" or not approved:
            # Reject: either explicit reject action or backward-compat approved=False
            run.status = PipelineRunStatus.FAILED
            run.error = "Checkpoint rejected by user"
            # R34-FIX: Clear persisted flag on reject too (defense-in-depth).
            run.context.pop("__checkpoint_approved__", None)
            logger.info("checkpoint_rejected", run_id=run.run_id, stage=run.current_stage.value)
        else:
            # Approve: set flag but leave status PAUSED. The user must call
            # /resume to restart execution. If we set RUNNING here, resume_run()
            # R9-FIX: blocks RUNNING as a double-execute guard → permanent deadlock.
            run.checkpoint_approved = True
            # R15-FIX: Persist checkpoint_approved in context so it survives
            # server restart / DB rebuild. Previously checkpoint_approved was
            # only an in-memory field — lost when the run was evicted from
            # hot cache and rebuilt from DB. User had to re-approve.
            run.context["__checkpoint_approved__"] = True
            logger.info("checkpoint_approved", run_id=run.run_id, stage=run.current_stage.value)

        # Persist checkpoint decision
        await self._persist_run(run)

    async def run_pipeline(self, run: PipelineRun) -> PipelineRun:
        """Execute the full pipeline from current stage to completion or pause.

        Runs stages sequentially, stopping at checkpoints (in CHECKPOINT mode)
        or after every stage (in STEP_BY_STEP mode).

        Resource isolation:
        - REVIEW-FIX: Acquires distributed Valkey lock (cross-process)
        - REVIEW-FIX: Acquires distributed Valkey slot (cluster-wide concurrency)
        - Acquires local semaphore (in-process concurrency)
        - Wraps entire execution with a timeout
        - Rate-limits AI calls per project

        State is persisted to the database after each stage for crash recovery.
        """
        # REVIEW-FIX: Distributed lock — ensure only ONE worker runs this pipeline
        if not await self._acquire_distributed_lock(run.run_id, ttl_seconds=self._timeout_seconds + 60):
            run.status = PipelineRunStatus.FAILED
            run.error = "Another worker is already executing this pipeline"
            return run

        # REVIEW-FIX: Distributed slot — respect cluster-wide concurrency limit
        if not await self._acquire_distributed_slot(run.run_id):
            await self._release_distributed_lock(run.run_id)
            run.status = PipelineRunStatus.FAILED
            run.error = f"Cluster-wide concurrency limit ({self._max_concurrent}) reached"
            return run

        async with self._get_semaphore():
            try:
                return await asyncio.wait_for(
                    self._run_pipeline_inner(run),
                    timeout=self._timeout_seconds,
                )
            except asyncio.TimeoutError:
                run.status = PipelineRunStatus.FAILED
                run.error = f"Pipeline timed out after {self._timeout_seconds // 60} minutes"
                await self._persist_run(run)
                # S-7-FIX: Evict failed run from hot cache
                self._active_runs.pop(run.run_id, None)
                # R26-FIX-6: Clean up _run_locks on timeout to prevent
                # unbounded memory growth from failing pipelines.
                self._run_locks.pop(run.run_id, None)
                logger.error(
                    "pipeline_timeout",
                    run_id=run.run_id,
                    timeout_minutes=self._timeout_seconds // 60,
                )
                return run
            except PipelineCostLimitError as exc:  # COST-CAP-FIX
                # COST-CAP-FIX: Per-pipeline cost cap exceeded. Mark as FAILED
                # with a clear error message so the user knows why it stopped.
                run.status = PipelineRunStatus.FAILED
                run.error = str(exc)  # COST-CAP-FIX
                try:
                    await self._persist_run(run)
                except Exception:
                    logger.error("persist_after_cost_cap_failed", run_id=run.run_id)
                self._active_runs.pop(run.run_id, None)
                self._run_locks.pop(run.run_id, None)
                logger.error(
                    "pipeline_cost_cap_exceeded",
                    run_id=run.run_id,
                    error=str(exc),
                )  # COST-CAP-FIX
                return run
            except Exception as exc:
                # R9-FIX: Catch ALL exceptions from _run_pipeline_inner.
                from app.services.ai_router import _sanitize_error
                safe_err = _sanitize_error(exc)
                run.status = PipelineRunStatus.FAILED
                run.error = f"Pipeline crashed: {type(exc).__name__}: {safe_err}"
                try:
                    await self._persist_run(run)
                except Exception:
                    logger.error("persist_after_crash_failed", run_id=run.run_id)
                self._active_runs.pop(run.run_id, None)
                # R26-FIX-6: Clean up _run_locks on crash.
                self._run_locks.pop(run.run_id, None)
                logger.error(
                    "pipeline_crash",
                    run_id=run.run_id,
                    error=safe_err,
                    exc_info=True,
                )
                return run
            except BaseException as exc:
                # R19-FIX: CancelledError is a BaseException in Python 3.9+.
                run.status = PipelineRunStatus.FAILED
                run.error = f"Pipeline cancelled: {type(exc).__name__}"
                try:
                    await self._persist_run(run)
                except Exception:
                    logger.error("persist_after_cancel_failed", run_id=run.run_id)
                self._active_runs.pop(run.run_id, None)
                # R26-FIX-6: Clean up _run_locks on cancel.
                self._run_locks.pop(run.run_id, None)
                from app.services.ai_router import _sanitize_error
                logger.warning(
                    "pipeline_cancelled",
                    run_id=run.run_id,
                    error=_sanitize_error(exc),
                )
                raise  # Re-raise to propagate cancellation
            finally:
                # REVIEW-FIX: Always release distributed resources regardless
                # of how the pipeline exits (success, failure, timeout, cancel).
                await self._release_distributed_lock(run.run_id)
                await self._release_distributed_slot(run.run_id)

    async def _get_max_step_order(self, run: PipelineRun) -> int:
        """Query the max step_order from DB for this run.

        REVIEW-FIX: After resume, run.step_results is empty (rebuild_run
        doesn't load step history). Using len(run.step_results) as step_order
        would start at 0, creating duplicate step_order values in pipeline.steps.
        Query the DB for the actual max to prevent collisions.
        """
        if not run.db_run_id:
            return 0
        try:
            from app.database import get_session_factory
            from sqlalchemy import func, select
            from app.models.pipeline import PipelineStep as PipelineStepModel

            factory = get_session_factory()
            async with factory() as session:
                async with session.begin():
                    # R18-FIX: Must set RLS context or FORCE ROW LEVEL SECURITY
                    # causes the query to return 0 rows. Without this, step_order
                    # restarts at 0 after resume, creating duplicate ordering values.
                    await self._persistence._set_rls_context(
                        session, run.organization_id, run.user_id,
                    )
                    result = await session.execute(
                        select(func.coalesce(func.max(PipelineStepModel.step_order), 0))
                        .where(PipelineStepModel.run_id == uuid.UUID(run.db_run_id))
                    )
                    return result.scalar_one()
        except Exception as exc:
            logger.warning("get_max_step_order_failed", run_id=run.run_id, error=str(exc))
            return len(run.step_results)

    async def _run_pipeline_inner(self, run: PipelineRun) -> PipelineRun:
        """Inner pipeline execution (runs inside semaphore + timeout)."""
        run.status = PipelineRunStatus.RUNNING

        # COST-AGG-FIX: Create a shared cost tracker for this run so ALL agents
        # (Shubham, Fixer, Aanya, etc.) contribute to the same cap.
        # Registered in the module-level dict so agents can look it up by run_id
        # without receiving it as a parameter (no signature changes required).
        _cost_tracker = ProjectCostTracker(pipeline_run_id=run.run_id)
        _RUN_COST_TRACKERS[run.run_id] = _cost_tracker
        # F6-FIX: Also register in ai_router's registry for pre-flight checks
        register_cost_tracker(run.run_id, _cost_tracker)

        # Persist initial state
        await self._persist_run(run)

        # REVIEW-FIX: Query max step_order from DB instead of using
        # len(run.step_results) which is 0 after resume (step history not loaded).
        step_count = await self._get_max_step_order(run)

        # WS-FIX: Notify clients pipeline has begun
        from app.routers.websocket import notify_pipeline_event, notify_stage_event
        try:
            await notify_pipeline_event(
                run.run_id,
                "pipeline_started",
                data={"execution_mode": run.execution_mode.value},
            )
        except Exception:
            pass  # WebSocket notification failures must NEVER crash the pipeline

        # PUBSUB-FIX: Cross-process broadcast — publish pipeline_started so other
        # workers' WebSocket clients receive the event.
        try:
            from app.services.pipeline_events import get_pipeline_event_publisher
            await get_pipeline_event_publisher().publish_stage_update(
                run.run_id,
                stage="pipeline",
                status="pipeline_started",
                agent_name="",
            )
        except Exception:
            pass

        # WEBHOOK-FIX: Also deliver pipeline.started to registered webhooks
        try:
            from app.services.webhook_service import broadcast_webhook_event
            await broadcast_webhook_event(
                run.organization_id,
                "pipeline.started",
                {"run_id": run.run_id, "project_id": run.project_id},
            )
        except Exception:
            pass

        while run.current_stage != PipelineStage.COMPLETED:
            # R27-FIX-21: Only rate-limit stages that actually make AI calls.
            # Checkpoints, delivery, and completed stages consume rate tokens
            # without needing them, wasting ~22% of rate budget.
            if run.current_stage not in CHECKPOINT_STAGES:
                await self._rate_limiter.acquire(run.project_id)

            # WS-FIX: Notify stage starting
            try:
                agent_for_stage = STAGE_AGENTS.get(run.current_stage)
                agent_name_ws = agent_for_stage if isinstance(agent_for_stage, str) else ""
                await notify_stage_event(
                    run.run_id,
                    "stage_started",
                    stage=run.current_stage.value,
                    agent=agent_name_ws,
                )
            except Exception:
                pass

            # PUBSUB-FIX: Cross-process broadcast — publish stage_started.
            try:
                from app.services.pipeline_events import get_pipeline_event_publisher
                await get_pipeline_event_publisher().publish_stage_update(
                    run.run_id,
                    stage=run.current_stage.value,
                    status="stage_started",
                    agent_name=agent_name_ws,
                )
            except Exception:
                pass

            step_result = await self.execute_stage(run)
            run.step_results.append(step_result)
            step_count += 1

            # WS-FIX: Notify stage completed + emit per-file events for generated files
            try:
                await notify_stage_event(
                    run.run_id,
                    "stage_completed",
                    stage=step_result.stage.value,
                    agent=step_result.agent_name,
                )
                await self._emit_file_events(run, step_result)
            except Exception:
                pass

            # PUBSUB-FIX: Cross-process broadcast — publish stage_completed.
            try:
                from app.services.pipeline_events import get_pipeline_event_publisher
                await get_pipeline_event_publisher().publish_stage_update(
                    run.run_id,
                    stage=step_result.stage.value,
                    status="stage_completed",
                    agent_name=step_result.agent_name,
                )
            except Exception:
                pass

            # WEBHOOK-FIX: Also deliver stage.completed to registered webhooks
            try:
                from app.services.webhook_service import broadcast_webhook_event
                await broadcast_webhook_event(
                    run.organization_id,
                    "stage.completed",
                    {"run_id": run.run_id, "stage": step_result.stage.value, "agent": step_result.agent_name},
                )
            except Exception:
                pass

            # ARTIFACT-FIX: Persist ZIP bytes to DB so download endpoint doesn't
            # rebuild on every request and ZIP survives context_snapshot purges.
            if step_result.stage.value == "delivery" and not step_result.skipped:
                try:
                    from app.engine.delivery import DeliveryEngine
                    _engine = DeliveryEngine()
                    _pkg = _engine.build_package(run.run_id, run.context)
                    await self._persist_artifact(run.run_id, run.organization_id, _pkg.zip_bytes)
                except Exception as _e:
                    logger.warning("artifact_persist_failed", run_id=run.run_id, error=str(_e)[:200])

            # GIT-FIX: Per-stage commit — creates a commit in the repo for each
            # completed stage so version history is preserved per stage.
            try:
                await self._git_stage_commit(run, step_result)
            except Exception as _git_exc:
                logger.debug("git_stage_commit_skipped", stage=step_result.stage.value, reason=str(_git_exc)[:100])

            # GIT-FIX: Auto-create PR after delivery stage
            if step_result.stage.value == "delivery":
                try:
                    await self._git_create_pr(run)
                except Exception as _pr_exc:
                    logger.debug("git_auto_pr_skipped", reason=str(_pr_exc)[:100])

            # REVIEW-FIX: Track per-step status in context for observability.
            # Stores the last completed/failed stage name and agent so the
            # /status endpoint and resume logic know exactly where we are.
            run.context["__last_step__"] = {
                "stage": run.current_stage.value,
                "agent": step_result.agent_name,
                "status": (
                    step_result.result.status.value if step_result.result
                    else ("skipped" if step_result.skipped else "pending")
                ),
                "step_order": step_count,
            }

            # Persist step result + run state to DB
            await self._persist_step(run, step_result, step_count)
            await self._persist_run(run)

            # Check if we need to pause
            if run.status == PipelineRunStatus.PAUSED:
                break

            # Check for failure — current_stage still points to the failed
            # stage so resume_run() will re-execute this exact stage.
            if step_result.result and step_result.result.status == AgentStatus.FAILED:
                run.status = PipelineRunStatus.FAILED
                run.error = (
                    f"Stage {run.current_stage.value} ({step_result.agent_name}) failed: "
                    f"{step_result.result.error or 'unknown error'}"
                )
                await self._persist_run(run)
                break

            # R28-FIX-2: Check quality loops BEFORE step-by-step pause.
            # Previously, step-by-step mode immediately advanced + paused,
            # skipping the challenge-retry and fix-retest loops entirely.
            # This silently disabled two critical quality gates in debug mode.

            # Architecture challenge-retry loop: after ARCHITECTURE_REVIEW,
            # if critical challenges found, rewind to ARCHITECTURE for Vikram
            # to re-generate with challenges as constraints (max 2 retries).
            if (
                run.current_stage == PipelineStage.ARCHITECTURE_REVIEW
                and step_result.result
                and step_result.result.status == AgentStatus.COMPLETED
                and self._has_critical_challenges(step_result)
                and run.challenge_retry_count < MAX_CHALLENGE_RETRIES
            ):
                run.challenge_retry_count += 1
                # Store challenges in context so Vikram can use them
                if step_result.result.output:
                    run.context["__architecture_challenges__"] = step_result.result.output.get("challenges", [])
                logger.info(
                    "architecture_challenge_retry",
                    run_id=run.run_id,
                    retry=run.challenge_retry_count,
                    max_retries=MAX_CHALLENGE_RETRIES,
                    critical_count=step_result.result.output.get("critical_count", 0) if step_result.result.output else 0,
                )
                # Rewind to ARCHITECTURE so Vikram regenerates
                run.current_stage = PipelineStage.ARCHITECTURE
                await self._persist_run(run)
                continue

            # F3-FIX: After Fixer stage, merge patched files back into source
            # agent context. Fixer writes patches to context["fixer"]["patched_files"]
            # but Aanya/Shubham read from context["shubham/aanya"]["file_contents"].
            # Without this merge, re-running Aanya after Fixer reads ORIGINAL unfixed code.
            if (
                run.current_stage == PipelineStage.FIXING
                and step_result.result
                and step_result.result.status == AgentStatus.COMPLETED
            ):
                fixer_output = run.context.get("fixer", {})
                if isinstance(fixer_output, dict):
                    patched = fixer_output.get("patched_files", {})
                    if patched and isinstance(patched, dict):
                        for target in ("shubham", "aanya"):
                            agent_ctx = run.context.get(target, {})
                            if isinstance(agent_ctx, dict) and "file_contents" in agent_ctx:
                                merged = 0
                                for path, content in patched.items():
                                    if path in agent_ctx["file_contents"]:
                                        agent_ctx["file_contents"][path] = content
                                        merged += 1
                                if merged:
                                    logger.info(
                                        "fixer_context_merge",
                                        run_id=run.run_id,
                                        target=target,
                                        files_merged=merged,
                                    )

            # Fix-retest loop: after FIXING, check if we need to re-run
            if (
                run.current_stage == PipelineStage.FIXING
                and step_result.result
                and step_result.result.status == AgentStatus.COMPLETED
                and self._has_errors_to_fix(step_result)
                and run.fix_retest_cycle < MAX_FIX_RETEST_CYCLES
            ):
                run.fix_retest_cycle += 1

                # I4-FIX: Fast-path — trivial fixes (syntax/import/typo) skip
                # the full quality review cycle and go directly to TESTING.
                # Structural/moderate fixes still re-run all quality gates.
                fix_severity = "STRUCTURAL"
                if step_result.result.output:
                    fix_severity = step_result.result.output.get(
                        "fix_severity", "STRUCTURAL"
                    )

                if fix_severity == "TRIVIAL":
                    run.context["__fix_fast_path__"] = True
                    logger.info(
                        "fix_retest_fast_path",
                        run_id=run.run_id,
                        cycle=run.fix_retest_cycle,
                        severity=fix_severity,
                    )
                    run.current_stage = PipelineStage.TESTING
                else:
                    run.context.pop("__fix_fast_path__", None)
                    logger.info(
                        "fix_retest_loop",
                        run_id=run.run_id,
                        cycle=run.fix_retest_cycle,
                        max_cycles=MAX_FIX_RETEST_CYCLES,
                        severity=fix_severity,
                    )
                    run.current_stage = PipelineStage.QUALITY_REVIEW

                await self._persist_run(run)
                continue

            # R21-FIX: Clear bulky output from step_result after persistence
            # AND after all checks that read output (fix-retest, challenge-retry).
            # step_results list is kept in-memory for the entire pipeline run.
            # Each step can hold the full AI response (10-100KB+). Over 18 stages,
            # this accumulates unboundedly. The output is already persisted to the
            # DB by _persist_step, so clear it from memory to prevent OOM.
            if step_result.result and hasattr(step_result.result, "output"):
                step_result.result.output = None

            # Step-by-step mode: pause after every stage (but AFTER quality loops)
            # R9-FIX: advance() BEFORE pausing. R28-FIX-2: moved after quality loops.
            if run.execution_mode == ExecutionMode.STEP_BY_STEP:
                next_stage = await self.advance(run)
                if next_stage is None:
                    # Pipeline completed — don't pause
                    break
                run.status = PipelineRunStatus.PAUSED
                await self._persist_run(run)
                break

            # Advance to next stage
            next_stage = await self.advance(run)
            if next_stage is None:
                break

        # Set final status if pipeline reached the end
        if run.current_stage == PipelineStage.COMPLETED and run.status == PipelineRunStatus.RUNNING:
            run.status = PipelineRunStatus.COMPLETED

        # Final persist
        await self._persist_run(run)

        # WS-FIX: Emit terminal pipeline-level events
        if run.status == PipelineRunStatus.COMPLETED:
            # Compute simulation summary for the completion event payload so
            # WebSocket subscribers don't need an extra HTTP round-trip.
            _sandbox_sim = bool(run.context.get("aarav", {}).get("is_simulation_sandbox", False))
            _git_sim = bool(run.context.get("git_agent", {}).get("is_simulation_git", False))
            _deploy_sim = bool(run.context.get("pranav", {}).get("is_simulation_deploy", False))
            _partially_simulated = _sandbox_sim or _git_sim or _deploy_sim
            _sim_summary = (
                {"sandbox_simulated": _sandbox_sim, "git_simulated": _git_sim, "deploy_simulated": _deploy_sim}
                if _partially_simulated else {}
            )
            try:
                await notify_pipeline_event(
                    run.run_id,
                    "pipeline_completed",
                    data={
                        "run_id": run.run_id,
                        "is_partially_simulated": _partially_simulated,
                        "simulation_summary": _sim_summary,
                    },
                )
            except Exception:
                pass
            # PUBSUB-FIX: Cross-process broadcast — publish pipeline_completed.
            try:
                from app.services.pipeline_events import get_pipeline_event_publisher
                await get_pipeline_event_publisher().publish_stage_update(
                    run.run_id,
                    stage="pipeline",
                    status="pipeline_completed",
                    agent_name="",
                )
            except Exception:
                pass
            # WEBHOOK-FIX: Also deliver pipeline.completed to registered webhooks
            try:
                from app.services.webhook_service import broadcast_webhook_event
                await broadcast_webhook_event(
                    run.organization_id,
                    "pipeline.completed",
                    {"run_id": run.run_id, "project_id": run.project_id},
                )
            except Exception:
                pass
            # TOKEN-USAGE-FIX: Persist per-model token usage to billing.token_usage
            # so cost analytics and quota enforcement have a durable record.
            # Uses the shared cost tracker created at pipeline start.
            # NOTE: billing.token_usage requires user_id (NOT NULL). Skip if the run
            # has no user_id (system-triggered runs). No pipeline_run_id column exists.
            try:
                tracker = _RUN_COST_TRACKERS.get(run.run_id)
                if tracker is not None and run.user_id is not None:
                    summary = tracker.summary()
                    if summary.get("total_cost_usd", 0) > 0:
                        from app.database import get_session_factory
                        from sqlalchemy import text as _text
                        from datetime import datetime, timezone as _tz
                        _factory = get_session_factory()
                        async with _factory() as _session:
                            per_model = summary.get("per_model_breakdown", {})
                            for model_id, stats in per_model.items():
                                await _session.execute(_text("""
                                    INSERT INTO billing.token_usage
                                        (id, organization_id, user_id, project_id,
                                         model, input_tokens, output_tokens, cost_usd, recorded_at)
                                    VALUES
                                        (gen_random_uuid(), :org_id::uuid, :user_id::uuid,
                                         :project_id::uuid, :model,
                                         :input_tokens, :output_tokens, :cost_usd, :now)
                                    ON CONFLICT DO NOTHING
                                """), {
                                    "org_id": str(run.organization_id),
                                    "user_id": str(run.user_id),
                                    "project_id": str(run.project_id),
                                    "model": model_id,
                                    "input_tokens": stats.get("input_tokens", 0),
                                    "output_tokens": stats.get("output_tokens", 0),
                                    "cost_usd": round(stats.get("cost_usd", 0.0), 6),
                                    "now": datetime.now(_tz.utc),
                                })
                            await _session.commit()
                            logger.info(
                                "token_usage_persisted",
                                run_id=run.run_id,
                                total_cost_usd=round(summary.get("total_cost_usd", 0), 4),
                                models=list(per_model.keys()),
                            )
            except Exception as _exc:
                # Non-fatal — billing data is important but must not crash the pipeline
                logger.warning("token_usage_persist_failed", run_id=run.run_id, error=str(_exc)[:200])
        elif run.status == PipelineRunStatus.FAILED:
            try:
                await notify_pipeline_event(
                    run.run_id,
                    "pipeline_failed",
                    data={"error": str(run.error or "")[:200]},
                )
            except Exception:
                pass
            # PUBSUB-FIX: Cross-process broadcast — publish pipeline_failed.
            try:
                from app.services.pipeline_events import get_pipeline_event_publisher
                await get_pipeline_event_publisher().publish_stage_update(
                    run.run_id,
                    stage="pipeline",
                    status="pipeline_failed",
                    agent_name="",
                    error=str(run.error or "")[:200],
                )
            except Exception:
                pass
            # WEBHOOK-FIX: Also deliver pipeline.failed to registered webhooks
            try:
                from app.services.webhook_service import broadcast_webhook_event
                await broadcast_webhook_event(
                    run.organization_id,
                    "pipeline.failed",
                    {"run_id": run.run_id, "project_id": run.project_id, "error": str(run.error or "")[:200]},
                )
            except Exception:
                pass

        # S-7-FIX: Evict terminal runs from hot cache to prevent OOM.
        # The DB is the source of truth; completed/failed runs don't need
        # to stay in memory.  Paused runs remain cached for checkpoint approval.
        if run.status in (PipelineRunStatus.COMPLETED, PipelineRunStatus.FAILED):
            self._active_runs.pop(run.run_id, None)
            self._paused_at.pop(run.run_id, None)  # R17-FIX: Clean up tracking
            # COST-AGG-FIX: Evict the shared cost tracker alongside the run.
            # Keeping it after the run completes would be a memory leak on long-lived
            # processes with many pipeline runs.
            _RUN_COST_TRACKERS.pop(run.run_id, None)
            # F6-FIX: Also unregister from ai_router's registry
            unregister_cost_tracker(run.run_id)
            logger.debug("run_evicted_from_cache", run_id=run.run_id, status=run.status.value)
        elif run.status == PipelineRunStatus.PAUSED:
            # R17-FIX: Track when this run entered PAUSED state for TTL eviction
            self._paused_at[run.run_id] = time.monotonic()

        return run

    def _get_run_lock(self, run_id: str) -> asyncio.Lock:
        """Get or create an asyncio.Lock for a specific run_id.

        REVIEW-FIX: Per-run lock prevents concurrent resume/execution of the
        same pipeline. Locks are cleaned up when runs reach terminal state.

        This provides LOCAL (in-process) locking only.  Cross-worker safety
        is also handled by _acquire_distributed_lock() which uses Valkey
        SET NX EX for cluster-wide mutual exclusion.
        """
        if run_id not in self._run_locks:
            self._run_locks[run_id] = asyncio.Lock()
        return self._run_locks[run_id]

    async def _acquire_distributed_lock(
        self, run_id: str, ttl_seconds: int = 300,
    ) -> bool:
        """REVIEW-FIX: Acquire a Valkey-based distributed lock for a pipeline run.

        Uses SET NX EX (set-if-not-exists with expiry) to ensure only ONE
        worker across all processes can execute a given pipeline run at a time.
        The TTL prevents deadlocks if the worker crashes (lock auto-expires).

        Returns True if lock acquired, False if another worker holds it.
        """
        try:
            from app.services.valkey_pool import get_valkey_client
            client = await get_valkey_client()
            lock_key = f"pipeline:lock:{run_id}"
            # SET NX EX: atomic set-if-not-exists with TTL
            was_set = await client.set(lock_key, "1", nx=True, ex=ttl_seconds)
            return bool(was_set)
        except Exception as exc:
            # Valkey unavailable — fall back to local-only locking
            logger.debug("distributed_lock_unavailable", run_id=run_id, error=str(exc))
            return True  # Permit execution (single-process fallback)

    async def _release_distributed_lock(self, run_id: str) -> None:
        """REVIEW-FIX: Release the Valkey-based distributed lock for a pipeline run."""
        try:
            from app.services.valkey_pool import get_valkey_client
            client = await get_valkey_client()
            lock_key = f"pipeline:lock:{run_id}"
            await client.delete(lock_key)
        except Exception:
            pass  # Best-effort release; TTL will expire it anyway

    def _evict_stale_paused(self) -> None:
        """R17-FIX: Evict locks and hot-cache entries for abandoned PAUSED runs.

        Called periodically during create_run/resume_run to prevent unbounded
        growth of _active_runs and _run_locks dicts. Runs that have been PAUSED
        for over 24 hours without user interaction are evicted from memory.
        The DB still has the full state, so /resume can reload them.
        """
        now = time.monotonic()
        stale = [
            rid for rid, ts in self._paused_at.items()
            if now - ts > self._PAUSED_TTL_SECONDS
        ]
        for rid in stale:
            self._active_runs.pop(rid, None)
            self._run_locks.pop(rid, None)
            self._paused_at.pop(rid, None)
        if stale:
            logger.info("evicted_stale_paused_runs", count=len(stale))

    async def resume_run(
        self, run_id: str, organization_id: str | None = None,
    ) -> PipelineRun | None:
        """Resume an interrupted pipeline from the last completed stage.

        Looks up the run in the hot cache first, then falls back to DB.
        Advances past the last completed stage and continues execution.

        RESUME-FIX: Uses find_run_by_id() (O(1) indexed lookup) instead of
        find_interrupted_runs() which scanned ALL interrupted runs across
        ALL tenants (O(N) + cross-tenant data exposure).

        R17-FIX: Evicts stale paused runs on each resume call to prevent
        unbounded growth of _run_locks dict from abandoned runs.

        REVIEW-FIX: Per-run lock prevents two concurrent /resume calls from
        both entering run_pipeline() on the same PipelineRun object.
        """
        # REVIEW-FIX: Acquire per-run lock to prevent concurrent resume race.
        # Two simultaneous /resume requests for the same run_id would both
        # see status=PAUSED, both call run_pipeline() concurrently, causing
        # interleaved stage execution and corrupted state.
        # R18-FIX: Clear paused timestamp BEFORE eviction so the run being
        # resumed is not accidentally evicted (if it was paused for > 24h).
        self._paused_at.pop(run_id, None)
        self._evict_stale_paused()

        lock = self._get_run_lock(run_id)
        async with lock:
            result = await self._resume_run_inner(run_id, organization_id)
            # R17-FIX: Clean up lock if run not found (prevents leak from
            # invalid run_id requests creating permanent Lock entries).
            if result is None:
                self._run_locks.pop(run_id, None)
            return result

    async def _resume_run_inner(
        self, run_id: str, organization_id: str | None = None,
    ) -> PipelineRun | None:
        """Inner resume logic (runs inside per-run lock)."""
        run = self.get_run(run_id)

        if run is None:
            # RESUME-FIX: Direct O(1) lookup instead of O(N) full scan
            # RLS-READ-FIX: Pass organization_id for tenant-scoped lookup
            run_data = await self._persistence.find_run_by_id(
                run_id, organization_id=organization_id,
            )
            if run_data is not None:
                run = self._persistence.rebuild_run(run_data)
                self._active_runs[run.run_id] = run

        if run is None:
            return None

        # R8-FIX: Guard against concurrent execution of the same pipeline.
        if run.status == PipelineRunStatus.RUNNING:
            logger.warning("resume_run_already_running", run_id=run.run_id)
            return run

        # Only resume if the run was interrupted, paused, or failed
        if run.status not in (
            PipelineRunStatus.PAUSED,
            PipelineRunStatus.FAILED,
            PipelineRunStatus.INTERRUPTED,  # R12-FIX
        ):
            return run

        # R9-FIX: Only block at unapproved checkpoints. After approve_checkpoint()
        # sets checkpoint_approved=True, the resume call should proceed.
        if run.status == PipelineRunStatus.PAUSED and run.current_stage in CHECKPOINT_STAGES:
            if not run.checkpoint_approved:
                # Don't auto-advance past checkpoints; user must approve first
                return run
            # Checkpoint was approved — fall through to run_pipeline

        # If failed or interrupted, reset status to running to retry from current stage
        if run.status in (PipelineRunStatus.FAILED, PipelineRunStatus.INTERRUPTED):
            run.status = PipelineRunStatus.RUNNING
            run.error = None

        logger.info(
            "pipeline_resuming",
            run_id=run.run_id,
            from_stage=run.current_stage.value,
            status=run.status.value,
        )

        result = await self.run_pipeline(run)

        # Clean up lock for terminal runs to prevent unbounded growth
        if result and result.status in (PipelineRunStatus.COMPLETED, PipelineRunStatus.FAILED):
            self._run_locks.pop(run_id, None)
            self._paused_at.pop(run_id, None)  # R17-FIX: Clean up tracking

        return result

    async def cancel_run(self, run_id: str, organization_id: str | None = None) -> bool | None:
        """Cancel a running or paused pipeline run.

        IDE-FIX: Returns True if cancelled, False if already in terminal state,
        None if run not found. Sets status to CANCELLED and persists to DB.
        """
        run = self.get_run(run_id)
        if run is None:
            # Check DB
            run_data = await self._persistence.find_run_by_id(run_id, organization_id=organization_id)
            if run_data is None:
                return None
            run = self._persistence.rebuild_run(run_data)
            self._active_runs[run.run_id] = run

        if organization_id and run.organization_id != organization_id:
            return None  # Treat cross-tenant as not found

        terminal = {PipelineRunStatus.COMPLETED, PipelineRunStatus.FAILED, PipelineRunStatus.CANCELLED}
        if run.status in terminal:
            return False

        # Mark cancelled
        run.status = PipelineRunStatus.CANCELLED
        run.error = "Cancelled by user"

        # Persist to DB
        try:
            await self._persist_run(run)
        except Exception:
            pass  # Status is set in-memory; DB update is best-effort

        logger.info("pipeline_cancelled", run_id=run_id, organization_id=organization_id)

        # WEBHOOK-FIX: Also deliver pipeline.cancelled to registered webhooks
        try:
            from app.services.webhook_service import broadcast_webhook_event
            await broadcast_webhook_event(
                run.organization_id,
                "pipeline.cancelled",
                {"run_id": run.run_id, "project_id": run.project_id},
            )
        except Exception:
            pass

        return True

    async def recover_interrupted_runs(self) -> int:
        """Find and mark interrupted runs on server startup.

        Called once during server startup. Marks runs that were RUNNING
        as interrupted so they can be manually resumed via /resume.

        Returns the number of interrupted runs found.
        """
        interrupted = await self._persistence.find_interrupted_runs()
        count = 0

        for run_data in interrupted:
            # Only mark RUNNING runs as interrupted (PAUSED are expected)
            if run_data["status"] == "running":
                await self._persistence.mark_interrupted(run_data["db_run_id"])
                logger.warning(
                    "pipeline_interrupted",
                    db_run_id=run_data["db_run_id"],
                    stage=run_data["current_step"],
                )
                count += 1
            else:
                # Rebuild paused runs into hot cache
                run = self._persistence.rebuild_run(run_data)
                self._active_runs[run.run_id] = run
                # R20-FIX: Track paused timestamp for TTL eviction. Without
                # this, restored PAUSED runs are never evicted by
                # _evict_stale_paused() (which only checks _paused_at dict),
                # leaking memory indefinitely for abandoned paused runs
                # across server restarts.
                self._paused_at[run.run_id] = time.monotonic()
                logger.info(
                    "pipeline_restored",
                    run_id=run.run_id,
                    stage=run.current_stage.value,
                )

        if count:
            logger.info("crash_recovery_complete", interrupted_count=count)
        return count

    # -- Internal Methods -----------------------------------------------------

    @staticmethod
    def _has_critical_challenges(step_result: StepResult) -> bool:
        """Check if the challenger found CRITICAL architecture issues.

        Returns True if the verdict is "reject" (critical challenges exist),
        meaning Vikram should re-generate the architecture.
        """
        if not step_result.result or not step_result.result.output:
            return False

        output = step_result.result.output
        return output.get("has_critical", False) or output.get("verdict") == "reject"

    @staticmethod
    def _has_errors_to_fix(step_result: StepResult) -> bool:
        """Check if the fixer step found remaining errors that need re-testing.

        Returns True if the fixer output indicates errors remain and at least
        some fixes were applied, meaning we should re-run quality gates.

        KEY-FIX: The Fixer agent outputs `errors_remaining`, `errors_fixed`,
        and `errors_received` — NOT `errors_found`/`fixes_applied`/`has_errors`.
        Previous implementation checked non-existent keys, so the fix-retest
        loop NEVER triggered.
        """
        if not step_result.result or not step_result.result.output:
            return False

        output = step_result.result.output
        # Fixer reports: errors_received, errors_fixed, errors_remaining
        errors_remaining = output.get("errors_remaining", 0)
        errors_fixed = output.get("errors_fixed", 0)

        # Re-run quality gates if fixer applied fixes but errors remain
        return bool(errors_remaining > 0 and errors_fixed > 0)

    async def _persist_run(self, run: PipelineRun) -> None:
        """Persist the current run state to the database (non-fatal on error).

        PERSIST-FIX: Tracks consecutive failures per run. If persistence
        fails too many times in a row, escalate to error level so monitoring
        catches it. Pipeline still continues (DB is not the sole source of
        truth during execution — hot cache is).

        R15-FIX: After first persist, save_run() syncs run_id with db_run_id.
        We must also update the _active_runs cache key so get_run() can find
        the run by the new (DB-stable) ID.
        """
        # R21-FIX: Persist retry counters in context so rebuild_run() can restore
        # them. Without this, resuming a run after a crash resets fix_retest_cycle
        # and challenge_retry_count to 0, allowing unlimited retries.
        run.context["__fix_retest_cycle__"] = run.fix_retest_cycle
        run.context["__challenge_retry_count__"] = run.challenge_retry_count

        # DEFERRED-FIX-1: Cap context_snapshot size before DB persist.
        # Each agent appends its full output to run.context (10-100KB+).
        # Over 18 stages + retest loops, this grows unboundedly in the DB.
        # Truncate large agent output values to prevent JSONB bloat.
        _MAX_CONTEXT_VALUE_CHARS = 8192
        # R25-FIX-7: Added __feedback_history__, __user_feedback__, and
        # __requirements__ to the protected set. __feedback_history__ is a list
        # of dicts that grows with each redo cycle. If truncated to
        # {"__truncated__": True}, .append() on the next cycle crashes with
        # AttributeError: 'dict' object has no attribute 'append'.
        _METADATA_KEYS = frozenset({
            "__fix_retest_cycle__", "__challenge_retry_count__",
            "__persist_failures__", "__last_step__",
            "__checkpoint_approved__", "__architecture_challenges__",
            "__paused_at__", "__feedback_history__", "__user_feedback__",
            "__requirements__",
        })
        # R29-FIX-3: Truncate a COPY of the context for DB persistence, NOT
        # the live in-memory context. Previous code mutated run.context directly,
        # replacing real agent output (e.g., Vikram's architecture contract) with
        # {"__truncated__": True} stubs. All downstream agents then received the
        # stub instead of real data, silently corrupting every non-trivial pipeline.
        import copy as _copy
        # R38-FIX: deepcopy to isolate nested dicts from concurrent mutation
        persist_context = _copy.deepcopy(run.context)
        import json as _json
        for key, val in list(persist_context.items()):
            if key in _METADATA_KEYS or not isinstance(val, (dict, list, str)):
                continue
            try:
                serialized = _json.dumps(val)
            except (TypeError, ValueError):
                continue
            if len(serialized) > _MAX_CONTEXT_VALUE_CHARS:
                persist_context[key] = {"__truncated__": True, "original_size": len(serialized)}
        original_context = run.context
        run.context = persist_context

        old_run_id = run.run_id  # Capture before save_run may change it
        try:
            await self._persistence.save_run(run)
            # R15-FIX: If save_run changed run.run_id (first persist syncs
            # with DB UUID), update the hot cache key. Otherwise get_run()
            # cannot find the run (old key → object with new run_id).
            if run.run_id != old_run_id:
                self._active_runs.pop(old_run_id, None)
                self._active_runs[run.run_id] = run
                # R18-FIX: Also remap _run_locks and _paused_at to the new
                # run_id. Without this, the lock/tracking entries orphan under
                # the old key and leak on each pipeline run.
                old_lock = self._run_locks.pop(old_run_id, None)
                if old_lock is not None:
                    self._run_locks[run.run_id] = old_lock
                old_ts = self._paused_at.pop(old_run_id, None)
                if old_ts is not None:
                    self._paused_at[run.run_id] = old_ts
                logger.debug(
                    "run_id_synced_with_db",
                    old_id=old_run_id,
                    new_id=run.run_id,
                )
            # Reset failure counter on success
            run.context = original_context  # R29-FIX-3: Restore live context
            run.context.pop("__persist_failures__", None)
        except Exception as exc:
            run.context = original_context  # R29-FIX-3: Restore live context
            # R26-FIX-7: Sanitize error — raw str(exc) from SQLAlchemy can
            # contain full SQL queries including context_snapshot JSONB with
            # potentially sensitive agent output data.
            from app.services.ai_router import _sanitize_error
            safe_err = _sanitize_error(exc)
            failures = run.context.get("__persist_failures__", 0) + 1
            run.context["__persist_failures__"] = failures
            if failures >= 3:
                logger.error(
                    "persist_run_repeated_failure",
                    run_id=run.run_id,
                    consecutive_failures=failures,
                    error=safe_err,
                )
            else:
                logger.warning("persist_run_failed", run_id=run.run_id, error=safe_err)

    async def _persist_step(self, run: PipelineRun, step: StepResult, order: int) -> None:
        """Persist a step result to the database (non-fatal on error)."""
        try:
            await self._persistence.save_step(run, step, order)
        except Exception as exc:
            # R27-FIX-9: Sanitize error (same as R26-FIX-7 for _persist_run).
            # SQLAlchemy exceptions can include full SQL with JSONB output_data,
            # leaking sensitive agent response content.
            from app.services.ai_router import _sanitize_error
            logger.warning(
                "persist_step_failed",
                run_id=run.run_id,
                stage=step.stage.value,
                agent=step.agent_name,
                error=_sanitize_error(exc),
            )

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
            # Agent not yet implemented -- skip with warning
            logger.warning("agent_not_found", agent=agent_name, stage=stage.value)
            step.skipped = True
            step.completed_at = datetime.now(timezone.utc)
            return step

        # Inject tilotma_mode for TILOTMA_REVIEW stage so Tilotma knows
        # to run in review mode (GO/NO-GO) instead of requirements mode.
        if stage == PipelineStage.TILOTMA_REVIEW and agent_name == "tilotma":
            run.context["tilotma_mode"] = "review"
        elif agent_name == "tilotma" and stage == PipelineStage.REQUIREMENTS:
            run.context.pop("tilotma_mode", None)  # Ensure requirements mode
            # CONTEXT-FIX: Tilotma reads "user_input" from context, but the
            # pipeline router stores requirements as "__requirements__". Bridge
            # the gap so Tilotma gets the user's project description.
            if "__requirements__" in run.context and "user_input" not in run.context:
                run.context["user_input"] = run.context["__requirements__"]

        # I2-FIX: Pass only the context keys this agent actually needs.
        # Eliminates the "context bloat blackhole" where every agent received
        # the full multi-MB accumulated context of all prior agents.
        filtered_context = _build_filtered_context(run.context, agent_name)

        # R11-FIX: Wrap agent.run() so crashed steps still get a StepResult
        # record in the database (for post-mortem diagnostics). Previously,
        # unhandled exceptions propagated up with no DB record for the step.
        #
        # I1-FIX: Also catches AgentInterruptRequest for dynamic re-dispatch.
        try:
            result = await agent.run(run.run_id, filtered_context)
        except AgentInterruptRequest as interrupt:
            result = await self._handle_interrupt(
                run, stage, agent_name, interrupt,
            )
        except Exception as exc:
            from app.services.ai_router import _sanitize_error
            safe_err = _sanitize_error(exc)
            logger.error("execute_agent_crashed", agent=agent_name, stage=stage.value, error=safe_err)
            result = AgentResult(
                agent_name=agent_name,
                status=AgentStatus.FAILED,
                error=safe_err,
            )

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

        # REFIX: Track which names successfully spawned tasks so zip
        # doesn't mismatch when get_agent() raises for some agents.
        #
        # F13-FIX: Selective deepcopy — only deep-copy prior agent output dicts
        # (which might be mutated by parallel agents). Metadata keys (__*__)
        # and small scalar values are shared by reference. This avoids the
        # full deepcopy which blocks the GIL for MB-sized contexts.
        import copy
        from types import MappingProxyType

        def _selective_context_copy(ctx: dict[str, Any]) -> dict[str, Any]:
            """Deep-copy only mutable agent output dicts; share metadata."""
            out: dict[str, Any] = {}
            for key, val in ctx.items():
                if key.startswith("__"):
                    # Metadata keys — shared (read-only contract)
                    out[key] = val
                elif isinstance(val, dict):
                    # Agent output — deep-copy to isolate mutations
                    out[key] = copy.deepcopy(val)
                elif isinstance(val, (str, int, float, bool, type(None))):
                    # Immutable scalars — share by reference
                    out[key] = val
                else:
                    # Lists, custom objects — deep-copy for safety
                    out[key] = copy.deepcopy(val)
            return out

        tasks = []
        loaded_names: list[str] = []
        for name in agent_names:
            try:
                agent = get_agent(name)
                # I2-FIX: Filter context BEFORE selective deepcopy
                filtered = _build_filtered_context(run.context, name)
                tasks.append(agent.run(run.run_id, _selective_context_copy(filtered)))
                loaded_names.append(name)
            except KeyError:
                logger.warning("agent_not_found", agent=name, stage=stage.value)

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            combined_output: dict[str, Any] = {}
            any_succeeded = False
            for name, res in zip(loaded_names, results):
                if isinstance(res, AgentResult):
                    combined_output[name] = res.output
                    # CTX-FIX: Store each parallel agent's output under its own
                    # key in run.context so downstream agents (e.g., Fixer) can
                    # find results via `context.get("karan", {})`.  Previously,
                    # only the combined result was stored under "karan,navya,deepika",
                    # making individual agent outputs invisible to downstream agents.
                    if res.status == AgentStatus.COMPLETED and res.output:
                        run.context[name] = res.output
                        any_succeeded = True
                elif isinstance(res, Exception):
                    # R11-FIX: Sanitize exception — may contain API keys
                    from app.services.ai_router import _sanitize_error
                    safe_err = _sanitize_error(res)
                    combined_output[name] = {"error": safe_err}
                    logger.error("parallel_agent_exception", agent=name, error=safe_err)

            # R10-FIX: If ALL agents threw exceptions, mark step as FAILED.
            # Previously this always returned COMPLETED, letting the pipeline
            # sail past quality gates when every quality reviewer crashed.
            step.result = AgentResult(
                agent_name=step.agent_name,
                status=AgentStatus.COMPLETED if any_succeeded else AgentStatus.FAILED,
                output=combined_output,
                error="All parallel agents failed" if not any_succeeded else None,
            )
        else:
            step.skipped = True

        step.completed_at = datetime.now(timezone.utc)
        return step

    # ── I1-FIX: Agent Interrupt Handling ───────────────────────────

    async def _handle_interrupt(
        self,
        run: PipelineRun,
        current_stage: PipelineStage,
        requesting_agent_name: str,
        interrupt: AgentInterruptRequest,
    ) -> AgentResult:
        """Handle an agent interrupt by re-running the target agent.

        Flow:
        1. Increment per-pair interrupt counter (persisted in run.context)
        2. Inject interrupt context for the target agent
        3. Re-run target agent → merge its updated output into run.context
        4. Clean up interrupt context
        5. Re-run the requesting agent with fresh context
        6. Return requesting agent's result

        Nested interrupts from the resumed agent are caught and rejected
        to prevent infinite interrupt chains.
        """
        from app.services.ai_router import _sanitize_error

        counter_key = (
            f"__interrupt_count__{interrupt.requesting_agent}"
            f"_{interrupt.target_agent}__"
        )
        run.context[counter_key] = run.context.get(counter_key, 0) + 1

        logger.info(
            "agent_interrupt_handling",
            run_id=run.run_id,
            from_agent=interrupt.requesting_agent,
            target_agent=interrupt.target_agent,
            reason=interrupt.reason[:200],
            interrupt_count=run.context[counter_key],
        )

        # Inject interrupt context so the target agent knows what's needed
        run.context["__interrupt_request__"] = {
            "from_agent": interrupt.requesting_agent,
            "reason": interrupt.reason,
            "required_changes": interrupt.required_changes,
        }

        # Re-run the target agent
        try:
            target_agent = get_agent(interrupt.target_agent)
            target_result = await target_agent.run(run.run_id, run.context)

            if target_result.status == AgentStatus.COMPLETED and target_result.output:
                run.context[interrupt.target_agent] = target_result.output
                logger.info(
                    "interrupt_target_completed",
                    run_id=run.run_id,
                    target_agent=interrupt.target_agent,
                )
            else:
                logger.warning(
                    "interrupt_target_failed",
                    run_id=run.run_id,
                    target_agent=interrupt.target_agent,
                    error=target_result.error,
                )
        except Exception as exc:
            logger.error(
                "interrupt_target_crashed",
                target_agent=interrupt.target_agent,
                error=_sanitize_error(exc),
            )
        finally:
            run.context.pop("__interrupt_request__", None)

        # Re-run the requesting agent with updated context
        try:
            requesting_agent = get_agent(requesting_agent_name)
            filtered_context = _build_filtered_context(
                run.context, requesting_agent_name
            )
            result = await requesting_agent.run(run.run_id, filtered_context)
            return result
        except AgentInterruptRequest:
            # Prevent infinite interrupt chains — agent tried to interrupt
            # again immediately after target re-ran.
            logger.warning(
                "interrupt_chain_blocked",
                run_id=run.run_id,
                agent=requesting_agent_name,
            )
            return AgentResult(
                agent_name=requesting_agent_name,
                status=AgentStatus.FAILED,
                error=(
                    "Interrupt chain detected: agent re-raised interrupt "
                    "after target re-run. Proceeding with current context."
                ),
            )
        except Exception as exc:
            return AgentResult(
                agent_name=requesting_agent_name,
                status=AgentStatus.FAILED,
                error=_sanitize_error(exc),
            )

    async def _emit_file_events(self, run: PipelineRun, step_result: StepResult) -> None:
        """Emit file_created WebSocket events for files generated in this stage.

        WS-FIX: Reads file_contents from the agent's context output and emits
        one file_created event per file so the frontend IDE can show real-time
        'Creating backend/app/models.py...' progress.
        """
        from app.routers.websocket import notify_file_created

        agent = step_result.agent_name.lower() if step_result.agent_name else ""

        # Determine which context key this agent writes to
        context_keys = {
            "shubham": ("shubham", "file_contents"),
            "aanya": ("aanya", "file_contents"),
            "dhruv": ("dhruv", "sql_files"),
        }

        if agent not in context_keys:
            return

        outer_key, inner_key = context_keys[agent]
        file_map: dict[str, str] = run.context.get(outer_key, {}).get(inner_key, {})

        if not file_map:
            return

        # Determine language from file extension
        def _lang_from_path(path: str) -> str:
            ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
            return {
                "py": "python", "ts": "typescript", "tsx": "typescript",
                "js": "javascript", "jsx": "javascript", "sql": "sql",
                "html": "html", "css": "css", "json": "json", "yaml": "yaml",
                "yml": "yaml", "md": "markdown", "sh": "bash",
            }.get(ext, ext)

        for file_path, content in file_map.items():
            try:
                await notify_file_created(
                    run.run_id,
                    file_path=file_path,
                    agent_name=agent,
                    size=len(content.encode("utf-8")),
                    language=_lang_from_path(file_path),
                )
            except Exception:
                pass  # Never crash pipeline on WS failure

    async def _persist_artifact(self, run_id: str, organization_id: str, zip_bytes: bytes) -> None:
        """Persist ZIP artifact to DB. ARTIFACT-FIX."""
        from app.database import get_admin_session_factory, get_session_factory
        from sqlalchemy import text
        factory = get_admin_session_factory() or get_session_factory()
        async with factory() as session:
            async with session.begin():
                await session.execute(
                    text("UPDATE pipeline.runs SET artifact_data = :data WHERE id = :id::uuid"),
                    {"data": zip_bytes, "id": run_id},
                )

    async def _git_stage_commit(self, run: PipelineRun, step_result: StepResult) -> None:
        """Commit generated files to git after each stage. GIT-FIX.

        Only commits if git_config exists in run.context (set by user when
        starting pipeline with git integration enabled).
        """
        git_config = run.context.get("git_config")
        if not git_config or step_result.skipped:
            return

        from app.agents.git_agent import GitAgent
        agent = GitAgent()

        # Collect files generated in this stage
        files: dict[str, str] = {}
        agent_name = step_result.agent_name.lower() if step_result.agent_name else ""
        for key in ("shubham", "aanya", "dhruv"):
            if agent_name == key or not agent_name:
                sub = run.context.get(key, {})
                if isinstance(sub, dict):
                    files.update(sub.get("file_contents", {}))
                    files.update(sub.get("sql_files", {}))

        if not files:
            return

        # Create branch for checkpoints
        branch = git_config.get("branch", "main")
        if step_result.stage.value in ("checkpoint_design", "checkpoint_testing"):
            branch = f"checkpoint/{step_result.stage.value}"

        await agent.commit_files(
            repo_url=git_config["repo_url"],
            token=git_config["token"],
            files=files,
            message=f"feat({step_result.stage.value}): generated by {step_result.agent_name or 'pipeline'} [{run.run_id[:8]}]",
            branch=branch,
        )

    async def _git_create_pr(self, run: PipelineRun) -> None:
        """Auto-create PR after delivery stage if git_config has auto_pr. GIT-FIX."""
        git_config = run.context.get("git_config")
        if not git_config:
            return
        # Only create PR when auto_pr is truthy
        auto_pr = git_config.get("auto_pr", "false")
        if str(auto_pr).lower() not in ("true", "1", "yes"):
            return

        from app.agents.git_agent import GitAgent
        agent = GitAgent()
        if hasattr(agent, "create_pull_request"):
            await agent.create_pull_request(
                repo_url=git_config["repo_url"],
                token=git_config.get("token", ""),
                pipeline_run_id=run.run_id,
                context=run.context,
            )

    async def _handle_checkpoint(
        self, run: PipelineRun, stage: PipelineStage
    ) -> StepResult:
        """Handle a checkpoint stage -- pause pipeline for user approval."""
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

        # CHECKPOINT-FIX: If checkpoint was already approved (via approve_checkpoint()
        # before resume_run()), skip the pause. Without this, the pipeline gets stuck
        # in a re-approval loop: resume sets status=RUNNING, execute_stage re-enters
        # _handle_checkpoint, which sets status=PAUSED again.
        if run.checkpoint_approved:
            run.checkpoint_approved = False  # Reset for next checkpoint
            # R16-FIX: Also clear the persisted context key. Without this,
            # a server restart after checkpoint 1 was consumed (but before
            # checkpoint 2) would rebuild with checkpoint_approved=True,
            # causing checkpoint 2 to auto-skip without user approval.
            run.context.pop("__checkpoint_approved__", None)
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

        # WEBHOOK-FIX: Deliver checkpoint.waiting to registered webhooks
        try:
            from app.services.webhook_service import broadcast_webhook_event
            await broadcast_webhook_event(
                run.organization_id,
                "checkpoint.waiting",
                {"run_id": run.run_id, "project_id": run.project_id, "stage": stage.value},
            )
        except Exception:
            pass

        return step


# -- Singleton ----------------------------------------------------------------

_orchestrator: PipelineOrchestrator | None = None


def get_orchestrator() -> PipelineOrchestrator:
    """Get or create the pipeline orchestrator singleton.

    Reads resource limits from Settings if available.
    """
    global _orchestrator
    if _orchestrator is None:
        try:
            from app.config import get_settings
            settings = get_settings()
            _orchestrator = PipelineOrchestrator(
                max_concurrent=settings.max_concurrent_pipelines,
                calls_per_minute=settings.ai_calls_per_minute_per_project,
                timeout_minutes=settings.pipeline_total_timeout_minutes,
            )
        except Exception:
            _orchestrator = PipelineOrchestrator()
    return _orchestrator
