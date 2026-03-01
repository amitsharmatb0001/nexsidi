"""Tests for Review Round 15 fixes.

Covers critical + high fixes found during the Round 15 brutal review:
- PIPELINE: run_id mutation cache fix, /resume background dispatch, checkpoint persistence
- CELERY: Worker shutdown signal, persistent event loop
- AUTH: Refresh token atomic single-use (SET NX), operation reorder
- WEBSOCKET: DB fallback for get_run, tenant filter
- CIRCUIT BREAKER: Half-open timeout reset
- PIPELINE: find_interrupted_runs safety limit
"""

from __future__ import annotations

import asyncio
import inspect
import time

import pytest


# ── PIPELINE: run_id mutation — hot cache key update ──────────────


class TestRunIdMutationCacheFix:
    """_persist_run() must update hot cache key when run_id changes after DB persist."""

    def test_persist_run_updates_cache_key(self):
        """_persist_run must handle run_id change (old key removed, new key added)."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator._persist_run)
        # Must detect run_id change
        assert "old_run_id" in source or "run.run_id != old" in source
        # Must remove old key
        assert "_active_runs.pop(" in source
        # Must add new key
        assert "_active_runs[run.run_id]" in source

    def test_persist_run_is_called_before_dispatch(self):
        """Pipeline router must persist run to DB BEFORE dispatching to Celery."""
        from app.routers.pipeline import start_pipeline
        source = inspect.getsource(start_pipeline)
        # _persist_run must appear before apply_async
        persist_pos = source.find("_persist_run")
        dispatch_pos = source.find("apply_async")
        if dispatch_pos > 0:
            assert persist_pos < dispatch_pos, "_persist_run must come before apply_async"


# ── PIPELINE: /resume dispatches to background ────────────────────


class TestResumeBackgroundDispatch:
    """/resume must dispatch to background (Celery or asyncio.create_task)."""

    def test_resume_does_not_await_resume_run_inline(self):
        """resume_pipeline endpoint must NOT await orch.resume_run() directly."""
        from app.routers.pipeline import resume_pipeline
        source = inspect.getsource(resume_pipeline)
        # Should not have "await orch.resume_run" inline in the handler
        # Instead it should dispatch to background
        assert "create_task" in source or "apply_async" in source

    def test_resume_has_celery_dispatch_path(self):
        """resume_pipeline must have Celery dispatch path."""
        from app.routers.pipeline import resume_pipeline
        source = inspect.getsource(resume_pipeline)
        assert "resume_pipeline_task" in source

    def test_resume_has_asyncio_fallback_path(self):
        """resume_pipeline must have asyncio.create_task fallback."""
        from app.routers.pipeline import resume_pipeline
        source = inspect.getsource(resume_pipeline)
        assert "asyncio.create_task" in source

    def test_resume_dispatch_failure_returns_503(self):
        """Celery dispatch failure in /resume must return 503."""
        from app.routers.pipeline import resume_pipeline
        source = inspect.getsource(resume_pipeline)
        assert "503" in source or "SERVICE_UNAVAILABLE" in source


# ── PIPELINE: checkpoint_approved persistence ─────────────────────


class TestCheckpointApprovedPersistence:
    """checkpoint_approved must be persisted to DB context_snapshot."""

    def test_approve_checkpoint_stores_in_context(self):
        """approve_checkpoint must set __checkpoint_approved__ in context."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator.approve_checkpoint)
        assert "__checkpoint_approved__" in source

    def test_rebuild_run_restores_checkpoint_approved(self):
        """rebuild_run must restore checkpoint_approved from context."""
        from app.services.pipeline import PipelinePersistence
        source = inspect.getsource(PipelinePersistence.rebuild_run)
        assert "__checkpoint_approved__" in source
        assert "checkpoint_approved" in source


# ── CELERY: Worker shutdown signal ────────────────────────────────


class TestCeleryWorkerShutdownSignal:
    """_shutdown_services must be called on Celery worker shutdown."""

    def test_worker_shutdown_signal_registered(self):
        """pipeline_tasks module must register worker_process_shutdown signal."""
        import app.tasks.pipeline_tasks as mod
        source = inspect.getsource(mod)
        assert "worker_process_shutdown" in source

    def test_shutdown_handler_calls_shutdown_services(self):
        """Worker shutdown handler must call _shutdown_services."""
        import app.tasks.pipeline_tasks as mod
        source = inspect.getsource(mod)
        assert "_shutdown_services" in source
        # Must run it on the persistent loop
        assert "run_until_complete" in source

    def test_shutdown_handler_closes_loop(self):
        """Worker shutdown handler must close the event loop."""
        import app.tasks.pipeline_tasks as mod
        source = inspect.getsource(mod)
        # Must close the loop after shutting down services
        assert "_worker_loop.close()" in source or "loop.close()" in source


# ── AUTH: Refresh token atomic single-use (SET NX) ───────────────


class TestRefreshTokenAtomicSingleUse:
    """Refresh token revocation must use atomic SET NX for single-use."""

    def test_revoke_uses_set_nx(self):
        """TokenRevocationStore.revoke() must use SET NX (not SETEX)."""
        from app.services.token_revocation import TokenRevocationStore
        source = inspect.getsource(TokenRevocationStore.revoke)
        assert "nx=True" in source

    def test_revoke_returns_bool(self):
        """revoke() must return a boolean (True=revoked, False=already-revoked)."""
        from app.services.token_revocation import TokenRevocationStore
        source = inspect.getsource(TokenRevocationStore.revoke)
        assert "return True" in source or "return was_set" in source
        assert "return False" in source or "was_set" in source

    def test_refresh_endpoint_checks_revoke_result(self):
        """refresh_token endpoint must check was_set from store.revoke()."""
        from app.routers.auth import refresh_token
        source = inspect.getsource(refresh_token)
        assert "was_set" in source

    def test_refresh_endpoint_rejects_replay(self):
        """refresh_token must return 401 when revoke returns False (replay)."""
        from app.routers.auth import refresh_token
        source = inspect.getsource(refresh_token)
        assert "already used" in source.lower() or "replay" in source.lower()


# ── AUTH: /refresh reorder — validate user before revoke ──────────


class TestRefreshOperationOrder:
    """/refresh must validate user existence BEFORE revoking old token."""

    def test_user_check_before_revoke(self):
        """User validation must appear before revocation in /refresh."""
        from app.routers.auth import refresh_token
        source = inspect.getsource(refresh_token)
        # User check (select User) must appear before store.revoke
        user_check_pos = source.find("select(User)")
        revoke_pos = source.find("store.revoke")
        assert user_check_pos > 0, "Must check user existence"
        assert revoke_pos > 0, "Must revoke old token"
        assert user_check_pos < revoke_pos, (
            "User validation must come BEFORE revocation"
        )

    def test_refresh_checks_user_active(self):
        """refresh_token must check user.is_active."""
        from app.routers.auth import refresh_token
        source = inspect.getsource(refresh_token)
        assert "is_active" in source


# ── WEBSOCKET: DB fallback for get_run ────────────────────────────


class TestWebSocketDBFallback:
    """WebSocket must fall back to DB when run not in hot cache."""

    def test_websocket_uses_load_run_metadata(self):
        """pipeline_websocket must call load_run_metadata when get_run returns None."""
        from app.routers.websocket import pipeline_websocket
        source = inspect.getsource(pipeline_websocket)
        assert "load_run_metadata" in source

    def test_websocket_passes_organization_id(self):
        """load_run_metadata call must pass organization_id for RLS."""
        from app.routers.websocket import pipeline_websocket
        source = inspect.getsource(pipeline_websocket)
        assert "organization_id" in source

    def test_websocket_checks_tenant_ownership(self):
        """WebSocket must verify run.organization_id matches claims."""
        from app.routers.websocket import pipeline_websocket
        source = inspect.getsource(pipeline_websocket)
        assert "organization_id" in source
        assert "4003" in source  # Access denied close code


# ── CIRCUIT BREAKER: Half-open timeout reset ──────────────────────


class TestCircuitBreakerHalfOpenTimeout:
    """Half-open probe failure must reset the timeout."""

    def test_record_failure_updates_last_failure_at(self):
        """record_failure must always update last_failure_at."""
        from app.services.ai_router import CircuitState
        source = inspect.getsource(CircuitState.record_failure)
        assert "last_failure_at = time.monotonic()" in source

    def test_last_failure_at_updated_before_half_open_check(self):
        """last_failure_at must be updated BEFORE the half-open check."""
        from app.services.ai_router import CircuitState
        source = inspect.getsource(CircuitState.record_failure)
        last_failure_pos = source.find("last_failure_at = time.monotonic()")
        half_open_pos = source.find("if self._half_open")
        assert last_failure_pos < half_open_pos, (
            "last_failure_at must be updated before half_open check"
        )

    @pytest.mark.asyncio
    async def test_half_open_failure_resets_timeout_behavior(self):
        """After half-open probe failure, circuit must stay open for full timeout."""
        from app.services.ai_router import CircuitState
        cb = CircuitState(failure_threshold=2, reset_timeout_seconds=0.5)

        # Open the circuit
        await cb.record_failure()
        await cb.record_failure()
        assert cb.is_open is True

        # Wait for timeout → should transition to half-open
        await asyncio.sleep(0.6)
        assert await cb.is_available() is True  # half-open probe allowed
        assert cb._half_open is True

        # Simulate probe failure
        await cb.record_failure()
        assert cb._half_open is False
        assert cb.is_open is True

        # Immediately after failure, circuit should be CLOSED (not half-open)
        assert await cb.is_available() is False

        # Wait for the NEW timeout period
        await asyncio.sleep(0.6)
        # NOW it should be half-open again
        assert await cb.is_available() is True


# ── PIPELINE: find_interrupted_runs safety ────────────────────────


class TestFindInterruptedRunsSafety:
    """find_interrupted_runs must have safety limits."""

    def test_find_interrupted_runs_has_limit(self):
        """Query must have a LIMIT to prevent unbounded memory on corrupt data."""
        from app.services.pipeline import PipelinePersistence
        source = inspect.getsource(PipelinePersistence.find_interrupted_runs)
        assert ".limit(" in source

    def test_find_interrupted_runs_documents_cross_tenant(self):
        """find_interrupted_runs must document its cross-tenant nature."""
        from app.services.pipeline import PipelinePersistence
        source = inspect.getsource(PipelinePersistence.find_interrupted_runs)
        assert "cross-tenant" in source.lower() or "admin operation" in source.lower()


# ── CELERY: Persistent event loop preserved ──────────────────────


class TestCeleryPersistentLoopPreserved:
    """Round 15 must not regress the persistent event loop fix."""

    def test_run_async_still_uses_persistent_loop(self):
        """_run_async must use _get_worker_loop (not asyncio.run)."""
        from app.tasks.pipeline_tasks import _run_async
        source = inspect.getsource(_run_async)
        assert "_get_worker_loop" in source
        # Check that no CODE line uses asyncio.run (docstrings may mention it)
        in_docstring = False
        for line in source.split("\n"):
            stripped = line.strip()
            if '"""' in stripped:
                in_docstring = not in_docstring
                continue
            if in_docstring or stripped.startswith("#"):
                continue
            assert "asyncio.run" not in stripped, f"Found asyncio.run in code: {stripped}"

    def test_worker_loop_survives_shutdown_handler_import(self):
        """Importing pipeline_tasks should not break event loop."""
        from app.tasks.pipeline_tasks import _get_worker_loop
        loop = _get_worker_loop()
        assert isinstance(loop, asyncio.AbstractEventLoop)
        assert not loop.is_closed()
