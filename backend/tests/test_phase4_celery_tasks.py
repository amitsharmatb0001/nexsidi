"""Tests for Phase 4: Celery Task Queue with Checkpoint-Resume.

Covers:
- celery_app.py: Celery app factory configuration
- tasks/pipeline_tasks.py: run_pipeline_task, resume_pipeline_task
- config.py: use_celery feature flag, celery_broker_url, celery_result_backend
- routers/pipeline.py: Celery dispatch when use_celery=True
- Checkpoint-resume: crash recovery, SoftTimeLimitExceeded handling
- Feature flag: use_celery=False preserves asyncio.create_task() behavior
"""

from __future__ import annotations

import inspect
from unittest.mock import patch

import pytest


# ── Celery App Configuration Tests ────────────────────────────────


class TestCeleryAppConfiguration:
    """Celery app must be properly configured."""

    def test_celery_app_exists(self):
        """celery_app module must export a Celery app."""
        from app.celery_app import celery_app
        assert celery_app is not None
        assert celery_app.main == "nexsidi"

    def test_celery_uses_json_serialization(self):
        """Celery must use JSON serialization (no pickle attacks)."""
        from app.celery_app import celery_app
        assert celery_app.conf.task_serializer == "json"
        assert celery_app.conf.result_serializer == "json"
        assert "json" in celery_app.conf.accept_content

    def test_celery_acks_late(self):
        """Tasks must ack late (re-deliver on crash)."""
        from app.celery_app import celery_app
        assert celery_app.conf.task_acks_late is True

    def test_celery_reject_on_worker_lost(self):
        """Tasks must be re-delivered when worker process dies (OOM kill)."""
        from app.celery_app import celery_app
        assert celery_app.conf.task_reject_on_worker_lost is True

    def test_celery_prefetch_one(self):
        """Workers must prefetch one task at a time."""
        from app.celery_app import celery_app
        assert celery_app.conf.worker_prefetch_multiplier == 1

    def test_celery_tracks_started(self):
        """Tasks must track STARTED state."""
        from app.celery_app import celery_app
        assert celery_app.conf.task_track_started is True

    def test_celery_results_expire(self):
        """Results must expire (24h)."""
        from app.celery_app import celery_app
        assert celery_app.conf.result_expires == 86400

    def test_celery_includes_pipeline_tasks(self):
        """Celery must discover pipeline_tasks module."""
        from app.celery_app import celery_app
        assert "app.tasks.pipeline_tasks" in celery_app.conf.include

    def test_celery_visibility_timeout(self):
        """Broker visibility timeout must exceed task time_limit."""
        from app.celery_app import celery_app
        opts = celery_app.conf.broker_transport_options or {}
        vis_timeout = opts.get("visibility_timeout", 0)
        assert vis_timeout > 7200, "Visibility timeout must exceed time_limit (7200s)"


# ── Pipeline Task Tests ───────────────────────────────────────────


class TestPipelineTasks:
    """Pipeline Celery tasks must be properly defined."""

    def test_run_pipeline_task_exists(self):
        """run_pipeline_task must be a registered Celery task."""
        from app.tasks.pipeline_tasks import run_pipeline_task
        assert hasattr(run_pipeline_task, "apply_async")
        assert hasattr(run_pipeline_task, "delay")

    def test_resume_pipeline_task_exists(self):
        """resume_pipeline_task must be a registered Celery task."""
        from app.tasks.pipeline_tasks import resume_pipeline_task
        assert hasattr(resume_pipeline_task, "apply_async")

    def test_run_pipeline_task_name(self):
        """run_pipeline_task must have a descriptive task name."""
        from app.tasks.pipeline_tasks import run_pipeline_task
        assert run_pipeline_task.name == "nexsidi.run_pipeline"

    def test_resume_pipeline_task_name(self):
        """resume_pipeline_task must have a descriptive task name."""
        from app.tasks.pipeline_tasks import resume_pipeline_task
        assert resume_pipeline_task.name == "nexsidi.resume_pipeline"

    def test_run_task_has_time_limit(self):
        """run_pipeline_task must have a time limit."""
        from app.tasks.pipeline_tasks import run_pipeline_task
        source = inspect.getsource(run_pipeline_task)
        assert "time_limit" in source

    def test_run_task_uses_persistent_event_loop(self):
        """Celery task must use _run_async (persistent loop), not asyncio.run."""
        from app.tasks.pipeline_tasks import run_pipeline_task
        source = inspect.getsource(run_pipeline_task)
        # REVIEW-FIX: asyncio.run() destroys event loop after each task,
        # killing DB/Redis connection pools. Must use _run_async instead.
        assert "_run_async(" in source

    def test_ensure_services_exists(self):
        """_ensure_services must initialize DB, Valkey, etc. for workers."""
        from app.tasks.pipeline_tasks import _ensure_services
        import asyncio
        assert asyncio.iscoroutinefunction(_ensure_services)

    def test_shutdown_services_exists(self):
        """_shutdown_services must clean up after task completion."""
        from app.tasks.pipeline_tasks import _shutdown_services
        import asyncio
        assert asyncio.iscoroutinefunction(_shutdown_services)


# ── Checkpoint-Resume Tests ───────────────────────────────────────


class TestCeleryCheckpointResume:
    """Celery tasks must support crash-resilient checkpoint-resume."""

    def test_run_task_checks_retries(self):
        """run_pipeline_task must check self.request.retries for resume."""
        from app.tasks.pipeline_tasks import run_pipeline_task
        source = inspect.getsource(run_pipeline_task)
        assert "self.request.retries" in source

    def test_run_task_passes_is_retry(self):
        """run_pipeline_task must pass is_retry flag to async function."""
        from app.tasks.pipeline_tasks import run_pipeline_task
        source = inspect.getsource(run_pipeline_task)
        assert "is_retry" in source

    def test_async_run_resumes_on_retry(self):
        """_run_pipeline_async must resume (not recreate) on retry."""
        from app.tasks.pipeline_tasks import _run_pipeline_async
        source = inspect.getsource(_run_pipeline_async)
        assert "is_retry" in source
        assert "resume_run" in source

    def test_async_run_creates_new_on_first_attempt(self):
        """_run_pipeline_async must create new run on first attempt."""
        from app.tasks.pipeline_tasks import _run_pipeline_async
        source = inspect.getsource(_run_pipeline_async)
        assert "create_run" in source

    def test_soft_timeout_handler_exists(self):
        """_handle_soft_timeout must exist and be async."""
        from app.tasks.pipeline_tasks import _handle_soft_timeout
        import asyncio
        assert asyncio.iscoroutinefunction(_handle_soft_timeout)

    def test_soft_timeout_marks_interrupted(self):
        """_handle_soft_timeout must mark run as INTERRUPTED."""
        from app.tasks.pipeline_tasks import _handle_soft_timeout
        source = inspect.getsource(_handle_soft_timeout)
        assert "INTERRUPTED" in source

    def test_run_task_handles_soft_timeout(self):
        """run_pipeline_task must catch SoftTimeLimitExceeded."""
        from app.tasks.pipeline_tasks import run_pipeline_task
        source = inspect.getsource(run_pipeline_task)
        assert "SoftTimeLimitExceeded" in source

    def test_run_task_retries_on_soft_timeout(self):
        """run_pipeline_task must self.retry() after SoftTimeLimitExceeded."""
        from app.tasks.pipeline_tasks import run_pipeline_task
        source = inspect.getsource(run_pipeline_task)
        assert "self.retry" in source

    def test_run_task_has_max_retries_2(self):
        """run_pipeline_task must allow up to 2 retries."""
        from app.tasks.pipeline_tasks import run_pipeline_task
        source = inspect.getsource(run_pipeline_task)
        assert "max_retries=2" in source

    def test_run_task_exponential_backoff(self):
        """run_pipeline_task must use exponential backoff on retry."""
        from app.tasks.pipeline_tasks import run_pipeline_task
        source = inspect.getsource(run_pipeline_task)
        # countdown=30 * (2 ** self.request.retries)
        assert "2 **" in source

    def test_run_task_handles_max_retries_exceeded(self):
        """run_pipeline_task must handle MaxRetriesExceededError."""
        from app.tasks.pipeline_tasks import run_pipeline_task
        source = inspect.getsource(run_pipeline_task)
        assert "MaxRetriesExceededError" in source

    def test_resume_task_handles_soft_timeout(self):
        """resume_pipeline_task must also catch SoftTimeLimitExceeded."""
        from app.tasks.pipeline_tasks import resume_pipeline_task
        source = inspect.getsource(resume_pipeline_task)
        assert "SoftTimeLimitExceeded" in source

    def test_soft_timeout_imported(self):
        """SoftTimeLimitExceeded must be importable."""
        from app.tasks.pipeline_tasks import SoftTimeLimitExceeded
        assert issubclass(SoftTimeLimitExceeded, Exception)


# ── Service Lifecycle Tests ───────────────────────────────────────


class TestServiceLifecycle:
    """Service initialization must be idempotent (once per worker)."""

    def test_services_initialized_flag(self):
        """Module must track initialization state."""
        import app.tasks.pipeline_tasks as mod
        assert hasattr(mod, "_services_initialized")

    def test_make_result_helper(self):
        """_make_result must return JSON-serializable dict."""
        from app.tasks.pipeline_tasks import _make_result
        from unittest.mock import MagicMock

        mock_run = MagicMock()
        mock_run.run_id = "abc-123"
        mock_run.status.value = "completed"
        mock_run.current_stage.value = "completed"
        mock_run.error = None

        result = _make_result(mock_run)
        assert result["run_id"] == "abc-123"
        assert result["status"] == "completed"
        assert result["error"] is None


# ── Config Tests ──────────────────────────────────────────────────


class TestCeleryConfig:
    """Config must have Celery-related settings."""

    def test_use_celery_flag_exists(self):
        """use_celery feature flag must exist in Settings."""
        from app.config import Settings
        assert "use_celery" in Settings.model_fields

    def test_use_celery_defaults_false(self):
        """use_celery must default to False (current behavior preserved)."""
        from app.config import Settings
        field_info = Settings.model_fields["use_celery"]
        assert field_info.default is False

    def test_celery_broker_url_exists(self):
        """celery_broker_url setting must exist."""
        from app.config import Settings
        assert "celery_broker_url" in Settings.model_fields

    def test_celery_result_backend_exists(self):
        """celery_result_backend setting must exist."""
        from app.config import Settings
        assert "celery_result_backend" in Settings.model_fields


# ── Pipeline Router Dispatch Tests ────────────────────────────────


class TestPipelineRouterDispatch:
    """Pipeline router must dispatch to Celery when flag is on."""

    def test_router_has_celery_dispatch_path(self):
        """start_pipeline must check use_celery flag."""
        from app.routers.pipeline import start_pipeline
        source = inspect.getsource(start_pipeline)
        assert "use_celery" in source

    def test_router_imports_celery_task(self):
        """start_pipeline must import run_pipeline_task."""
        from app.routers.pipeline import start_pipeline
        source = inspect.getsource(start_pipeline)
        assert "run_pipeline_task" in source

    def test_router_uses_apply_async(self):
        """Celery dispatch must use apply_async (not delay)."""
        from app.routers.pipeline import start_pipeline
        source = inspect.getsource(start_pipeline)
        assert "apply_async" in source

    def test_router_preserves_asyncio_fallback(self):
        """When use_celery=False, must still use asyncio.create_task."""
        from app.routers.pipeline import start_pipeline
        source = inspect.getsource(start_pipeline)
        assert "asyncio.create_task" in source

    def test_router_correlates_task_id(self):
        """Celery task_id should match run_id for correlation."""
        from app.routers.pipeline import start_pipeline
        source = inspect.getsource(start_pipeline)
        assert "task_id=run.run_id" in source
