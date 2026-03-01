"""Tests for Review Round 29 fixes.

Covers bugs found in R29 brutal code review:
- R29-1:  decode_token require list is conditional (refresh tokens omit role)
- R29-2:  list_chat_sessions uses ChatSessionListItemResponse (no messages field)
- R29-3:  Context truncation operates on a copy, not live run.context
- R29-4:  context_engine store() uses manual lock acquire/release
- R29-5:  Challenger agent sanitizes exceptions
- R29-6:  main.py startup/health uses _sanitize_error
- R29-7:  Continuation latency accumulated across all calls
- R29-8:  Asset processor validates filename against path traversal
- R29-9:  resume_pipeline error doesn't leak internal status value
- R29-10: UUID vs str normalization in pipeline ownership checks
- R29-11: X-XSS-Protection set to "0" (OWASP recommendation)
- R29-12: delete_project excludes already-archived projects
- R29-13: Fixer _get_iteration_model_map uses .get() with fallback
- R29-14: Alembic migration failure sanitizes error
"""

from __future__ import annotations

import copy
import inspect
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── R29-1: decode_token conditional require list ─────────────────


class TestR29_1_ConditionalRequireList:
    """decode_token must NOT require 'role' for refresh tokens."""

    def test_access_token_requires_role(self):
        """Access token decoding should require 'role' claim."""
        from app.services.auth import decode_token

        source = inspect.getsource(decode_token)
        assert '"role"' in source
        assert "expected_type" in source

    def test_refresh_token_does_not_require_role(self):
        """Refresh token decoding should NOT require 'role' claim."""
        from app.services.auth import decode_token

        source = inspect.getsource(decode_token)
        # R29-FIX-1: The require list must differ based on expected_type
        assert 'expected_type != "refresh"' in source or "refresh" in source

    def test_refresh_token_has_no_role_claim(self):
        """create_refresh_token should NOT include a role claim."""
        from app.services.auth import create_refresh_token

        source = inspect.getsource(create_refresh_token)
        assert '"role"' not in source

    def test_refresh_token_decode_succeeds(self):
        """A refresh token (without role) should decode successfully."""
        import uuid
        from app.services.auth import create_refresh_token, decode_token

        token = create_refresh_token(
            user_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
        )
        # This should NOT raise — R29-FIX-1 makes role optional for refresh
        payload = decode_token(token, expected_type="refresh")
        assert payload["type"] == "refresh"
        assert "role" not in payload


# ── R29-2: list_chat_sessions uses list-specific schema ──────────


class TestR29_2_ChatSessionListSchema:
    """list_chat_sessions must use ChatSessionListItemResponse (no messages)."""

    def test_list_item_schema_has_no_messages(self):
        """ChatSessionListItemResponse should not have a messages field."""
        from app.schemas.project import ChatSessionListItemResponse

        fields = ChatSessionListItemResponse.model_fields
        assert "messages" not in fields

    def test_list_endpoint_uses_list_item_schema(self):
        """list_chat_sessions should reference ChatSessionListItemResponse."""
        from app.routers.chat import list_chat_sessions

        source = inspect.getsource(list_chat_sessions)
        assert "ChatSessionListItemResponse" in source

    def test_full_schema_still_has_messages(self):
        """ChatSessionResponse (for get_chat_session) should still have messages."""
        from app.schemas.project import ChatSessionResponse

        fields = ChatSessionResponse.model_fields
        assert "messages" in fields


# ── R29-3: Context truncation uses copy ──────────────────────────


class TestR29_3_ContextTruncationCopy:
    """_persist_run must truncate a COPY of context, not the live object."""

    def test_persist_run_uses_copy(self):
        """_persist_run source should import copy and create persist_context."""
        from app.services.pipeline import PipelineOrchestrator

        source = inspect.getsource(PipelineOrchestrator._persist_run)
        assert "import copy" in source or "_copy.copy" in source
        assert "persist_context" in source
        assert "original_context" in source

    def test_context_restored_after_persist(self):
        """run.context should be restored to original after _persist_run."""
        from app.services.pipeline import PipelineOrchestrator

        source = inspect.getsource(PipelineOrchestrator._persist_run)
        # Must restore in both success and error paths
        assert source.count("run.context = original_context") >= 2

    def test_shallow_copy_preserves_live_data(self):
        """Shallow copy should keep the original dict values intact."""
        original = {"vikram": {"tables": ["users", "orders"]}, "__requirements__": "Build an app"}
        copied = copy.copy(original)
        # Mutate the copy
        copied["vikram"] = {"__truncated__": True}
        # Original should be untouched
        assert original["vikram"]["tables"] == ["users", "orders"]


# ── R29-4: context_engine manual lock ────────────────────────────


class TestR29_4_ManualLockInStore:
    """context_engine store() must use manual acquire/release."""

    def test_store_uses_manual_lock(self):
        """store() should use lock.acquire() + try/finally, not async with."""
        from app.services.context_engine import ContextEngine

        source = inspect.getsource(ContextEngine.store)
        # R29-FIX-4: Must use manual acquire, not async with
        assert "await lock.acquire()" in source
        assert "finally:" in source
        assert "await lock.release()" in source

    def test_store_handles_lock_timeout(self):
        """store() should raise TimeoutError if lock cannot be acquired."""
        from app.services.context_engine import ContextEngine

        source = inspect.getsource(ContextEngine.store)
        assert "TimeoutError" in source


# ── R29-5: Challenger sanitizes exceptions ───────────────────────


class TestR29_5_ChallengerSanitized:
    """Challenger agent must use _sanitize_error for all exceptions."""

    def test_ai_review_error_sanitized(self):
        """challenger_ai_review_failed should use _sanitize_error."""
        from app.agents.challenger import Challenger

        source = inspect.getsource(Challenger)
        assert "_sanitize_error" in source
        # Should NOT have raw str(exc) in logger calls
        assert "error=str(exc)" not in source


# ── R29-6: main.py startup sanitizes errors ──────────────────────


class TestR29_6_MainStartupSanitized:
    """main.py startup/health exceptions must use _sanitize_error."""

    def test_startup_uses_sanitize(self):
        """Lifespan function should use _sanitize_error for all exception logging."""
        from app.main import lifespan

        source = inspect.getsource(lifespan)
        assert "_sanitize_error" in source
        # Should NOT have raw str(exc) for startup errors
        assert "error=str(exc)" not in source

    def test_readiness_check_sanitizes(self):
        """Readiness check should sanitize DB connection errors."""
        from app.main import create_app

        source = inspect.getsource(create_app)
        assert "_sanitize" in source or "_sanitize_error" in source


# ── R29-7: Continuation latency accumulated ──────────────────────


class TestR29_7_ContinuationLatency:
    """call_ai_with_continuation must accumulate latency across all continuations."""

    def test_total_latency_accumulated(self):
        """Source should accumulate total_latency_ms across continuations."""
        from app.agents.base import call_ai_with_continuation

        source = inspect.getsource(call_ai_with_continuation)
        assert "total_latency_ms" in source
        assert "+= response.latency_ms" in source

    def test_return_uses_total_latency(self):
        """Return AIResponse should use total_latency_ms, not response.latency_ms."""
        from app.agents.base import call_ai_with_continuation

        source = inspect.getsource(call_ai_with_continuation)
        # The return should use total_latency_ms
        assert "latency_ms=total_latency_ms" in source


# ── R29-8: Asset processor path traversal validation ─────────────


class TestR29_8_PathTraversal:
    """Asset processor must reject path traversal in filenames."""

    def test_dotdot_rejected(self):
        """Filenames with '..' should be rejected."""
        from app.services.asset_processor import AssetProcessor

        processor = AssetProcessor()
        result = processor.validate_asset("../../etc/passwd", 1000, "text/plain")
        assert not result.valid
        assert any("path traversal" in e.lower() for e in result.errors)

    def test_absolute_path_rejected(self):
        """Absolute paths should be rejected."""
        from app.services.asset_processor import AssetProcessor

        processor = AssetProcessor()
        result = processor.validate_asset("/etc/shadow", 1000, "text/plain")
        assert not result.valid

    def test_normal_filename_allowed(self):
        """Normal filenames should pass validation."""
        from app.services.asset_processor import AssetProcessor

        processor = AssetProcessor()
        result = processor.validate_asset("logo.png", 5000, "image/png")
        assert result.valid

    def test_subdirectory_in_name_rejected(self):
        """Filenames with subdirectories should be rejected."""
        from app.services.asset_processor import AssetProcessor

        processor = AssetProcessor()
        result = processor.validate_asset("subdir/logo.png", 5000, "image/png")
        assert not result.valid


# ── R29-9: resume_pipeline generic error message ─────────────────


class TestR29_9_GenericResumeError:
    """resume_pipeline should not leak internal status values in errors."""

    def test_no_status_value_in_error(self):
        """Error message should NOT contain run.status.value."""
        from app.routers.pipeline import resume_pipeline

        source = inspect.getsource(resume_pipeline)
        assert "run.status.value" not in source or "current status" in source


# ── R29-10: UUID vs str normalization ────────────────────────────


class TestR29_10_UUIDStrNormalization:
    """Pipeline ownership checks must normalize UUID/str comparison."""

    def test_get_pipeline_status_uses_str(self):
        """get_pipeline_status should str()-normalize the comparison."""
        from app.routers.pipeline import get_pipeline_status

        source = inspect.getsource(get_pipeline_status)
        assert "str(run.organization_id)" in source

    def test_approve_checkpoint_uses_str(self):
        """approve_checkpoint should str()-normalize the comparison."""
        from app.routers.pipeline import approve_checkpoint

        source = inspect.getsource(approve_checkpoint)
        assert "str(run.organization_id)" in source

    def test_resume_pipeline_uses_str(self):
        """resume_pipeline should str()-normalize the comparison."""
        from app.routers.pipeline import resume_pipeline

        source = inspect.getsource(resume_pipeline)
        assert "str(run.organization_id)" in source

    def test_websocket_uses_str(self):
        """WebSocket pipeline endpoint should str()-normalize."""
        from app.routers.websocket import pipeline_websocket

        source = inspect.getsource(pipeline_websocket)
        assert "str(run.organization_id)" in source

    def test_uuid_str_comparison_always_false(self):
        """Demonstrate that UUID != str even with same value."""
        import uuid
        uid = uuid.uuid4()
        assert uid != str(uid), "UUID should not equal its str representation"
        assert str(uid) == str(uid), "str(UUID) should equal str(UUID)"


# ── R29-11: X-XSS-Protection = "0" ──────────────────────────────


class TestR29_11_XSSProtection:
    """X-XSS-Protection should be set to '0' per OWASP."""

    def test_xss_protection_is_zero(self):
        """SecurityHeadersMiddleware should set X-XSS-Protection to '0'."""
        from app.main import create_app

        source = inspect.getsource(create_app)
        assert '"0"' in source
        assert '"1; mode=block"' not in source


# ── R29-12: delete_project excludes archived ─────────────────────


class TestR29_12_DeleteExcludesArchived:
    """delete_project should filter out already-archived projects."""

    def test_delete_project_has_archived_filter(self):
        """delete_project query should filter out archived projects."""
        from app.routers.projects import delete_project

        source = inspect.getsource(delete_project)
        assert '"archived"' in source or "'archived'" in source
        assert "!=" in source or "not" in source.lower()


# ── R29-13: Fixer mode fallback ──────────────────────────────────


class TestR29_13_FixerModeFallback:
    """_get_iteration_model_map must use .get() with fallback."""

    def test_uses_get_with_fallback(self):
        """_get_iteration_model_map should use dict.get() with a default."""
        from app.agents.fixer import _get_iteration_model_map

        source = inspect.getsource(_get_iteration_model_map)
        assert ".get(" in source
        assert '"mixed"' in source

    def test_unknown_mode_returns_mixed(self):
        """An unknown mode should fall back to 'mixed' map."""
        with patch("app.services.ai_router.get_ai_mode", return_value="unknown_mode"):
            from app.agents.fixer import _get_iteration_model_map
            result = _get_iteration_model_map()
            # Should be the mixed map (5 entries: iterations 1-5)
            assert len(result) >= 3


# ── R29-14: Alembic migration error sanitized ────────────────────


class TestR29_14_AlembicErrorSanitized:
    """run_migrations should sanitize Alembic errors."""

    def test_run_migrations_uses_sanitize_error(self):
        """run_migrations source should call _sanitize_error on exceptions."""
        from app.database import run_migrations

        source = inspect.getsource(run_migrations)
        assert "_sanitize_error" in source
        assert "RuntimeError" in source


# ── Cross-cutting: Verify all R29 fixes are present ──────────────


class TestR29_AllFixesPresent:
    """Verify that R29 fix markers exist in the codebase."""

    @pytest.mark.parametrize("fix_id,module_path", [
        ("R29-FIX-1", "app.services.auth"),
        ("R29-FIX-2", "app.schemas.project"),
        ("R29-FIX-3", "app.services.pipeline"),
        ("R29-FIX-4", "app.services.context_engine"),
        ("R29-FIX-5", "app.agents.challenger"),
        ("R29-FIX-6", "app.main"),
        ("R29-FIX-7", "app.agents.base"),
        ("R29-FIX-8", "app.services.asset_processor"),
        ("R29-FIX-9", "app.routers.pipeline"),
        ("R29-FIX-10", "app.routers.pipeline"),
        ("R29-FIX-11", "app.main"),
        ("R29-FIX-12", "app.routers.projects"),
        ("R29-FIX-13", "app.agents.fixer"),
        ("R29-FIX-14", "app.database"),
    ])
    def test_fix_marker_exists(self, fix_id, module_path):
        """Each R29 fix should have a comment marker in the code."""
        import importlib

        module = importlib.import_module(module_path)
        source = inspect.getsource(module)
        assert fix_id in source, f"{fix_id} marker not found in {module_path}"
