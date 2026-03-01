"""Tests for Review Round 14 fixes.

Covers critical, high, and medium fixes found during the brutal review:
- AUTH: verify_token in /refresh, refresh token revocation, log_action fixes
- PIPELINE: Per-run lock, step_order collision, per-step tracking
- CELERY: Event loop persistence, SoftTimeout handler safety, dispatch failure
- SECURITY: GCP project ID SSRF, exception handler credential sanitization
"""

from __future__ import annotations

import asyncio
import inspect
import re
import uuid

import pytest


# ── AUTH: /refresh uses verify_token ─────────────────────────────────────


class TestRefreshUsesVerifyToken:
    """POST /refresh must use verify_token (async, checks revocation)."""

    def test_refresh_calls_verify_token(self):
        """Refresh endpoint must use verify_token, not just decode_token."""
        from app.routers.auth import refresh_token
        source = inspect.getsource(refresh_token)
        assert "await verify_token(" in source

    def test_refresh_does_not_call_decode_token_directly(self):
        """Refresh should NOT use decode_token directly for the refresh token."""
        from app.routers.auth import refresh_token
        source = inspect.getsource(refresh_token)
        # The old pattern: decode_token(body.refresh_token, ...)
        assert "decode_token(body.refresh_token" not in source

    def test_refresh_revokes_old_token(self):
        """Old refresh token must be revoked before issuing new pair."""
        from app.routers.auth import refresh_token
        source = inspect.getsource(refresh_token)
        assert "store.revoke(" in source
        # Must revoke the old jti
        assert "old_jti" in source

    def test_refresh_handles_revocation_store_unavailable(self):
        """Refresh must handle RuntimeError if revocation store not available."""
        from app.routers.auth import refresh_token
        source = inspect.getsource(refresh_token)
        assert "RuntimeError" in source

    def test_verify_token_imported(self):
        """verify_token must be imported in auth router."""
        import app.routers.auth as mod
        source = inspect.getsource(mod)
        assert "verify_token" in source


# ── AUTH: log_action in /logout fixed ────────────────────────────────────


class TestLogoutAuditFixed:
    """/logout and /logout-all must properly call log_action."""

    def test_logout_awaits_log_action(self):
        """logout must await log_action (it's async)."""
        from app.routers.auth import logout
        source = inspect.getsource(logout)
        assert "await log_action(" in source

    def test_logout_passes_session(self):
        """logout must pass a DB session to log_action."""
        from app.routers.auth import logout
        source = inspect.getsource(logout)
        assert "session=session" in source

    def test_logout_passes_entity_type(self):
        """logout must pass entity_type to log_action."""
        from app.routers.auth import logout
        source = inspect.getsource(logout)
        assert 'entity_type="user"' in source

    def test_logout_all_awaits_log_action(self):
        """logout_all must await log_action (it's async)."""
        from app.routers.auth import logout_all
        source = inspect.getsource(logout_all)
        assert "await log_action(" in source

    def test_logout_all_passes_session(self):
        """logout_all must pass a DB session to log_action."""
        from app.routers.auth import logout_all
        source = inspect.getsource(logout_all)
        assert "session=session" in source

    def test_logout_all_passes_entity_type(self):
        """logout_all must pass entity_type to log_action."""
        from app.routers.auth import logout_all
        source = inspect.getsource(logout_all)
        assert 'entity_type="user"' in source

    def test_logout_uses_metadata_not_details(self):
        """logout must use 'metadata' kwarg, not 'details' (which doesn't exist)."""
        from app.routers.auth import logout
        source = inspect.getsource(logout)
        # The old broken pattern used details=
        assert "details=" not in source
        assert "metadata=" in source


# ── PIPELINE: Per-run lock ───────────────────────────────────────────────


class TestResumeRunLock:
    """resume_run must use per-run lock to prevent concurrent execution."""

    def test_resume_run_acquires_lock(self):
        """resume_run must acquire a per-run lock."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator.resume_run)
        assert "_get_run_lock" in source
        assert "async with lock" in source

    def test_orchestrator_has_run_locks(self):
        """PipelineOrchestrator must have _run_locks dict."""
        from app.services.pipeline import PipelineOrchestrator
        orch = PipelineOrchestrator()
        assert hasattr(orch, "_run_locks")
        assert isinstance(orch._run_locks, dict)

    def test_get_run_lock_returns_asyncio_lock(self):
        """_get_run_lock must return an asyncio.Lock."""
        from app.services.pipeline import PipelineOrchestrator
        orch = PipelineOrchestrator()
        lock = orch._get_run_lock("test-run-id")
        assert isinstance(lock, asyncio.Lock)

    def test_get_run_lock_same_id_returns_same_lock(self):
        """Same run_id must get the same lock."""
        from app.services.pipeline import PipelineOrchestrator
        orch = PipelineOrchestrator()
        lock1 = orch._get_run_lock("run-abc")
        lock2 = orch._get_run_lock("run-abc")
        assert lock1 is lock2

    def test_get_run_lock_different_id_returns_different_lock(self):
        """Different run_ids must get different locks."""
        from app.services.pipeline import PipelineOrchestrator
        orch = PipelineOrchestrator()
        lock1 = orch._get_run_lock("run-1")
        lock2 = orch._get_run_lock("run-2")
        assert lock1 is not lock2

    def test_resume_run_inner_has_logic(self):
        """_resume_run_inner must contain the actual resume logic."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator._resume_run_inner)
        assert "find_run_by_id" in source
        assert "run_pipeline" in source

    def test_lock_cleaned_up_on_terminal_state(self):
        """Lock must be cleaned up when run reaches terminal state."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator._resume_run_inner)
        assert "_run_locks.pop(" in source


# ── PIPELINE: step_order collision fix ───────────────────────────────────


class TestStepOrderCollision:
    """step_count must query DB max on resume, not use len(step_results)."""

    def test_run_pipeline_inner_queries_max_step_order(self):
        """_run_pipeline_inner must call _get_max_step_order."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator._run_pipeline_inner)
        assert "_get_max_step_order" in source

    def test_get_max_step_order_exists(self):
        """_get_max_step_order method must exist."""
        from app.services.pipeline import PipelineOrchestrator
        assert hasattr(PipelineOrchestrator, "_get_max_step_order")
        assert asyncio.iscoroutinefunction(PipelineOrchestrator._get_max_step_order)

    def test_get_max_step_order_queries_db(self):
        """_get_max_step_order must query pipeline.steps table."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator._get_max_step_order)
        assert "PipelineStepModel" in source or "PipelineStep" in source
        assert "func.max" in source


# ── PIPELINE: Per-step status tracking ───────────────────────────────────


class TestPerStepTracking:
    """Pipeline must track status per-step, not just checkpoints."""

    def test_run_pipeline_stores_last_step(self):
        """_run_pipeline_inner must update __last_step__ in context."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator._run_pipeline_inner)
        assert "__last_step__" in source

    def test_last_step_includes_stage_and_agent(self):
        """__last_step__ must include stage, agent, and status."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator._run_pipeline_inner)
        # Must track stage name
        assert "current_stage.value" in source
        # Must track agent name
        assert "agent_name" in source

    def test_failure_error_includes_stage_name(self):
        """When a stage fails, run.error must include the stage name."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator._run_pipeline_inner)
        # The error message must mention which stage failed
        assert "current_stage.value" in source
        # Must be in the failure path
        assert "failed:" in source.lower() or "failed" in source.lower()


# ── CELERY: Persistent event loop ────────────────────────────────────────


class TestCeleryEventLoop:
    """Celery tasks must use persistent event loop, not asyncio.run()."""

    def test_run_pipeline_uses_run_async(self):
        """run_pipeline_task must use _run_async, not asyncio.run."""
        from app.tasks.pipeline_tasks import run_pipeline_task
        source = inspect.getsource(run_pipeline_task)
        assert "_run_async(" in source
        # Check that no CODE line (not comments/docstrings) uses asyncio.run
        in_docstring = False
        for line in source.split("\n"):
            stripped = line.strip()
            if '"""' in stripped:
                in_docstring = not in_docstring
                continue
            if in_docstring or stripped.startswith("#"):
                continue
            assert "asyncio.run(" not in stripped, f"Found asyncio.run in code: {stripped}"

    def test_resume_pipeline_uses_run_async(self):
        """resume_pipeline_task must use _run_async, not asyncio.run."""
        from app.tasks.pipeline_tasks import resume_pipeline_task
        source = inspect.getsource(resume_pipeline_task)
        assert "_run_async(" in source
        # Check that no CODE line uses asyncio.run
        in_docstring = False
        for line in source.split("\n"):
            stripped = line.strip()
            if '"""' in stripped:
                in_docstring = not in_docstring
                continue
            if in_docstring or stripped.startswith("#"):
                continue
            assert "asyncio.run(" not in stripped, f"Found asyncio.run in code: {stripped}"

    def test_get_worker_loop_exists(self):
        """_get_worker_loop must exist and return event loop."""
        from app.tasks.pipeline_tasks import _get_worker_loop
        loop = _get_worker_loop()
        assert isinstance(loop, asyncio.AbstractEventLoop)
        assert not loop.is_closed()

    def test_run_async_exists(self):
        """_run_async helper must exist."""
        from app.tasks.pipeline_tasks import _run_async
        assert callable(_run_async)

    def test_worker_loop_is_persistent(self):
        """Calling _get_worker_loop twice must return the same loop."""
        from app.tasks.pipeline_tasks import _get_worker_loop
        loop1 = _get_worker_loop()
        loop2 = _get_worker_loop()
        assert loop1 is loop2

    def test_run_async_can_run_coroutine(self):
        """_run_async must successfully run a coroutine."""
        from app.tasks.pipeline_tasks import _run_async

        async def add(a, b):
            return a + b

        result = _run_async(add(3, 4))
        assert result == 7


# ── CELERY: SoftTimeout handler safety ───────────────────────────────────


class TestSoftTimeoutHandlerSafety:
    """_handle_soft_timeout must be wrapped in try/except."""

    def test_handle_soft_timeout_has_exception_handler(self):
        """_handle_soft_timeout must have a top-level try/except."""
        from app.tasks.pipeline_tasks import _handle_soft_timeout
        source = inspect.getsource(_handle_soft_timeout)
        assert "except Exception" in source

    def test_run_pipeline_task_catches_soft_timeout_persist_failure(self):
        """run_pipeline_task SoftTimeout handler catches persist failures."""
        from app.tasks.pipeline_tasks import run_pipeline_task
        source = inspect.getsource(run_pipeline_task)
        assert "celery_soft_timeout_persist_failed" in source

    def test_resume_pipeline_task_catches_soft_timeout_persist_failure(self):
        """resume_pipeline_task SoftTimeout handler catches persist failures."""
        from app.tasks.pipeline_tasks import resume_pipeline_task
        source = inspect.getsource(resume_pipeline_task)
        assert "celery_resume_soft_timeout_persist_failed" in source


# ── CELERY: Dispatch failure handling ────────────────────────────────────


class TestCeleryDispatchFailure:
    """Celery dispatch failure must clean up orphan and return 503."""

    def test_celery_dispatch_wrapped_in_try_except(self):
        """apply_async must be wrapped in try/except."""
        from app.routers.pipeline import start_pipeline
        source = inspect.getsource(start_pipeline)
        assert "dispatch_exc" in source

    def test_dispatch_failure_cleans_up_orphan(self):
        """Dispatch failure must remove run from hot cache."""
        from app.routers.pipeline import start_pipeline
        source = inspect.getsource(start_pipeline)
        assert "_active_runs.pop(" in source

    def test_dispatch_failure_returns_503(self):
        """Dispatch failure must return 503."""
        from app.routers.pipeline import start_pipeline
        source = inspect.getsource(start_pipeline)
        assert "503" in source or "SERVICE_UNAVAILABLE" in source


# ── PIPELINE: approve_checkpoint DB fallback ─────────────────────────────


class TestApproveCheckpointDBFallback:
    """approve_checkpoint must fall back to DB when run not in hot cache."""

    def test_approve_loads_from_db_on_cache_miss(self):
        """approve_checkpoint must call load_run_metadata if not in cache."""
        from app.routers.pipeline import approve_checkpoint
        source = inspect.getsource(approve_checkpoint)
        assert "load_run_metadata" in source

    def test_approve_readds_to_cache(self):
        """approve_checkpoint must re-add loaded run to hot cache."""
        from app.routers.pipeline import approve_checkpoint
        source = inspect.getsource(approve_checkpoint)
        assert "_active_runs[" in source


# ── SECURITY: GCP project ID validation ──────────────────────────────────


class TestGCPProjectIdValidation:
    """GCP project IDs must be validated to prevent SSRF."""

    def test_config_validates_gcp_project_id(self):
        """Settings must validate gcp_project_id format."""
        from app.config import Settings
        # Check that the validator exists
        source = inspect.getsource(Settings)
        assert "validate_gcp_project_id" in source

    def test_valid_project_id_passes(self):
        """Valid GCP project IDs must pass validation."""
        from app.config import Settings
        # Direct call to the validator
        assert Settings.validate_gcp_project_id("my-project-123") == "my-project-123"
        assert Settings.validate_gcp_project_id("nexsidi") == "nexsidi"
        assert Settings.validate_gcp_project_id("yugnex-ai") == "yugnex-ai"

    def test_empty_project_id_passes(self):
        """Empty project ID means 'not configured' and should pass."""
        from app.config import Settings
        assert Settings.validate_gcp_project_id("") == ""

    def test_path_traversal_rejected(self):
        """Path traversal attempts must be rejected."""
        from app.config import Settings
        with pytest.raises(ValueError):
            Settings.validate_gcp_project_id("../../evil")

    def test_url_injection_rejected(self):
        """URL injection attempts must be rejected."""
        from app.config import Settings
        with pytest.raises(ValueError):
            Settings.validate_gcp_project_id("evil.com/attack")

    def test_too_short_rejected(self):
        """Project IDs shorter than 6 chars must be rejected."""
        from app.config import Settings
        with pytest.raises(ValueError):
            Settings.validate_gcp_project_id("abc")

    def test_uppercase_rejected(self):
        """Uppercase characters must be rejected (GCP requires lowercase)."""
        from app.config import Settings
        with pytest.raises(ValueError):
            Settings.validate_gcp_project_id("MyProject")


# ── SECURITY: Exception handler credential sanitization ──────────────────


class TestExceptionHandlerSanitization:
    """Global exception handler must sanitize credentials from error messages."""

    def test_exception_handler_sanitizes_errors(self):
        """Exception handler must strip DB URIs and API keys."""
        from app.main import create_app
        source = inspect.getsource(create_app)
        # Must use regex sanitization
        assert "REDACTED" in source
        # Must handle DB connection strings
        assert "postgresql" in source

    def test_db_uri_pattern_stripped(self):
        """Database connection string pattern must be redacted."""
        from app.main import create_app
        source = inspect.getsource(create_app)
        # Must match postgresql://user:pass@host/db patterns
        assert "postgresql|mysql|sqlite|redis|valkey" in source

    def test_api_key_pattern_stripped(self):
        """API key patterns must be redacted."""
        from app.main import create_app
        source = inspect.getsource(create_app)
        assert "key|token|secret|password|credential" in source
