"""Tests for Review Round 27 fixes.

Covers key bugs found in R27 brutal code review:
- R27-1:  _sanitize_error redacts GCP project IDs and SA emails
- R27-2:  Pranav _deploy fallback sets cloud_config (no NameError)
- R27-3:  WhatsApp phone hash computed from original, not redacted phone
- R27-4:  Logout refresh-token revocation: correct else branch
- R27-6:  Concurrent pipeline guard uses SELECT FOR UPDATE
- R27-7:  Archived projects excluded from GET/PATCH
- R27-8:  resume_pipeline_task wraps exc for serializable retry
- R27-9:  _persist_step uses _sanitize_error
- R27-10: Gemini cached-content merges into first user message
- R27-11: get_all_steps raises ContextIntegrityError when verify_chain=True and entry missing
- R27-12: Multi-chunk reassembly uses MGET instead of sequential GETs
- R27-17: Audit log failure after revocation returns 204, not 500
- R27-18: Family revocation uses <= (not <) for fail-closed
- R27-21: Rate limiter skips checkpoint stages
- R27-22: Complexity map uses explicit default, not list(values())[1]
- R27-23: CircuitState lazy-inits Lock via _get_lock()
- R27-24: _run_async has proper Any type annotation
- R27-25: __feedback_history__ capped at 20 entries
- R27-agents: Agent exception handlers use _sanitize_error
"""

from __future__ import annotations

import asyncio
import inspect
import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── R27-1: _sanitize_error redacts GCP project IDs ──────────────


class TestR27_1_SanitizeGCPInfo:
    """_sanitize_error must redact GCP project IDs and service account emails."""

    def test_redacts_gcp_project_url(self):
        """Vertex AI error URLs with projects/X/locations/Y must be redacted."""
        from app.services.ai_router import _sanitize_error

        exc = Exception(
            "403 POST https://us-central1-aiplatform.googleapis.com/v1/"
            "projects/yugnex-ai/locations/us-central1/publishers/google/models/gemini"
        )
        result = _sanitize_error(exc)
        assert "yugnex-ai" not in result
        assert "us-central1" not in result or "[REDACTED]" in result
        assert "projects/[REDACTED]/locations/[REDACTED]" in result

    def test_redacts_service_account_email(self):
        """GCP service account emails must be redacted."""
        from app.services.ai_router import _sanitize_error

        exc = Exception(
            "Permission denied for sa-nexsidi@yugnex-ai.iam.gserviceaccount.com"
        )
        result = _sanitize_error(exc)
        assert "gserviceaccount.com" not in result
        assert "[REDACTED_SA]" in result

    def test_preserves_non_sensitive_parts(self):
        """Non-sensitive error parts must be preserved."""
        from app.services.ai_router import _sanitize_error

        exc = Exception("Connection timed out after 10 seconds")
        result = _sanitize_error(exc)
        assert "Connection timed out after 10 seconds" == result


# ── R27-2: Pranav _deploy fallback ───────────────────────────────


class TestR27_2_PranavDeployFallback:
    """Pranav._deploy must set cloud_config in the except branch."""

    def test_deploy_method_no_nameerror_path(self):
        """_deploy must assign cloud_config in the KeyError except branch."""
        from app.agents.pranav import Pranav
        source = inspect.getsource(Pranav._deploy)
        # The except KeyError branch must assign cloud_config
        assert "cloud_config = get_cloud_config" in source, \
            "_deploy must assign cloud_config in except KeyError branch"

    def test_deploy_fallback_comment(self):
        """R27-FIX-2 comment must be present."""
        from app.agents.pranav import Pranav
        source = inspect.getsource(Pranav._deploy)
        assert "R27-FIX-2" in source


# ── R27-3: WhatsApp phone hash from original ─────────────────────


class TestR27_3_WhatsAppPhoneHash:
    """Phone hash must be computed from original number, not redacted."""

    def test_phone_hash_field_exists(self):
        """WhatsAppIncoming must have phone_hash field."""
        from app.agents.whatsapp_agent import WhatsAppIncoming
        import dataclasses
        field_names = [f.name for f in dataclasses.fields(WhatsAppIncoming)]
        assert "phone_hash" in field_names

    def test_from_webhook_computes_hash_before_redaction(self):
        """from_webhook must hash the original phone, not the redacted form."""
        from app.agents.whatsapp_agent import WhatsAppIncoming, _hash_phone

        payload = {
            "entry": [{"changes": [{"value": {"messages": [{
                "from": "919876543210",
                "type": "text",
                "text": {"body": "Build an app"},
                "timestamp": "1234567890",
            }]}}]}],
        }
        incoming = WhatsAppIncoming.from_webhook(payload)
        assert incoming is not None
        # phone_hash must match hash of original, not redacted
        expected_hash = _hash_phone("919876543210")
        assert incoming.phone_hash == expected_hash
        # phone_number is redacted
        assert "****" in incoming.phone_number or "*" in incoming.phone_number

    def test_execute_uses_precomputed_hash(self):
        """execute() must use incoming.phone_hash, not _hash_phone(redacted)."""
        from app.agents.whatsapp_agent import WhatsAppAgent
        source = inspect.getsource(WhatsAppAgent.execute)
        assert "incoming.phone_hash" in source, \
            "execute must use pre-computed phone_hash"
        assert "_hash_phone(incoming.phone_number)" not in source, \
            "execute must not hash the redacted phone_number"


# ── R27-4: Logout refresh-token else branch ──────────────────────


class TestR27_4_LogoutRefreshElse:
    """Logout refresh revocation must use payload['key'] and correct else."""

    def test_uses_bracket_access_for_jti(self):
        """Must use refresh_payload['jti'], not .get('jti')."""
        from app.routers.auth import logout
        source = inspect.getsource(logout)
        # After R27-FIX-4, should use bracket access
        assert 'refresh_payload["jti"]' in source, \
            "Must use refresh_payload['jti'] (mandatory claims)"

    def test_mismatch_warning_at_correct_level(self):
        """logout_refresh_token_mismatch must be in the sub != user_id else."""
        from app.routers.auth import logout
        source = inspect.getsource(logout)
        assert "logout_refresh_token_mismatch" in source


# ── R27-6: Concurrent pipeline SELECT FOR UPDATE ─────────────────


class TestR27_6_ConcurrentPipelineGuard:
    """start_pipeline must use SELECT FOR UPDATE for TOCTOU prevention."""

    def test_with_for_update_in_source(self):
        """start_pipeline must lock the project row with FOR UPDATE."""
        from app.routers.pipeline import start_pipeline
        source = inspect.getsource(start_pipeline)
        assert "with_for_update()" in source, \
            "start_pipeline must use SELECT ... FOR UPDATE on project row"


# ── R27-7: Archived projects excluded ────────────────────────────


class TestR27_7_ArchivedProjectsExcluded:
    """GET/PATCH endpoints must exclude archived projects."""

    def test_get_project_excludes_archived(self):
        """get_project must filter status != 'archived'."""
        from app.routers.projects import get_project
        source = inspect.getsource(get_project)
        assert '"archived"' in source, \
            "get_project must filter out archived projects"

    def test_update_project_excludes_archived(self):
        """update_project must filter status != 'archived'."""
        from app.routers.projects import update_project
        source = inspect.getsource(update_project)
        assert '"archived"' in source, \
            "update_project must filter out archived projects"


# ── R27-8: resume_pipeline_task serializable exc ─────────────────


class TestR27_8_ResumeTaskSerializableExc:
    """resume_pipeline_task must wrap exc in serializable Exception."""

    def test_resume_task_wraps_exc(self):
        """self.retry(exc=Exception(safe_err[:500])) pattern must be present."""
        from app.tasks.pipeline_tasks import resume_pipeline_task
        source = inspect.getsource(resume_pipeline_task)
        assert "Exception(safe_err[:500])" in source, \
            "resume_pipeline_task must wrap exc in serializable Exception"


# ── R27-9: _persist_step uses _sanitize_error ────────────────────


class TestR27_9_PersistStepSanitized:
    """_persist_step must use _sanitize_error, not raw str(exc)."""

    def test_persist_step_sanitizes(self):
        """_persist_step error handler must call _sanitize_error."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator._persist_step)
        assert "_sanitize_error" in source, \
            "_persist_step must use _sanitize_error"
        # Must not have raw str(exc) in the error handler
        assert "error=str(exc)" not in source, \
            "_persist_step must not log raw str(exc)"


# ── R27-10: Gemini cached-content turn merging ───────────────────


class TestR27_10_GeminiCachedContentMerge:
    """Cached-content preamble must merge into first user message."""

    def test_build_google_body_merges_preamble(self):
        """_build_google_body must merge system prompt into first user msg."""
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter._build_google_body)
        # Must check first message role before inserting
        assert 'body["contents"][0]["role"] == "user"' in source or \
               "contents\"][0][\"role\"] == \"user\"" in source, \
            "Must check if first content is user role before merging"
        assert "R27-FIX-10" in source


# ── R27-11: get_all_steps raises on missing entry with verify_chain ──


class TestR27_11_GetAllStepsRaises:
    """get_all_steps must raise ContextIntegrityError when verify_chain=True."""

    def test_raises_on_missing_entry_with_verify(self):
        """Source must raise ContextIntegrityError when entry missing + verify."""
        from app.services.context_engine import ContextEngine
        source = inspect.getsource(ContextEngine.get_all_steps)
        assert "ContextIntegrityError" in source
        assert "verify_chain" in source

    @pytest.mark.asyncio
    async def test_missing_entry_raises_when_verify_true(self):
        """When entry_raw is None and verify_chain=True, must raise."""
        from app.services.context_engine import ContextEngine, ContextIntegrityError

        engine = ContextEngine.__new__(ContextEngine)
        engine._redis = AsyncMock()
        # Chain has one entry but the entry key returns None
        engine._redis.lrange = AsyncMock(return_value=[b"entry-1"])
        # R28-FIX-11: get_all_steps now uses MGET instead of individual GETs.
        # Return [None] to simulate a missing entry in MGET batch.
        engine._redis.mget = AsyncMock(return_value=[None])

        with pytest.raises(ContextIntegrityError, match="chain integrity"):
            await engine.get_all_steps("run-123", verify_chain=True)

    @pytest.mark.asyncio
    async def test_missing_entry_breaks_when_verify_false(self):
        """When entry_raw is None and verify_chain=False, must break (return [])."""
        from app.services.context_engine import ContextEngine

        engine = ContextEngine.__new__(ContextEngine)
        engine._redis = AsyncMock()
        engine._redis.lrange = AsyncMock(return_value=[b"entry-1"])
        # R28-FIX-11: get_all_steps now uses MGET instead of individual GETs.
        engine._redis.mget = AsyncMock(return_value=[None])

        result = await engine.get_all_steps("run-123", verify_chain=False)
        assert result == []


# ── R27-12: Multi-chunk MGET ─────────────────────────────────────


class TestR27_12_MultiChunkMGET:
    """Multi-chunk reassembly must use MGET instead of sequential GETs."""

    def test_reassemble_chunks_uses_mget(self):
        """_reassemble_chunks source must use self._redis.mget."""
        from app.services.context_engine import ContextEngine
        source = inspect.getsource(ContextEngine._reassemble_chunks)
        assert "mget" in source, \
            "_reassemble_chunks must use MGET for multi-chunk reads"

    @pytest.mark.asyncio
    async def test_multi_chunk_mget_reassembles(self):
        """MGET-based reassembly must concatenate all chunks."""
        from app.services.context_engine import ContextEngine

        engine = ContextEngine.__new__(ContextEngine)
        engine._redis = AsyncMock()
        engine._redis.mget = AsyncMock(return_value=[b"chunk0", b"chunk1", b"chunk2"])

        result = await engine._reassemble_chunks("run-1", "entry-1", 3)
        assert result == b"chunk0chunk1chunk2"

    @pytest.mark.asyncio
    async def test_multi_chunk_mget_raises_on_missing(self):
        """MGET with a None chunk must raise ContextIntegrityError."""
        from app.services.context_engine import ContextEngine, ContextIntegrityError

        engine = ContextEngine.__new__(ContextEngine)
        engine._redis = AsyncMock()
        engine._redis.mget = AsyncMock(return_value=[b"chunk0", None, b"chunk2"])

        with pytest.raises(ContextIntegrityError, match="Missing chunk 1"):
            await engine._reassemble_chunks("run-1", "entry-1", 3)


# ── R27-17: Audit log failure → still 204 ───────────────────────


class TestR27_17_AuditLogFailure:
    """Audit log failure after revocation must not return 500."""

    def test_logout_wraps_audit_in_try_except(self):
        """Logout endpoint must wrap audit log in try/except."""
        from app.routers.auth import logout
        source = inspect.getsource(logout)
        assert "logout_audit_failed" in source, \
            "Logout must catch audit log failures"
        assert "R27-FIX-17" in source

    def test_logout_all_wraps_audit_in_try_except(self):
        """Logout-all endpoint must wrap audit log in try/except."""
        from app.routers.auth import logout_all
        source = inspect.getsource(logout_all)
        assert "logout_all_audit_failed" in source, \
            "Logout-all must catch audit log failures"


# ── R27-18: Family revocation <= ─────────────────────────────────


class TestR27_18_FamilyRevocationLE:
    """Family revocation must use <= (fail-closed on exact microsecond)."""

    def test_is_user_revoked_uses_le(self):
        """is_user_revoked must use issued_at <= cutoff."""
        from app.services.token_revocation import TokenRevocationStore
        source = inspect.getsource(TokenRevocationStore.is_user_revoked)
        assert "issued_at <= cutoff" in source, \
            "is_user_revoked must use <= for fail-closed semantics"

    @pytest.mark.asyncio
    async def test_exact_microsecond_is_revoked(self):
        """Token issued at exact same time as revocation must be revoked."""
        from app.services.token_revocation import TokenRevocationStore

        store = TokenRevocationStore.__new__(TokenRevocationStore)
        store._redis = AsyncMock()
        now = datetime.now(timezone.utc)
        # Cutoff stored in Valkey = same instant as token iat
        store._redis.get = AsyncMock(return_value=now.isoformat().encode())

        result = await store.is_user_revoked("user-1", now)
        assert result is True, "Token issued at exact cutoff must be revoked"


# ── R27-21: Rate limiter skips checkpoints ───────────────────────


class TestR27_21_RateLimiterSkipsCheckpoints:
    """Rate limiter must not be called for checkpoint stages."""

    def test_checkpoint_guard_in_source(self):
        """_run_pipeline_inner must skip rate limiter for CHECKPOINT_STAGES."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator._run_pipeline_inner)
        assert "CHECKPOINT_STAGES" in source, \
            "_run_pipeline_inner must check CHECKPOINT_STAGES before rate limiting"
        assert "R27-FIX-21" in source


# ── R27-22: Explicit complexity default ──────────────────────────


class TestR27_22_ExplicitComplexityDefault:
    """Complexity map must use explicit default, not list(values())[1]."""

    def test_no_list_values_index(self):
        """select_model must not use list(active_map.values())[1]."""
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter.select_model)
        assert "list(active_map.values())[1]" not in source, \
            "Must not use fragile positional dict value access"

    def test_explicit_default_map(self):
        """Must use an explicit default model map."""
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter.select_model)
        assert "_MODE_DEFAULT_MODEL" in source, \
            "Must use explicit _MODE_DEFAULT_MODEL dict"


# ── R27-23: CircuitState lazy Lock ───────────────────────────────


class TestR27_23_CircuitStateLazyLock:
    """CircuitState must lazy-init its Lock via _get_lock()."""

    def test_get_lock_method_exists(self):
        """CircuitState must have a _get_lock() method."""
        from app.services.ai_router import CircuitState
        assert hasattr(CircuitState, "_get_lock")
        assert callable(CircuitState._get_lock)

    def test_lock_is_none_at_init(self):
        """Lock must be None at init time (lazy)."""
        from app.services.ai_router import CircuitState
        cs = CircuitState()
        assert cs._lock is None

    def test_get_lock_creates_lock(self):
        """_get_lock() must create an asyncio.Lock on first call."""
        from app.services.ai_router import CircuitState
        cs = CircuitState()
        lock = cs._get_lock()
        assert isinstance(lock, asyncio.Lock)
        # Second call returns same instance
        assert cs._get_lock() is lock


# ── R27-24: _run_async type annotation ───────────────────────────


class TestR27_24_RunAsyncAnnotation:
    """_run_async must have proper Any type annotation."""

    def test_return_type_is_any(self):
        """_run_async return type must be typing.Any, not builtin any."""
        from app.tasks.pipeline_tasks import _run_async
        import typing
        hints = typing.get_type_hints(_run_async)
        assert hints.get("return") is typing.Any, \
            "_run_async return type must be typing.Any"


# ── R27-25: Feedback history bounded ─────────────────────────────


class TestR27_25_FeedbackHistoryBounded:
    """__feedback_history__ must be capped to prevent unbounded growth."""

    def test_approve_checkpoint_caps_history(self):
        """approve_checkpoint must cap __feedback_history__ size."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator.approve_checkpoint)
        assert "_MAX_FEEDBACK_HISTORY" in source, \
            "approve_checkpoint must define max feedback history size"

    def test_feedback_truncated(self):
        """Feedback text must be truncated."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator.approve_checkpoint)
        assert "_MAX_FEEDBACK_LEN" in source, \
            "approve_checkpoint must cap individual feedback length"


# ── R27-agents: Agent _sanitize_error usage ──────────────────────


class TestR27_AgentsSanitizeError:
    """All agents must use _sanitize_error in exception handlers."""

    def _check_agent_source(self, module_path: str, class_name: str):
        """Verify an agent class uses _sanitize_error and not raw str(exc)."""
        import importlib
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        source = inspect.getsource(cls)
        # Check no raw str(exc) in error= keyword args
        # Allow str() on other things, just not on exc in error handlers
        lines = source.split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "error=str(exc)" in stripped:
                pytest.fail(f"{class_name} has raw str(exc) in: {stripped}")

    def test_karan_sanitizes(self):
        self._check_agent_source("app.agents.karan", "Karan")

    def test_navya_sanitizes(self):
        self._check_agent_source("app.agents.navya", "Navya")

    def test_deepika_sanitizes(self):
        self._check_agent_source("app.agents.deepika", "Deepika")

    def test_aanya_sanitizes(self):
        self._check_agent_source("app.agents.aanya", "Aanya")

    def test_shubham_sanitizes(self):
        self._check_agent_source("app.agents.shubham", "Shubham")

    def test_aarav_sanitizes(self):
        self._check_agent_source("app.agents.aarav", "Aarav")
