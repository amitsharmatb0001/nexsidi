"""Regression tests for Round 26 code review fixes.

Covers all CRITICAL, HIGH, and key MEDIUM fixes from R26.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── R26-FIX-1: Token revocation bypass after lazy re-init ──────────


class TestR26Fix1RevocationBypass:
    """Verify that after lazy re-init, Valkey errors in production raise JWTError."""

    def test_auth_verify_token_recheck_failure_not_silenced(self):
        """Source code must raise JWTError in production on re-check failure,
        not silently pass."""
        from app.services.auth import verify_token
        source = inspect.getsource(verify_token)
        # The old code had `except Exception: pass` with no production check
        # The fix adds `if _gs().is_production: raise JWTError(...)`
        assert "R26-FIX-1" in source
        assert "Token revocation service unavailable" in source

    def test_verify_token_except_exception_not_bare_pass(self):
        """The except Exception block after re-check must NOT be a bare `pass`.
        It must check is_production and raise JWTError."""
        from app.services.auth import verify_token
        source = inspect.getsource(verify_token)
        # After R26-FIX-1, the block must have the production check
        # Look for the specific pattern: except Exception -> is_production -> raise JWTError
        lines = source.split("\n")
        found_fix = False
        for i, line in enumerate(lines):
            if "R26-FIX-1" in line:
                found_fix = True
                break
        assert found_fix, "R26-FIX-1 comment not found in verify_token"
        # Ensure there's no bare `pass` in the re-check except block
        # The old code was: `except Exception: pass  # Fall through`
        # The new code has: `if _gs().is_production: raise JWTError(...)`
        assert "raise JWTError" in source


# ── R26-FIX-3: Incompatible locking in context_engine.py ──────────


class TestR26Fix3ContextLocking:
    """Verify clear_pipeline uses self._redis.lock() instead of manual SET NX."""

    def test_clear_pipeline_uses_redis_lock(self):
        """Source must use self._redis.lock(), not manual SET NX."""
        from app.services.context_engine import ContextEngine
        source = inspect.getsource(ContextEngine.clear_pipeline)
        # Must use self._redis.lock() — same mechanism as store()
        assert "self._redis.lock(" in source
        # Must NOT use manual SET NX
        assert "self._redis.set(lock_key" not in source
        # Must NOT use unconditional DELETE for lock release
        assert "await self._redis.delete(lock_key)" not in source
        assert "R26-FIX-3" in source

    def test_clear_pipeline_raises_on_lock_timeout(self):
        """clear_pipeline must raise TimeoutError, not proceed without lock."""
        from app.services.context_engine import ContextEngine
        source = inspect.getsource(ContextEngine.clear_pipeline)
        assert "raise TimeoutError" in source
        assert "R26-FIX-8" in source


# ── R26-FIX-4: ChatSession MissingGreenlet ────────────────────────


class TestR26Fix4ChatSessionEagerLoad:
    """Verify chat routes use selectinload to prevent MissingGreenlet."""

    def test_list_chat_sessions_no_selectinload_messages(self):
        """R28-FIX-7: list_chat_sessions should NOT eagerly load messages.
        (Overrides original R26-FIX-4 for list endpoint — selectinload
        for ALL messages on a paginated list causes O(N*M) memory usage.)
        Only get_chat_session should use selectinload for messages.
        """
        from app.routers.chat import list_chat_sessions
        source = inspect.getsource(list_chat_sessions)
        assert "selectinload(ChatSession.messages)" not in source

    def test_get_chat_session_uses_selectinload(self):
        from app.routers.chat import get_chat_session
        source = inspect.getsource(get_chat_session)
        assert "selectinload" in source
        assert "ChatSession.messages" in source

    def test_selectinload_imported(self):
        """selectinload must be imported in chat router."""
        import app.routers.chat as chat_module
        assert hasattr(chat_module, "selectinload") or "selectinload" in dir(chat_module)


# ── R26-FIX-5: WhatsAppAccount TenantMixin ────────────────────────


class TestR26Fix5WhatsAppTenant:
    """Verify WhatsAppAccount includes TenantMixin and organization_id."""

    def test_whatsapp_has_tenant_mixin(self):
        from app.models.auth import WhatsAppAccount
        from app.models.base import TenantMixin
        assert issubclass(WhatsAppAccount, TenantMixin)

    def test_whatsapp_has_organization_id(self):
        from app.models.auth import WhatsAppAccount
        assert hasattr(WhatsAppAccount, "organization_id")


# ── R26-FIX-6: _run_locks cleanup on pipeline crash ──────────────


class TestR26Fix6RunLocksCleanup:
    """Verify _run_locks are cleaned up on timeout, crash, and cancel."""

    def test_run_pipeline_timeout_cleans_locks(self):
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator.run_pipeline)
        # All three error paths must clean up _run_locks
        assert source.count("self._run_locks.pop(run.run_id, None)") >= 3
        assert "R26-FIX-6" in source


# ── R26-FIX-7: _persist_run sanitizes error logs ─────────────────


class TestR26Fix7PersistRunSanitize:
    """Verify _persist_run uses _sanitize_error instead of raw str(exc)."""

    def test_persist_run_uses_sanitize_error(self):
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator._persist_run)
        assert "_sanitize_error" in source
        assert "R26-FIX-7" in source
        # The old code used raw str(exc) in the error handler
        # Now it should use safe_err
        assert "error=safe_err" in source


# ── R26-FIX-9: Valkey password override ───────────────────────────


class TestR26Fix9ValkeyPassword:
    """Verify password kwarg is only passed when explicitly configured."""

    def test_password_not_passed_when_empty(self):
        from app.services.valkey_pool import get_valkey_client
        source = inspect.getsource(get_valkey_client)
        # Must NOT have password=settings.valkey_password or None
        assert 'password=settings.valkey_password or None' not in source
        # Must check if password is set before adding to kwargs
        assert "if settings.valkey_password" in source
        assert "R26-FIX-9" in source


# ── R26-FIX-10: ProjectResponse allows NULL description ──────────


class TestR26Fix10ProjectDescription:
    """Verify ProjectResponse accepts None for description."""

    def test_project_response_accepts_none_description(self):
        from app.schemas.project import ProjectResponse
        resp = ProjectResponse(
            id=uuid.uuid4(),
            name="Test",
            description=None,
            organization_id=uuid.uuid4(),
            created_at=datetime.now(timezone.utc),
        )
        assert resp.description is None

    def test_project_response_defaults_empty(self):
        """When description is omitted, defaults to empty string."""
        from app.schemas.project import ProjectResponse
        resp = ProjectResponse(
            id=uuid.uuid4(),
            name="Test",
            organization_id=uuid.uuid4(),
            created_at=datetime.now(timezone.utc),
        )
        assert resp.description == ""


# ── R26-FIX-11: Concurrent pipeline guard ────────────────────────


class TestR26Fix11ConcurrentPipelineGuard:
    """Verify start_pipeline checks for active runs."""

    def test_start_pipeline_checks_active_runs(self):
        from app.routers.pipeline import start_pipeline
        source = inspect.getsource(start_pipeline)
        assert "R26-FIX-11" in source
        assert "409" in source or "HTTP_409_CONFLICT" in source
        assert "already running" in source


# ── R26-FIX-12: Celery SoftTimeLimitExceeded + max retries ───────


class TestR26Fix12CeleryRetryHandling:
    """Verify SoftTimeLimitExceeded handler catches MaxRetriesExceededError."""

    def test_run_pipeline_task_catches_max_retries_in_soft_timeout(self):
        from app.tasks.pipeline_tasks import run_pipeline_task
        source = inspect.getsource(run_pipeline_task)
        # The fix wraps self.retry() in try/except MaxRetriesExceededError
        # within the SoftTimeLimitExceeded handler
        assert "R26-FIX-12" in source
        # Must have try/except around self.retry in the soft timeout handler
        assert "Soft time limit exceeded after all retries" in source

    def test_resume_pipeline_task_catches_max_retries_in_soft_timeout(self):
        from app.tasks.pipeline_tasks import resume_pipeline_task
        source = inspect.getsource(resume_pipeline_task)
        assert "R26-FIX-12" in source
        assert "Resume soft time limit exceeded after all retries" in source


# ── R26-FIX-24: JWT org claim required ───────────────────────────


class TestR26Fix24JWTOrgClaim:
    """Verify decode_token requires the 'org' claim."""

    def test_decode_token_requires_org(self):
        from app.services.auth import decode_token
        source = inspect.getsource(decode_token)
        assert '"org"' in source
        assert "R26-FIX-24" in source


# ── R26-FIX-26: Lazy init lock for token_revocation ──────────────


class TestR26Fix26LazyInitLock:
    """Verify try_lazy_init_revocation_store uses asyncio.Lock."""

    def test_lazy_init_uses_lock(self):
        from app.services.token_revocation import try_lazy_init_revocation_store
        source = inspect.getsource(try_lazy_init_revocation_store)
        assert "_get_lazy_init_lock()" in source
        assert "R26-FIX-26" in source

    def test_lazy_init_lock_getter_exists(self):
        from app.services import token_revocation
        assert hasattr(token_revocation, "_get_lazy_init_lock")


# ── R26-FIX-27: UUID validation at auth gate ─────────────────────


class TestR26Fix27UUIDValidation:
    """Verify get_current_user_context validates UUID format."""

    def test_dependencies_validate_uuid(self):
        from app.dependencies import get_current_user_context
        source = inspect.getsource(get_current_user_context)
        assert "uuid.UUID(user_id)" in source
        assert "uuid.UUID(org_id)" in source
        assert "R26-FIX-27" in source


# ── R26-FIX-28: Prompt cache key full hash ───────────────────────


class TestR26Fix28PromptCacheKey:
    """Verify prompt cache key uses full SHA-256 digest."""

    def test_cache_key_not_truncated(self):
        from app.services.prompt_engine import PromptEngine
        source = inspect.getsource(PromptEngine._compute_cache_key)
        # Must NOT truncate with [:24]
        assert "[:24]" not in source
        # Must use full hexdigest
        assert ".hexdigest()" in source
        assert "R26-FIX-28" in source


# ── R26-FIX-29: Prompt cache transactional pipeline ─────────────


class TestR26Fix29PromptCacheTransaction:
    """Verify prompt cache write uses transaction=True."""

    def test_prompt_cache_uses_transaction(self):
        from app.services.prompt_engine import PromptEngine
        source = inspect.getsource(PromptEngine.render)
        assert "transaction=True" in source
        assert "R26-FIX-29" in source


# ── R26-FIX-30: Valkey shutdown lock-protected ───────────────────


class TestR26Fix30ValkeyShutdownLock:
    """Verify shutdown_valkey_client uses the client lock."""

    def test_shutdown_uses_lock(self):
        from app.services.valkey_pool import shutdown_valkey_client
        source = inspect.getsource(shutdown_valkey_client)
        assert "_get_client_lock()" in source
        assert "R26-FIX-30" in source


# ── R26-FIX-31: list_chat_sessions user_id filter ────────────────


class TestR26Fix31ChatSessionsUserFilter:
    """Verify list_chat_sessions filters by user_id."""

    def test_list_sessions_filters_by_user(self):
        from app.routers.chat import list_chat_sessions
        source = inspect.getsource(list_chat_sessions)
        assert "ChatSession.user_id ==" in source
        assert "R26-FIX-31" in source


# ── R26-FIX-33: approve_checkpoint info leak ─────────────────────


class TestR26Fix33ApproveCheckpointInfoLeak:
    """Verify approve_checkpoint uses generic error message."""

    def test_approve_checkpoint_generic_error(self):
        from app.routers.pipeline import approve_checkpoint
        source = inspect.getsource(approve_checkpoint)
        # Must NOT pass raw str(exc) as detail
        assert 'detail=str(exc)' not in source
        assert "not in an approvable state" in source
        assert "R26-FIX-33" in source


# ── R26-FIX-34: WebSocket disconnect during auth ─────────────────


class TestR26Fix34WebSocketDisconnect:
    """Verify WebSocket auth catches WebSocketDisconnect explicitly."""

    def test_ws_auth_catches_disconnect(self):
        from app.routers.websocket import pipeline_websocket
        source = inspect.getsource(pipeline_websocket)
        assert "except WebSocketDisconnect:" in source
        assert "R26-FIX-34" in source


# ── R26-FIX-35: Celery shutdown closes Valkey pool ───────────────


class TestR26Fix35CeleryShutdownValkey:
    """Verify _shutdown_services calls shutdown_valkey_client."""

    def test_celery_shutdown_closes_valkey(self):
        from app.tasks.pipeline_tasks import _shutdown_services
        source = inspect.getsource(_shutdown_services)
        assert "shutdown_valkey_client" in source
        assert "R26-FIX-35" in source


# ── R26-FIX-36: Celery retry serializable exception ─────────────


class TestR26Fix36CeleryRetrySerializable:
    """Verify Celery retry uses serializable exception wrapper."""

    def test_retry_uses_exception_wrapper(self):
        from app.tasks.pipeline_tasks import run_pipeline_task
        source = inspect.getsource(run_pipeline_task)
        assert "Exception(safe_err" in source
        assert "R26-FIX-36" in source


# ── R26-FIX-10 (context engine): Lua cjson.encode ────────────────


class TestR26Fix10ContextLuaCjson:
    """Verify context_engine Lua uses cjson.encode instead of concatenation."""

    def test_lua_uses_cjson(self):
        from app.services.context_engine import ContextEngine
        source = inspect.getsource(ContextEngine.store)
        assert "cjson.encode" in source
        # Must NOT use string concatenation for JSON
        assert '\'{"last_hash":"\' ..' not in source
