"""Celery application factory.

Creates a Celery app configured to use Valkey/Redis as both the broker
and result backend.  The app uses JSON serialization for safety
(no pickle deserialization attacks) and is tuned for long-running
pipeline tasks.

Usage:
    Start a worker::

        celery -A app.celery_app:celery_app worker --loglevel=info --concurrency=2

    The ``--concurrency=2`` flag limits each worker to 2 concurrent pipeline
    tasks.  Each task is long-running (minutes to hours) so high concurrency
    is undesirable.

    In production, run multiple worker processes behind a process manager
    (systemd, Kubernetes Deployment, Cloud Run Jobs).

Crash Recovery Architecture (NOT fire-and-forget):
    This Celery setup implements DURABLE pipeline execution via four layers:

    1. acks_late=True + reject_on_worker_lost=True
       The task stays in the broker queue until fully ACK'd. If the worker
       process is OOM-killed or crashes, the broker re-delivers the task
       to the next available worker automatically.

    2. Per-stage DB persistence (pipeline.py _persist_step + _persist_run)
       After EVERY agent completes, both the step result AND the run state
       (current_stage, context snapshot) are written to PostgreSQL. The DB
       is the single source of truth for resumable state.

    3. SoftTimeLimitExceeded handler (pipeline_tasks.py _handle_soft_timeout)
       200 seconds before the 2h hard kill, a soft timeout fires. The handler
       updates the run status to INTERRUPTED in the DB via a fresh session,
       then retries via self.retry(countdown=10).  On retry, the pipeline
       resumes from the last persisted stage.

    4. Startup crash recovery (pipeline.py recover_interrupted_runs)
       On server startup, any RUNNING runs are marked INTERRUPTED so the
       /resume endpoint can pick them up without manual intervention.

    WHY NOT TEMPORAL: Temporal would add operational complexity (separate
    cluster, SDK dependency, workflow versioning). The four-layer approach
    above achieves equivalent durability for NexSidi's use case with zero
    additional infrastructure.
"""

from __future__ import annotations

from celery import Celery

from app.config import get_settings


def create_celery_app() -> Celery:
    """Create and configure the Celery application.

    Uses the existing Valkey/Redis URL from settings as both broker and
    result backend — no additional infrastructure required.

    Key configuration choices:
    - ``task_acks_late=True``: Tasks are acknowledged AFTER completion,
      so a worker crash re-delivers the task to another worker.
    - ``worker_prefetch_multiplier=1``: Workers fetch one task at a time
      (pipelines are long — prefetching wastes memory).
    - ``task_track_started=True``: Enables STARTED state for monitoring.
    - ``result_expires=86400``: Results expire after 24h (prevents
      unbounded Valkey memory growth from completed tasks).
    """
    settings = get_settings()

    # Use dedicated Celery broker URL if configured, else fall back to Valkey URL
    broker_url = settings.celery_broker_url or settings.valkey_url
    result_backend = settings.celery_result_backend or settings.valkey_url

    app = Celery(
        "nexsidi",
        broker=broker_url,
        backend=result_backend,
    )

    app.conf.update(
        # Serialization: JSON only (no pickle — prevents deserialization attacks)
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        # Timezone
        timezone="UTC",
        enable_utc=True,
        # Task behavior
        task_track_started=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,  # Re-deliver on OOM kill / worker crash
        worker_prefetch_multiplier=1,
        # Result storage
        result_expires=86400,  # 24h TTL
        # Retry on broker connection loss
        broker_connection_retry_on_startup=True,
        # Worker: long-running tasks need long visibility timeout
        # (prevents broker re-delivering a running task as "lost")
        broker_transport_options={
            "visibility_timeout": 8000,  # > time_limit (7200) + buffer
        },
        # Task discovery
        include=["app.tasks.pipeline_tasks"],
    )

    return app


# Module-level singleton — imported by workers and task modules
celery_app = create_celery_app()
