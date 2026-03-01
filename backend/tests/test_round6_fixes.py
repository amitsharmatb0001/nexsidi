"""Tests for Round 6 fixes: hardening from brutal re-review.

Covers:
- RESUME-FIX: resume_run() O(1) direct lookup instead of O(N) scan
- WS-UUID-FIX: WebSocket run_id UUID validation
- ATTACH-FIX: Chat attachments 100 KB size limit
- TECHSTACK-FIX: ProjectCreate tech_stack bounded (50 entries)
- ENUM-FIX + RACE-FIX: Registration email enumeration + IntegrityError
- JWTSEC-FIX: JWT secret minimum raised to 32 chars
- BUCKET-FIX: ProjectRateLimiter bucket eviction
- THINK-FIX: Thinking budget minimum of 1024 tokens
- QUERY-FIX: Notification limit validated via Query parameter
"""

from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── RESUME-FIX: resume_run() O(1) direct lookup ────────────────


class TestResumeRunDirectLookup:
    """resume_run() must use find_run_by_id() (O(1)) not find_interrupted_runs() (O(N))."""

    def test_resume_run_source_uses_find_by_id(self):
        """resume_run should call find_run_by_id, not find_interrupted_runs."""
        from app.services.pipeline import PipelineOrchestrator
        import dis

        # REVIEW-FIX: resume logic moved to _resume_run_inner (per-run lock)
        source = inspect.getsource(PipelineOrchestrator._resume_run_inner)
        # The actual code must call find_run_by_id
        assert "find_run_by_id" in source

        # Verify via bytecode: _resume_run_inner should NOT load find_interrupted_runs
        bytecode = dis.Bytecode(PipelineOrchestrator._resume_run_inner)
        attr_names = [
            instr.argval for instr in bytecode
            if instr.opname in ("LOAD_ATTR", "LOAD_METHOD")
        ]
        assert "find_run_by_id" in attr_names
        assert "find_interrupted_runs" not in attr_names

    def test_load_run_metadata_uses_find_by_id(self):
        """load_run_metadata should also use direct O(1) lookup."""
        from app.services.pipeline import PipelineOrchestrator

        source = inspect.getsource(PipelineOrchestrator.load_run_metadata)
        assert "find_run_by_id" in source


# ── WS-UUID-FIX: WebSocket run_id UUID validation ──────────────


class TestWebSocketRunIdValidation:
    """WebSocket handler must validate run_id as UUID."""

    def test_ws_handler_validates_uuid(self):
        """pipeline_websocket source must contain UUID validation."""
        from app.routers.websocket import pipeline_websocket

        source = inspect.getsource(pipeline_websocket)
        assert "uuid" in source.lower() or "_uuid.UUID" in source
        assert "4004" in source  # Invalid run_id close code

    def test_ws_handler_validates_before_token_check(self):
        """UUID validation must come before token validation."""
        from app.routers.websocket import pipeline_websocket

        source = inspect.getsource(pipeline_websocket)
        uuid_pos = source.find("_uuid.UUID(run_id)")
        # PHASE-2: Token check is now "if token:" (deprecated fallback)
        token_check = source.find("if token")
        # UUID validation should appear before token check
        assert uuid_pos != -1 and token_check != -1
        assert uuid_pos < token_check


# ── ATTACH-FIX: Chat attachments 100 KB size limit ────────────


class TestChatAttachmentSizeLimit:
    """ChatMessageCreate must reject oversized attachments."""

    def test_small_attachments_accepted(self):
        from app.schemas.project import ChatMessageCreate

        msg = ChatMessageCreate(
            content="Hello",
            attachments={"file": "small data"},
        )
        assert msg.attachments is not None

    def test_no_attachments_accepted(self):
        from app.schemas.project import ChatMessageCreate

        msg = ChatMessageCreate(content="Hello")
        assert msg.attachments is None

    def test_oversized_attachments_rejected(self):
        from app.schemas.project import ChatMessageCreate
        from pydantic import ValidationError

        # Create a >100KB payload
        big_data = {"data": "x" * 200_000}
        with pytest.raises(ValidationError, match="100 KB"):
            ChatMessageCreate(content="Hello", attachments=big_data)

    def test_exactly_100kb_accepted(self):
        """Attachments at exactly 100 KB should be accepted."""
        from app.schemas.project import ChatMessageCreate
        import orjson

        # Build a payload just under the limit
        data = {"key": "a" * 100_000}
        serialized = orjson.dumps(data)
        # If already under 100KB, should work
        if len(serialized) <= 102_400:
            msg = ChatMessageCreate(content="test", attachments=data)
            assert msg.attachments is not None


# ── TECHSTACK-FIX: ProjectCreate tech_stack bounded ─────────────


class TestTechStackValidation:
    """ProjectCreate must limit tech_stack size."""

    def test_normal_tech_stack_accepted(self):
        from app.schemas.project import ProjectCreate

        project = ProjectCreate(
            name="Test Project",
            tech_stack={"language": "python", "framework": "fastapi"},
        )
        assert len(project.tech_stack) == 2

    def test_over_20_entries_rejected(self):
        from app.schemas.project import ProjectCreate
        from pydantic import ValidationError

        # R38 tightened entry limit from 50 → 20
        big_stack = {f"key_{i}": f"value_{i}" for i in range(21)}
        with pytest.raises(ValidationError, match="20"):
            ProjectCreate(name="Test", tech_stack=big_stack)

    def test_long_key_rejected(self):
        from app.schemas.project import ProjectCreate
        from pydantic import ValidationError

        # R38 tightened key limit from 100 → 50 chars
        with pytest.raises(ValidationError, match="too long"):
            ProjectCreate(
                name="Test",
                tech_stack={"a" * 51: "value"},
            )

    def test_long_value_rejected(self):
        from app.schemas.project import ProjectCreate
        from pydantic import ValidationError

        # R38 tightened value limit from 500 → 200 chars
        with pytest.raises(ValidationError, match="too long"):
            ProjectCreate(
                name="Test",
                tech_stack={"key": "v" * 201},
            )


# ── ENUM-FIX + RACE-FIX: Registration email enumeration ────────


class TestRegistrationSecurity:
    """Registration must not reveal email existence and must handle race conditions."""

    def test_register_source_has_generic_error(self):
        """Registration error message should not reveal email existence."""
        from app.routers.auth import register

        source = inspect.getsource(register)
        # The actual HTTPException detail must use generic message.
        # Check that the detail= kwarg uses generic wording.
        # (Old text "Email already registered" may appear in comments for context)
        assert 'detail="Unable to create account with this email"' in source
        # The old revealing error must NOT appear as a detail string
        assert 'detail="Email already registered"' not in source

    def test_register_source_catches_integrity_error(self):
        """Registration must handle IntegrityError from concurrent inserts."""
        from app.routers.auth import register

        source = inspect.getsource(register)
        assert "IntegrityError" in source

    def test_register_error_message_is_generic(self):
        """Both duplicate-check and race-condition paths use same generic message."""
        from app.routers.auth import register

        source = inspect.getsource(register)
        # Count occurrences of the generic message
        count = source.count("Unable to create account with this email")
        assert count >= 2  # Once for SELECT check, once for IntegrityError


# ── JWTSEC-FIX: JWT secret minimum raised to 32 chars ──────────


class TestJWTSecretMinimum:
    """JWT secret must be at least 32 characters (256 bits for HS256)."""

    def test_config_validates_32_chars(self):
        """Settings.jwt_secret_key must enforce min_length=32."""
        from app.config import Settings
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Settings(
                database_url="postgresql+asyncpg://x:x@localhost/db",
                jwt_secret_key="only-28-chars-long-for-test!",
            )

    def test_32_char_key_accepted(self):
        """A 32-char key should be accepted."""
        from app.config import Settings

        with patch.dict("os.environ", {
            "DATABASE_URL": "postgresql+asyncpg://x:x@localhost/db",
            "JWT_SECRET_KEY": "a" * 32,
        }):
            s = Settings()  # type: ignore[call-arg]
            assert len(s.jwt_secret_key) == 32

    def test_16_char_key_rejected(self):
        """16-char key should now be rejected (was previously accepted)."""
        from app.config import Settings
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Settings(
                database_url="postgresql+asyncpg://x:x@localhost/db",
                jwt_secret_key="a" * 16,
            )


# ── BUCKET-FIX: ProjectRateLimiter bucket eviction ─────────────


class TestProjectRateLimiterEviction:
    """ProjectRateLimiter must evict stale project buckets."""

    def test_has_eviction_method(self):
        from app.services.pipeline import ProjectRateLimiter

        rl = ProjectRateLimiter()
        assert hasattr(rl, "_evict_stale_buckets")
        assert hasattr(rl, "_MAX_BUCKETS")
        assert hasattr(rl, "_EVICTION_INTERVAL")

    def test_stale_buckets_evicted(self):
        """Buckets with no recent entries should be evicted."""
        from app.services.pipeline import ProjectRateLimiter

        rl = ProjectRateLimiter()
        # Inject stale entries (timestamps from 120 seconds ago)
        now = time.monotonic()
        rl._buckets["stale-project"] = [now - 120]
        rl._buckets["active-project"] = [now - 10]

        rl._evict_stale_buckets(now, 60.0)

        assert "stale-project" not in rl._buckets
        assert "active-project" in rl._buckets

    def test_hard_cap_enforced(self):
        """When over MAX_BUCKETS, oldest are evicted."""
        from app.services.pipeline import ProjectRateLimiter

        rl = ProjectRateLimiter()
        original = ProjectRateLimiter._MAX_BUCKETS
        ProjectRateLimiter._MAX_BUCKETS = 5
        try:
            now = time.monotonic()
            for i in range(10):
                rl._buckets[f"proj-{i}"] = [now - (10 - i)]

            rl._evict_stale_buckets(now, 60.0)
            assert len(rl._buckets) <= 5
        finally:
            ProjectRateLimiter._MAX_BUCKETS = original

    @pytest.mark.asyncio
    async def test_periodic_eviction_triggers(self):
        """Eviction should trigger every _EVICTION_INTERVAL acquires."""
        from app.services.pipeline import ProjectRateLimiter

        rl = ProjectRateLimiter()
        original = ProjectRateLimiter._EVICTION_INTERVAL
        ProjectRateLimiter._EVICTION_INTERVAL = 3  # Trigger after 3 acquires
        try:
            # Add a stale bucket
            rl._buckets["stale"] = [time.monotonic() - 120]

            # R25-FIX: Patch _try_valkey_acquire to return False so acquires
            # go through in-memory path (where eviction runs). Without this,
            # a locally running Valkey causes _try_valkey_acquire to return
            # True, skipping the in-memory eviction entirely.
            with patch.object(rl, "_try_valkey_acquire", return_value=False):
                # 3 acquires should trigger eviction
                for i in range(3):
                    await rl.acquire(f"active-{i}")

            # Stale bucket should have been evicted
            assert "stale" not in rl._buckets
        finally:
            ProjectRateLimiter._EVICTION_INTERVAL = original


# ── THINK-FIX: Thinking budget minimum ─────────────────────────


class TestThinkingBudgetMinimum:
    """Thinking budget must have a minimum of 1024 tokens."""

    def test_small_max_tokens_disables_thinking(self):
        """When max_tokens is too small for budget, thinking should be disabled.

        R30-FIX-2: When budget_tokens (1024) >= max_tokens (100), the
        Anthropic API would reject the request with a 400 error. Instead
        of sending an invalid request, we disable thinking entirely.
        """
        from app.services.ai_router import AIRequest, AIRouter, MODELS

        router = AIRouter.__new__(AIRouter)
        spec = MODELS["sonnet"]

        request = AIRequest(
            messages=[],
            enable_thinking=True,
            max_tokens=100,
        )
        body = router._build_anthropic_body(spec, request)

        # R30-FIX-2: budget_tokens(1024) >= max_tokens(100), so thinking
        # is disabled entirely rather than sending an invalid API request.
        assert "thinking" not in body

    def test_large_max_tokens_uses_quarter(self):
        """When max_tokens is large, budget is max_tokens // 4, capped at 10000."""
        from app.services.ai_router import AIRequest, AIRouter, MODELS

        router = AIRouter.__new__(AIRouter)
        spec = MODELS["sonnet"]

        request = AIRequest(
            messages=[],
            enable_thinking=True,
            max_tokens=32000,
        )
        body = router._build_anthropic_body(spec, request)

        # max(1024, min(10000, 32000 // 4)) = max(1024, 8000) = 8000
        assert body["thinking"]["budget_tokens"] == 8000

    def test_very_large_max_tokens_capped_at_10000(self):
        """Budget should never exceed 10000."""
        from app.services.ai_router import AIRequest, AIRouter, MODELS

        router = AIRouter.__new__(AIRouter)
        spec = MODELS["opus"]

        request = AIRequest(
            messages=[],
            enable_thinking=True,
            max_tokens=128000,
        )
        body = router._build_anthropic_body(spec, request)

        # max(1024, min(10000, 128000 // 4)) = max(1024, 10000) = 10000
        assert body["thinking"]["budget_tokens"] == 10000

    def test_no_thinking_when_model_doesnt_support(self):
        """Thinking should not be enabled for models that don't support it."""
        from app.services.ai_router import AIRequest, AIRouter, MODELS

        router = AIRouter.__new__(AIRouter)
        spec = MODELS["haiku"]  # supports_thinking=False

        request = AIRequest(
            messages=[],
            enable_thinking=True,
        )
        body = router._build_anthropic_body(spec, request)

        assert "thinking" not in body


# ── QUERY-FIX: Notification limit Query validation ─────────────


class TestNotificationLimitValidation:
    """Notification list endpoint must validate limit via Query parameter."""

    def test_limit_param_uses_query(self):
        """limit parameter should use FastAPI Query for OpenAPI validation."""
        from app.routers.notifications import list_notifications

        sig = inspect.signature(list_notifications)
        param = sig.parameters["limit"]
        # Query parameters show up with a default that is a Query object
        assert param.default is not inspect.Parameter.empty

    def test_limit_source_has_query_import(self):
        """notifications router must import Query from FastAPI."""
        import app.routers.notifications as mod

        source = inspect.getsource(mod)
        assert "from fastapi import" in source and "Query" in source

    def test_limit_source_has_ge_le_bounds(self):
        """The Query should have ge=1 and le=200 bounds."""
        import app.routers.notifications as mod

        source = inspect.getsource(mod)
        assert "ge=1" in source
        assert "le=200" in source
