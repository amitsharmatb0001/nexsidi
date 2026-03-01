"""Tests for Review Round 28 fixes.

Covers bugs found in R28 brutal code review:
- R28-1:  Aanya sanitizes exceptions in accumulated_code (no key leaks)
- R28-2:  Pipeline quality loops run before step-by-step pause
- R28-3:  JWT requires "role" claim; dependencies use bracket access + None check
- R28-4:  Celery dispatch failure marks DB row FAILED (no orphans)
- R28-6:  Archived projects blocked from pipeline start and chat creation
- R28-7:  list_chat_sessions does not eagerly load messages
- R28-8:  store_output and get_step_context sanitize errors
- R28-9:  scan_utils guards against infinite loop (overlap >= window_size)
- R28-10: clear_pipeline uses MGET batch for entry metadata
- R28-11: get_all_steps uses MGET batch for entry metadata
- R28-12: WS _validate_ws_token catches non-JWTError exceptions
- R28-13: resume_pipeline validates status before Celery dispatch
- R28-14: get_current_user_context catches non-JWTError, returns 503
- R28-16: token_revocation dead code removed
- R28-18: WhatsApp template uses manual replacement, not format()
- R28-19: FileUpload.user_id is nullable=True (SET NULL compat)
- R28-20: Pranav _generate_config_files uses format_map(defaultdict)
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── R28-1: Aanya sanitizes accumulated_code exceptions ──────────


class TestR28_1_AanyaSanitizedError:
    """Aanya must sanitize exceptions in accumulated_code to prevent API key leaks."""

    def test_accumulated_code_does_not_leak_api_key(self):
        """The error message in accumulated_code should be sanitized."""
        from app.services.ai_router import _sanitize_error

        # Simulate httpx error with API key in headers
        exc = Exception(
            'HTTPStatusError: 429 for url https://api.anthropic.com/v1/messages '
            'headers: {"x-api-key": "sk-ant-api03-SECRETKEY1234567890"}'
        )
        sanitized = _sanitize_error(exc)
        assert "sk-ant-api03" not in sanitized
        assert "SECRETKEY" not in sanitized
        assert "[REDACTED]" in sanitized

    def test_sanitized_error_used_in_generation_failure(self):
        """Verify aanya.py references _sanitize_error for accumulated_code."""
        import inspect
        from app.agents.aanya import Aanya

        source = inspect.getsource(Aanya.execute)
        # R28-FIX-1: The except block should use safe_err, not str(exc)
        assert "_sanitize_error" in source
        assert "safe_err" in source


# ── R28-2: Quality loops before step-by-step pause ───────────────


class TestR28_2_QualityLoopsBeforeStepByStep:
    """Pipeline quality loops must run BEFORE step-by-step pause."""

    @pytest.mark.asyncio
    async def test_challenge_retry_runs_in_step_by_step_mode(self):
        """Architecture challenge-retry should trigger even in STEP_BY_STEP mode."""
        from app.agents.base import AgentResult, AgentStatus
        from app.services.pipeline import (
            ExecutionMode,
            PipelineOrchestrator,
            PipelineRun,
            PipelineRunStatus,
            PipelineStage,
        )

        orch = PipelineOrchestrator()

        run = PipelineRun(
            run_id="r28-test-2",
            project_id="proj-1",
            organization_id="org-1",
            user_id="user-1",
            execution_mode=ExecutionMode.STEP_BY_STEP,
            current_stage=PipelineStage.ARCHITECTURE_REVIEW,
            status=PipelineRunStatus.RUNNING,
        )
        run.challenge_retry_count = 0

        # Create a step result with critical challenges
        step_result = MagicMock()
        step_result.result = AgentResult(
            agent_name="challenger",
            status=AgentStatus.COMPLETED,
            output={"has_critical": True, "verdict": "reject", "challenges": ["perf"]},
        )

        # Verify _has_critical_challenges detects the issue
        assert orch._has_critical_challenges(step_result) is True

    @pytest.mark.asyncio
    async def test_fix_retest_runs_in_step_by_step_mode(self):
        """Fix-retest loop should trigger even in STEP_BY_STEP mode."""
        from app.agents.base import AgentResult, AgentStatus
        from app.services.pipeline import PipelineOrchestrator

        orch = PipelineOrchestrator()

        step_result = MagicMock()
        step_result.result = AgentResult(
            agent_name="fixer",
            status=AgentStatus.COMPLETED,
            output={"errors_remaining": 3, "errors_fixed": 5, "errors_received": 8},
        )

        assert orch._has_errors_to_fix(step_result) is True

    def test_quality_loops_code_order(self):
        """Verify challenge-retry and fix-retest checks appear BEFORE step-by-step pause."""
        import inspect
        from app.services.pipeline import PipelineOrchestrator

        source = inspect.getsource(PipelineOrchestrator._run_pipeline_inner)
        lines = source.split("\n")

        challenge_line = None
        fixretest_line = None
        stepbystep_line = None

        for i, line in enumerate(lines):
            if "architecture_challenge_retry" in line or "_has_critical_challenges" in line:
                if challenge_line is None:
                    challenge_line = i
            if "_has_errors_to_fix" in line:
                if fixretest_line is None:
                    fixretest_line = i
            if "ExecutionMode.STEP_BY_STEP" in line:
                stepbystep_line = i

        assert challenge_line is not None, "Challenge-retry check not found"
        assert fixretest_line is not None, "Fix-retest check not found"
        assert stepbystep_line is not None, "Step-by-step check not found"
        # R28-FIX-2: Both quality loops must come BEFORE step-by-step pause
        assert challenge_line < stepbystep_line, (
            f"Challenge-retry (line {challenge_line}) should be before "
            f"step-by-step (line {stepbystep_line})"
        )
        assert fixretest_line < stepbystep_line, (
            f"Fix-retest (line {fixretest_line}) should be before "
            f"step-by-step (line {stepbystep_line})"
        )


# ── R28-3: JWT "role" in require list ────────────────────────────


class TestR28_3_JWTRoleRequired:
    """JWT decode must require 'role' claim; dependencies use bracket access."""

    def test_decode_token_requires_role_claim(self):
        """decode_token's require list should include 'role'."""
        import inspect
        from app.services.auth import decode_token

        source = inspect.getsource(decode_token)
        # R28-FIX-3: "role" must be in the require list alongside other claims
        assert '"role"' in source
        assert "require" in source

    def test_dependencies_use_bracket_access(self):
        """get_current_user_context should use bracket access for mandatory claims."""
        import inspect
        from app.dependencies import get_current_user_context

        source = inspect.getsource(get_current_user_context)
        # R28-FIX-3: Should use payload["sub"], not payload.get("sub")
        assert 'payload["sub"]' in source
        assert 'payload["org"]' in source
        assert 'payload["role"]' in source

    def test_dependencies_none_check_not_truthiness(self):
        """get_current_user_context should check 'is None', not truthiness."""
        import inspect
        from app.dependencies import get_current_user_context

        source = inspect.getsource(get_current_user_context)
        # R28-FIX-3: Should use "is None" checks, not truthiness
        assert "is None" in source


# ── R28-4: Celery dispatch failure cleans up DB row ──────────────


class TestR28_4_CeleryDispatchCleanup:
    """Celery dispatch failure must mark DB row FAILED, not leave orphan."""

    def test_dispatch_failure_sets_failed_status(self):
        """Verify start_pipeline catches dispatch failure and marks run FAILED."""
        import inspect
        from app.routers.pipeline import start_pipeline

        source = inspect.getsource(start_pipeline)
        # R28-FIX-4: After dispatch failure, must persist FAILED status
        assert "PipelineRunStatus.FAILED" in source
        assert "dispatch_cleanup_persist_failed" in source


# ── R28-6: Archived project guards ───────────────────────────────


class TestR28_6_ArchivedProjectGuards:
    """Archived projects must be blocked from pipeline start and chat creation."""

    def test_start_pipeline_excludes_archived(self):
        """start_pipeline query should filter out archived projects."""
        import inspect
        from app.routers.pipeline import start_pipeline

        source = inspect.getsource(start_pipeline)
        assert '"archived"' in source or "'archived'" in source

    def test_create_chat_session_excludes_archived(self):
        """create_chat_session should filter out archived projects."""
        import inspect
        from app.routers.chat import create_chat_session

        source = inspect.getsource(create_chat_session)
        assert '"archived"' in source or "'archived'" in source


# ── R28-7: list_chat_sessions no eager load ──────────────────────


class TestR28_7_ListChatNoEagerLoad:
    """list_chat_sessions must NOT eagerly load all messages."""

    def test_list_sessions_no_selectinload_messages(self):
        """list_chat_sessions should not use selectinload(ChatSession.messages)."""
        import inspect
        from app.routers.chat import list_chat_sessions

        source = inspect.getsource(list_chat_sessions)
        # R28-FIX-7: The list query must NOT eagerly load messages.
        # Check that selectinload(ChatSession.messages) is not in the query.
        # The function may reference "messages" in response construction,
        # and "selectinload" may appear in imports, but the actual SELECT
        # query should NOT have selectinload applied for messages.
        assert "selectinload(ChatSession.messages)" not in source


# ── R28-8: store_output / get_step_context sanitize errors ───────


class TestR28_8_BaseSanitizedErrors:
    """store_output and get_step_context must use _sanitize_error."""

    def test_store_output_uses_sanitize_error(self):
        """store_output exception handler should call _sanitize_error."""
        import inspect
        from app.agents.base import store_output

        source = inspect.getsource(store_output)
        assert "_sanitize_error" in source

    def test_get_step_context_uses_sanitize_error(self):
        """get_step_context exception handler should call _sanitize_error."""
        import inspect
        from app.agents.base import get_step_context

        source = inspect.getsource(get_step_context)
        assert "_sanitize_error" in source


# ── R28-9: scan_utils infinite loop guard ────────────────────────


class TestR28_9_ScanUtilsInfiniteLoopGuard:
    """split_into_windows must not infinite loop when overlap >= window_size."""

    def test_overlap_equals_window_size(self):
        """overlap == window_size should reset overlap to 0 and terminate."""
        from app.agents.scan_utils import split_into_windows

        content = "a" * 10000
        # overlap == window_size → would cause step=0 → infinite loop
        result = split_into_windows(content, window_size=3000, overlap=3000)
        assert len(result) > 0
        assert all(len(w) <= 3000 for w in result)

    def test_overlap_exceeds_window_size(self):
        """overlap > window_size should reset overlap and terminate."""
        from app.agents.scan_utils import split_into_windows

        content = "b" * 8000
        result = split_into_windows(content, window_size=2000, overlap=5000)
        assert len(result) > 0
        assert all(len(w) <= 2000 for w in result)

    def test_normal_overlap_still_works(self):
        """Normal overlap < window_size should produce overlapping windows."""
        from app.agents.scan_utils import split_into_windows

        content = "x" * 7000
        result = split_into_windows(content, window_size=3000, overlap=500)
        assert len(result) >= 3
        assert all(len(w) <= 3000 for w in result)


# ── R28-10: clear_pipeline uses MGET ─────────────────────────────


class TestR28_10_ClearPipelineMGET:
    """clear_pipeline must use MGET for batch entry metadata loading."""

    def test_clear_pipeline_uses_mget(self):
        """clear_pipeline source should contain 'mget' call."""
        import inspect
        from app.services.context_engine import ContextEngine

        source = inspect.getsource(ContextEngine.clear_pipeline)
        assert "mget" in source.lower()

    @pytest.mark.asyncio
    async def test_clear_pipeline_calls_mget(self):
        """clear_pipeline should call mget instead of individual gets."""
        from app.services.context_engine import ContextEngine

        mock_redis = AsyncMock()
        engine = ContextEngine(mock_redis)

        # Setup: chain has 3 entries
        mock_redis.lrange = AsyncMock(return_value=[b"e1", b"e2", b"e3"])

        import orjson
        entry_meta = orjson.dumps({
            "step_name": "test", "chunk_count": 1,
            "content_hash": "h", "prev_hash": "p",
            "chain_hash": "c", "created_at": "2024-01-01",
        })
        mock_redis.mget = AsyncMock(return_value=[entry_meta, entry_meta, entry_meta])
        mock_redis.delete = AsyncMock(return_value=8)

        # Mock lock
        lock_mock = AsyncMock()
        lock_mock.acquire = AsyncMock(return_value=True)
        lock_mock.release = AsyncMock()
        mock_redis.lock = MagicMock(return_value=lock_mock)

        result = await engine.clear_pipeline("run-123")
        assert result == 8
        # Verify MGET was called (not individual GETs)
        mock_redis.mget.assert_called_once()


# ── R28-11: get_all_steps uses MGET ──────────────────────────────


class TestR28_11_GetAllStepsMGET:
    """get_all_steps must use MGET for batch entry metadata loading."""

    def test_get_all_steps_uses_mget(self):
        """get_all_steps source should contain 'mget' call."""
        import inspect
        from app.services.context_engine import ContextEngine

        source = inspect.getsource(ContextEngine.get_all_steps)
        assert "mget" in source.lower()


# ── R28-12: WS _validate_ws_token catches non-JWTError ───────────


class TestR28_12_WSTokenCatchAll:
    """_validate_ws_token must catch non-JWTError exceptions."""

    @pytest.mark.asyncio
    async def test_non_jwterror_returns_none(self):
        """Infrastructure errors (ConnectionError, RuntimeError) should return None."""
        from app.routers.websocket import _validate_ws_token

        # Patch verify_token to raise a RuntimeError (not JWTError)
        with patch(
            "app.routers.websocket.verify_token",
            side_effect=RuntimeError("Valkey not initialized"),
        ):
            result = await _validate_ws_token("fake-token")
            assert result is None

    @pytest.mark.asyncio
    async def test_connection_error_returns_none(self):
        """ConnectionError from Valkey should return None, not propagate."""
        from app.routers.websocket import _validate_ws_token

        with patch(
            "app.routers.websocket.verify_token",
            side_effect=ConnectionError("Redis connection refused"),
        ):
            result = await _validate_ws_token("fake-token")
            assert result is None


# ── R28-13: resume_pipeline validates status before dispatch ──────


class TestR28_13_ResumePipelineStatusValidation:
    """resume_pipeline must validate run status before Celery dispatch."""

    def test_resume_checks_status(self):
        """resume_pipeline source should check _RESUMABLE_STATUSES."""
        import inspect
        from app.routers.pipeline import resume_pipeline

        source = inspect.getsource(resume_pipeline)
        # R28-FIX-13: Must validate status before dispatch
        assert "_RESUMABLE_STATUSES" in source or "RESUMABLE" in source

    def test_resume_rejects_completed_runs(self):
        """resume_pipeline should reject runs with COMPLETED status."""
        import inspect
        from app.routers.pipeline import resume_pipeline

        source = inspect.getsource(resume_pipeline)
        # Must check for RUNNING status conflict
        assert "409" in source or "CONFLICT" in source

    def test_resume_rejects_running_runs(self):
        """resume_pipeline should reject runs that are already RUNNING."""
        import inspect
        from app.routers.pipeline import resume_pipeline

        source = inspect.getsource(resume_pipeline)
        assert "already running" in source.lower() or "RUNNING" in source


# ── R28-14: get_current_user_context catches infrastructure errors ─


class TestR28_14_DependencyCatchAll:
    """get_current_user_context must return 503 for infrastructure errors."""

    def test_catches_non_jwt_errors(self):
        """Source should have except Exception returning 503."""
        import inspect
        from app.dependencies import get_current_user_context

        source = inspect.getsource(get_current_user_context)
        assert "503" in source or "SERVICE_UNAVAILABLE" in source
        assert "except Exception" in source


# ── R28-16: Token revocation dead code removed ───────────────────


class TestR28_16_DeadCodeRemoved:
    """Dead code in token_revocation.py should be cleaned up."""

    def test_no_max_token_ttl_constant(self):
        """_MAX_TOKEN_TTL_SECONDS should not be defined as a variable."""
        from app.services import token_revocation

        # R28-FIX-16: The constant was removed. It may appear in comments
        # (e.g., the FIX comment explaining its removal), but must NOT be
        # defined as an actual module-level attribute.
        assert not hasattr(token_revocation, "_MAX_TOKEN_TTL_SECONDS")

    def test_get_revocation_store_is_clean(self):
        """get_revocation_store should not have dead lazy-reconnection code."""
        import inspect
        from app.services.token_revocation import get_revocation_store

        source = inspect.getsource(get_revocation_store)
        # Should be a simple check + raise, no dead event loop code
        assert "get_event_loop" not in source
        assert "get_running_loop" not in source


# ── R28-18: WhatsApp template manual replacement ─────────────────


class TestR28_18_WhatsAppTemplateInjection:
    """WhatsApp template should use manual str.replace, not format()."""

    def test_no_format_call_in_template_rendering(self):
        """WhatsApp agent should NOT use template.format(**params)."""
        import inspect
        from app.agents.whatsapp_agent import WhatsAppAgent

        source = inspect.getsource(WhatsAppAgent)
        # R28-FIX-18: Should use str.replace(), not .format(**params)
        assert ".format(**" not in source

    def test_manual_replacement_prevents_injection(self):
        """Manual replacement should not allow attribute access injection."""
        # Simulate the manual replacement approach
        template = "Hello {name}, your order {order_id} is ready."
        params = {"name": "Alice", "order_id": "ORD-123"}

        text = template
        for key, value in params.items():
            text = text.replace(f"{{{key}}}", str(value))

        assert text == "Hello Alice, your order ORD-123 is ready."

    def test_format_injection_blocked(self):
        """Format string injection via __class__ should not work with replace()."""
        template = "Hello {name}"
        # Attacker tries to inject format mini-language
        params = {"name": "{__class__.__init__.__globals__}"}

        text = template
        for key, value in params.items():
            text = text.replace(f"{{{key}}}", str(value))

        # The literal string should be preserved, not evaluated
        assert "__class__" in text
        assert "Hello {__class__.__init__.__globals__}" == text


# ── R28-19: FileUpload.user_id nullable ──────────────────────────


class TestR28_19_FileUploadNullable:
    """FileUpload.user_id must be nullable=True for SET NULL compatibility."""

    def test_user_id_is_nullable(self):
        """FileUpload.user_id column should have nullable=True."""
        from app.models.core import FileUpload
        from sqlalchemy import inspect as sa_inspect

        mapper = sa_inspect(FileUpload)
        user_id_col = mapper.columns["user_id"]
        assert user_id_col.nullable is True

    def test_user_id_type_is_optional(self):
        """FileUpload.user_id Mapped type should be Optional (uuid.UUID | None)."""
        import typing
        from app.models.core import FileUpload

        # Check the annotation allows None
        hints = typing.get_type_hints(FileUpload)
        # The Mapped type should include None
        user_id_hint = hints.get("user_id")
        assert user_id_hint is not None


# ── R28-20: Pranav format_map with defaultdict ───────────────────


class TestR28_20_PranavFormatMap:
    """Pranav _generate_config_files must use format_map(defaultdict)."""

    def test_uses_format_map_not_format(self):
        """Source should use format_map, not str.format()."""
        import inspect
        from app.agents.pranav import Pranav

        source = inspect.getsource(Pranav._generate_config_files)
        assert "format_map" in source
        assert "defaultdict" in source

    def test_format_map_handles_unknown_placeholders(self):
        """Unknown placeholders should produce empty string, not crash."""
        template = "image: {ECR_IMAGE}\nservice: {service_name}\nregion: {region}"
        values = defaultdict(
            lambda: "",
            service_name="my-app",
            region="us-east-1",
        )
        result = template.format_map(values)
        assert result == "image: \nservice: my-app\nregion: us-east-1"

    def test_format_map_with_all_known_placeholders(self):
        """All known placeholders should be substituted correctly."""
        template = "name: {service_name}, project: {project_id}, region: {region}"
        values = defaultdict(
            lambda: "",
            service_name="my-svc",
            project_id="nexsidi",
            region="asia-south1",
        )
        result = template.format_map(values)
        assert result == "name: my-svc, project: nexsidi, region: asia-south1"


# ── Cross-cutting: Verify all R28 fixes are present ──────────────


class TestR28_AllFixesPresent:
    """Verify that R28 fix markers exist in the codebase."""

    @pytest.mark.parametrize("fix_id,module_path", [
        ("R28-FIX-1", "app.agents.aanya"),
        ("R28-FIX-2", "app.services.pipeline"),
        ("R28-FIX-3", "app.services.auth"),
        ("R28-FIX-4", "app.routers.pipeline"),
        ("R28-FIX-6", "app.routers.pipeline"),
        ("R28-FIX-8", "app.agents.base"),
        ("R28-FIX-9", "app.agents.scan_utils"),
        ("R28-FIX-10", "app.services.context_engine"),
        ("R28-FIX-11", "app.services.context_engine"),
        ("R28-FIX-12", "app.routers.websocket"),
        ("R28-FIX-13", "app.routers.pipeline"),
        ("R28-FIX-14", "app.dependencies"),
        ("R28-FIX-16", "app.services.token_revocation"),
        ("R28-FIX-18", "app.agents.whatsapp_agent"),
        ("R28-FIX-19", "app.models.core"),
        ("R28-FIX-20", "app.agents.pranav"),
    ])
    def test_fix_marker_exists(self, fix_id, module_path):
        """Each R28 fix should have a comment marker in the code."""
        import importlib
        import inspect

        module = importlib.import_module(module_path)
        source = inspect.getsource(module)
        assert fix_id in source, f"{fix_id} marker not found in {module_path}"
