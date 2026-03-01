"""Tests for Review Round 25 fixes.

Covers 13 bugs found in R25 brutal code review:
- R25-1:  Valkey rate limiter _try_valkey_acquire returns False when denied
- R25-2:  GeminiCacheManager uses _sanitize_error in error handler
- R25-3:  Token revocation store lazy re-initialization on startup blip
- R25-4:  clear_pipeline acquires distributed lock before clearing
- R25-5:  Sliding window ensures message alternation for Anthropic
- R25-6:  call_count uses incremental counter, not deque len
- R25-7:  __feedback_history__ et al. in _METADATA_KEYS
- R25-8:  FallbackRevocationStore.revoke() returns True in dev
- R25-9:  Chat session endpoints verify user ownership
- R25-10: _pre_auth_semaphore lazy-initialized via getter
- R25-11: get_valkey_client protected by asyncio.Lock
- R25-12: Single-chunk miss raises ContextIntegrityError
- R25-13: require_admin reuses TenantSession (no double DB session)
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── R25-1: Valkey rate limiter bypass ─────────────────────────────


class TestR25_1_ValkeyRateLimiter:
    """_try_valkey_acquire must return False when still rate-limited after retry."""

    def test_try_valkey_acquire_returns_false_after_retry_denied(self):
        """Source must show return False after second eval still returns 1."""
        from app.services.pipeline import ProjectRateLimiter
        source = inspect.getsource(ProjectRateLimiter._try_valkey_acquire)
        # After retry, if result == 1, must return False (not True)
        assert "return False" in source, \
            "_try_valkey_acquire must return False when retry still denied"

    def test_try_valkey_acquire_retries_with_sleep(self):
        """Rate limiter must sleep before retrying."""
        from app.services.pipeline import ProjectRateLimiter
        source = inspect.getsource(ProjectRateLimiter._try_valkey_acquire)
        assert "asyncio.sleep" in source, \
            "_try_valkey_acquire must sleep before retry"

    @pytest.mark.asyncio
    async def test_try_valkey_acquire_returns_false_on_persistent_limit(self):
        """When Lua returns 1 twice, _try_valkey_acquire must return False."""
        from app.services.pipeline import ProjectRateLimiter

        limiter = ProjectRateLimiter.__new__(ProjectRateLimiter)
        limiter._calls_per_minute = 10

        mock_redis = AsyncMock()
        # Lua returns 1 (rate limited) both times
        mock_redis.eval = AsyncMock(return_value=1)

        mock_store = MagicMock()
        mock_store._redis = mock_redis

        # Patch at the location the local import resolves from
        with patch("app.services.token_revocation.get_revocation_store", return_value=mock_store), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await limiter._try_valkey_acquire("proj-123")

        assert result is False, "Must return False when rate-limited after retry"

    @pytest.mark.asyncio
    async def test_try_valkey_acquire_returns_true_on_success(self):
        """When Lua returns 0, _try_valkey_acquire must return True."""
        from app.services.pipeline import ProjectRateLimiter

        limiter = ProjectRateLimiter.__new__(ProjectRateLimiter)
        limiter._calls_per_minute = 10

        mock_redis = AsyncMock()
        # Lua returns 0 (acquired)
        mock_redis.eval = AsyncMock(return_value=0)

        mock_store = MagicMock()
        mock_store._redis = mock_redis

        with patch("app.services.token_revocation.get_revocation_store", return_value=mock_store):
            result = await limiter._try_valkey_acquire("proj-123")

        assert result is True, "Must return True when rate limit acquired"


# ── R25-2: GeminiCacheManager _sanitize_error ─────────────────────


class TestR25_2_GeminiCacheSanitize:
    """GeminiCacheManager error handler must use _sanitize_error, not str(exc)."""

    def test_gemini_cache_uses_sanitize_error(self):
        """The GeminiCacheManager must call _sanitize_error in its error handler."""
        from app.services.ai_router import GeminiCacheManager
        source = inspect.getsource(GeminiCacheManager)
        # Must use _sanitize_error, not raw str(exc) in error logging
        assert "_sanitize_error" in source, \
            "GeminiCacheManager must use _sanitize_error in error handler"

    def test_gemini_cache_no_raw_str_exc_in_log(self):
        """GeminiCacheManager error handler must not log raw str(exc)."""
        from app.services.ai_router import GeminiCacheManager
        source = inspect.getsource(GeminiCacheManager)
        lines = source.split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "logger.warning" in stripped and "str(exc)" in stripped:
                pytest.fail(f"GeminiCacheManager logs raw str(exc): {stripped}")


# ── R25-3: Token revocation lazy re-initialization ────────────────


class TestR25_3_RevocationLazyInit:
    """Token revocation store must support lazy re-initialization."""

    def test_try_lazy_init_function_exists(self):
        """try_lazy_init_revocation_store must exist."""
        from app.services.token_revocation import try_lazy_init_revocation_store
        assert asyncio.iscoroutinefunction(try_lazy_init_revocation_store)

    @pytest.mark.asyncio
    async def test_lazy_init_succeeds_when_valkey_available(self):
        """Lazy init must succeed when Valkey is reachable."""
        from app.services import token_revocation

        # Reset state
        original_store = token_revocation._store
        token_revocation._store = None

        try:
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock(return_value=True)

            # get_valkey_client is imported locally inside try_lazy_init, patch at source
            with patch("app.services.valkey_pool.get_valkey_client",
                       new_callable=AsyncMock, return_value=mock_client):
                result = await token_revocation.try_lazy_init_revocation_store()

            assert result is True
            assert token_revocation._store is not None
        finally:
            token_revocation._store = original_store

    @pytest.mark.asyncio
    async def test_lazy_init_returns_false_when_valkey_down(self):
        """Lazy init must return False when Valkey is unreachable."""
        from app.services import token_revocation

        original_store = token_revocation._store
        token_revocation._store = None

        try:
            with patch("app.services.valkey_pool.get_valkey_client",
                       new_callable=AsyncMock,
                       side_effect=ConnectionError("Valkey down")):
                result = await token_revocation.try_lazy_init_revocation_store()

            assert result is False
            assert token_revocation._store is None
        finally:
            token_revocation._store = original_store

    def test_verify_token_handles_runtime_error_with_lazy_init(self):
        """verify_token must attempt lazy re-init when store is None."""
        from app.services.auth import verify_token
        source = inspect.getsource(verify_token)
        assert "try_lazy_init_revocation_store" in source, \
            "verify_token must attempt lazy re-init on RuntimeError"


# ── R25-4: clear_pipeline distributed lock ────────────────────────


class TestR25_4_ClearPipelineLock:
    """clear_pipeline must acquire distributed lock before clearing.

    R26-FIX-3 updated clear_pipeline from manual SET NX + DELETE to
    self._redis.lock() (same as store()), ensuring compatible locking.
    Tests updated to match the new mechanism.
    """

    def test_clear_pipeline_acquires_lock(self):
        """clear_pipeline source must acquire a distributed lock via self._redis.lock()."""
        from app.services.context_engine import ContextEngine
        source = inspect.getsource(ContextEngine.clear_pipeline)
        # R26-FIX-3: Now uses self._redis.lock() instead of manual SET NX
        assert "self._redis.lock(" in source, \
            "clear_pipeline must acquire lock with self._redis.lock()"

    def test_clear_pipeline_releases_lock_in_finally(self):
        """clear_pipeline must release lock in finally block."""
        from app.services.context_engine import ContextEngine
        source = inspect.getsource(ContextEngine.clear_pipeline)
        assert "finally:" in source, \
            "clear_pipeline must have finally block for lock release"

    def test_clear_pipeline_raises_on_lock_timeout(self):
        """clear_pipeline must raise TimeoutError if lock cannot be acquired."""
        from app.services.context_engine import ContextEngine
        source = inspect.getsource(ContextEngine.clear_pipeline)
        # R26-FIX-8: Must raise TimeoutError instead of proceeding without lock
        assert "TimeoutError" in source, \
            "clear_pipeline must raise TimeoutError on lock acquisition failure"


# ── R25-5: Sliding window message alternation ─────────────────────


class TestR25_5_SlidingWindowAlternation:
    """Sliding window must ensure Anthropic-compliant message alternation."""

    def test_sliding_window_drops_leading_user_message(self):
        """If tail starts with 'user', it must be dropped for alternation."""
        from app.agents.base import call_ai_with_tools
        source = inspect.getsource(call_ai_with_tools)
        # Must check first message role and drop if "user"
        assert 'tail[0].role == "user"' in source, \
            "Sliding window must check if tail starts with user message"

    def test_sliding_window_has_max_history(self):
        """Sliding window must define _MAX_HISTORY_MESSAGES."""
        from app.agents.base import call_ai_with_tools
        source = inspect.getsource(call_ai_with_tools)
        assert "_MAX_HISTORY_MESSAGES" in source


# ── R25-6: call_count incremental counter ─────────────────────────


class TestR25_6_IncrementalCallCount:
    """ProjectCostTracker.call_count must use incremental counter."""

    def test_total_call_count_attribute_exists(self):
        """ProjectCostTracker must have _total_call_count attribute."""
        from app.services.ai_router import ProjectCostTracker
        tracker = ProjectCostTracker(pipeline_run_id="test")
        assert hasattr(tracker, "_total_call_count")
        assert tracker._total_call_count == 0

    def test_call_count_returns_incremental_counter(self):
        """call_count property must return _total_call_count, not len(deque)."""
        from app.services.ai_router import ProjectCostTracker
        source = inspect.getsource(ProjectCostTracker.call_count.fget)
        assert "_total_call_count" in source, \
            "call_count must use _total_call_count, not len(deque)"

    def test_call_count_survives_deque_eviction(self):
        """call_count must remain accurate after deque entries are evicted."""
        from app.services.ai_router import AIResponse, ProjectCostTracker, Provider
        tracker = ProjectCostTracker(pipeline_run_id="test")

        # Record more entries than deque maxlen using AIResponse
        total_to_record = tracker._MAX_ENTRIES + 100
        for i in range(total_to_record):
            response = AIResponse(
                content="test",
                model_used="claude-sonnet-4-20250514",
                provider=Provider.ANTHROPIC,
                input_tokens=10,
                output_tokens=5,
            )
            tracker.record(response, agent_name="test-agent", model_key="sonnet")

        # call_count should be total entries recorded, not capped at deque maxlen
        assert tracker.call_count == total_to_record
        # But deque is capped
        assert len(tracker._entries) == tracker._MAX_ENTRIES


# ── R25-7: _METADATA_KEYS completeness ───────────────────────────


class TestR25_7_MetadataKeys:
    """_METADATA_KEYS must include all metadata keys used by pipeline.

    _METADATA_KEYS is a local variable inside _persist_run(), so we
    verify via source inspection rather than direct import.
    """

    def _get_metadata_keys_source(self) -> str:
        """Get the source of the method containing _METADATA_KEYS."""
        from app.services.pipeline import PipelineOrchestrator
        # _METADATA_KEYS is local to _persist_run or _truncate_context
        # Search across the whole class source
        return inspect.getsource(PipelineOrchestrator)

    def test_feedback_history_in_metadata_keys(self):
        """__feedback_history__ must be in _METADATA_KEYS."""
        source = self._get_metadata_keys_source()
        assert '"__feedback_history__"' in source, \
            "__feedback_history__ must be in _METADATA_KEYS"

    def test_user_feedback_in_metadata_keys(self):
        """__user_feedback__ must be in _METADATA_KEYS."""
        source = self._get_metadata_keys_source()
        assert '"__user_feedback__"' in source, \
            "__user_feedback__ must be in _METADATA_KEYS"

    def test_requirements_in_metadata_keys(self):
        """__requirements__ must be in _METADATA_KEYS."""
        source = self._get_metadata_keys_source()
        assert '"__requirements__"' in source, \
            "__requirements__ must be in _METADATA_KEYS"

    def test_all_original_keys_still_present(self):
        """All original metadata keys must still be in _METADATA_KEYS."""
        source = self._get_metadata_keys_source()
        expected_originals = [
            "__fix_retest_cycle__",
            "__challenge_retry_count__",
            "__persist_failures__",
            "__last_step__",
            "__checkpoint_approved__",
            "__architecture_challenges__",
            "__paused_at__",
        ]
        for key in expected_originals:
            assert f'"{key}"' in source, \
                f"Missing original key {key} in _METADATA_KEYS"


# ── R25-8: FallbackRevocationStore.revoke() returns True ──────────


class TestR25_8_FallbackStoreRevoke:
    """FallbackRevocationStore.revoke() must return True for dev /refresh."""

    @pytest.mark.asyncio
    async def test_fallback_store_revoke_returns_true(self):
        """Dev fallback revoke() must return True so /refresh works."""
        from app.services.token_revocation import _FallbackRevocationStore
        store = _FallbackRevocationStore()
        result = await store.revoke("test-jti", datetime.now(timezone.utc))
        assert result is True, \
            "FallbackRevocationStore.revoke() must return True in dev"

    @pytest.mark.asyncio
    async def test_fallback_store_is_revoked_fails_closed_on_empty_jti(self):
        """Fallback is_revoked must return True for empty jti (fail-closed)."""
        from app.services.token_revocation import _FallbackRevocationStore
        store = _FallbackRevocationStore()
        assert await store.is_revoked("") is True

    @pytest.mark.asyncio
    async def test_fallback_store_is_user_revoked_fails_closed_on_empty(self):
        """Fallback is_user_revoked must return True for empty user_id."""
        from app.services.token_revocation import _FallbackRevocationStore
        store = _FallbackRevocationStore()
        assert await store.is_user_revoked("", datetime.now(timezone.utc)) is True


# ── R25-9: Chat session user ownership ────────────────────────────


class TestR25_9_ChatSessionOwnership:
    """Chat session endpoints must verify user ownership."""

    def test_send_message_checks_user_id(self):
        """send_message must filter by user_id."""
        from app.routers.chat import send_message
        source = inspect.getsource(send_message)
        assert "ctx.user_id" in source, \
            "send_message must check user ownership"
        assert "ChatSession.user_id" in source, \
            "send_message must filter by ChatSession.user_id"

    def test_get_messages_checks_user_id(self):
        """get_messages must filter by user_id."""
        from app.routers.chat import get_messages
        source = inspect.getsource(get_messages)
        assert "ctx.user_id" in source, \
            "get_messages must check user ownership"

    def test_get_chat_session_checks_user_id(self):
        """get_chat_session must filter by user_id."""
        from app.routers.chat import get_chat_session
        source = inspect.getsource(get_chat_session)
        assert "ctx.user_id" in source, \
            "get_chat_session must check user ownership"


# ── R25-10: _pre_auth_semaphore lazy init ─────────────────────────


class TestR25_10_PreAuthSemaphoreLazy:
    """WebSocket _pre_auth_semaphore must be lazy-initialized."""

    def test_getter_function_exists(self):
        """_get_pre_auth_semaphore getter must exist."""
        from app.routers.websocket import _get_pre_auth_semaphore
        assert callable(_get_pre_auth_semaphore)

    def test_module_level_semaphore_is_none_initially(self):
        """Module-level _pre_auth_semaphore should be None before first use."""
        import app.routers.websocket as ws_mod
        # Reset to test lazy init
        original = ws_mod._pre_auth_semaphore
        ws_mod._pre_auth_semaphore = None
        try:
            sem = ws_mod._get_pre_auth_semaphore()
            assert isinstance(sem, asyncio.Semaphore)
            # Calling again returns the same instance
            assert ws_mod._get_pre_auth_semaphore() is sem
        finally:
            ws_mod._pre_auth_semaphore = original

    def test_websocket_handler_uses_getter(self):
        """pipeline_websocket must use _get_pre_auth_semaphore(), not bare global."""
        from app.routers.websocket import pipeline_websocket
        source = inspect.getsource(pipeline_websocket)
        assert "_get_pre_auth_semaphore()" in source, \
            "WebSocket handler must use getter, not bare _pre_auth_semaphore"


# ── R25-11: get_valkey_client asyncio.Lock ────────────────────────


class TestR25_11_ValkeyClientLock:
    """get_valkey_client must use asyncio.Lock to prevent duplicate pool."""

    def test_lock_in_source(self):
        """get_valkey_client must use asyncio.Lock for thread safety."""
        from app.services.valkey_pool import get_valkey_client
        source = inspect.getsource(get_valkey_client)
        assert "_get_client_lock()" in source, \
            "get_valkey_client must use _get_client_lock()"

    def test_double_check_after_lock(self):
        """Must double-check _client after acquiring lock."""
        from app.services.valkey_pool import get_valkey_client
        source = inspect.getsource(get_valkey_client)
        # Must have second check inside the lock
        assert source.count("_client is not None") >= 2, \
            "Must double-check _client after acquiring lock"

    @pytest.mark.asyncio
    async def test_concurrent_init_creates_single_pool(self):
        """Concurrent calls must only create one connection pool."""
        import app.services.valkey_pool as pool_mod

        original_client = pool_mod._client
        original_lock = pool_mod._client_lock
        pool_mod._client = None
        pool_mod._client_lock = None

        creation_count = 0
        mock_instance = MagicMock()

        original_from_url = None

        def mock_from_url(*args, **kwargs):
            nonlocal creation_count
            creation_count += 1
            return mock_instance

        try:
            # redis.asyncio.from_url is a sync function (returns client),
            # imported inside get_valkey_client. We need to mock the module.
            import redis.asyncio as aioredis
            original_from_url = aioredis.from_url

            aioredis.from_url = mock_from_url

            with patch("app.services.valkey_pool.get_settings") as mock_settings:
                mock_settings.return_value = MagicMock(
                    valkey_url="redis://localhost:6379",
                    valkey_password=None,
                )
                # Launch multiple concurrent calls
                results = await asyncio.gather(
                    pool_mod.get_valkey_client(),
                    pool_mod.get_valkey_client(),
                    pool_mod.get_valkey_client(),
                )

            # All should return the same instance
            assert results[0] is results[1] is results[2]
            # Only one pool should be created
            assert creation_count == 1, \
                f"Expected 1 pool creation, got {creation_count}"
        finally:
            pool_mod._client = original_client
            pool_mod._client_lock = original_lock
            if original_from_url is not None:
                import redis.asyncio as aioredis
                aioredis.from_url = original_from_url

    def test_reset_clears_lock(self):
        """reset_valkey_client must also reset the lock."""
        import app.services.valkey_pool as pool_mod
        pool_mod._client_lock = asyncio.Lock()
        pool_mod.reset_valkey_client()
        assert pool_mod._client_lock is None


# ── R25-12: Single-chunk miss raises ContextIntegrityError ────────


class TestR25_12_SingleChunkMiss:
    """Single-chunk miss must raise ContextIntegrityError, not return b''."""

    def test_reassemble_chunks_source_raises_on_none(self):
        """_reassemble_chunks must raise ContextIntegrityError when single chunk is None."""
        from app.services.context_engine import ContextEngine
        source = inspect.getsource(ContextEngine._reassemble_chunks)
        # Must raise ContextIntegrityError
        assert "ContextIntegrityError" in source, \
            "_reassemble_chunks must raise ContextIntegrityError on missing chunk"
        # Old code had `return data if data else b""` — must not exist in single-chunk path
        # Extract single-chunk branch (between chunk_count == 1 and the multi-chunk section)
        single_chunk_section = source.split("chunk_count == 1")[1].split("parts:")[0]
        assert 'return data if data else b""' not in single_chunk_section, \
            "_reassemble_chunks must not return b'' for missing single chunk"

    @pytest.mark.asyncio
    async def test_single_chunk_none_raises_error(self):
        """When single chunk GET returns None, must raise ContextIntegrityError."""
        from app.services.context_engine import ContextEngine, ContextIntegrityError

        engine = ContextEngine.__new__(ContextEngine)
        engine._redis = AsyncMock()
        engine._redis.get = AsyncMock(return_value=None)

        with pytest.raises(ContextIntegrityError, match="Missing chunk 0"):
            await engine._reassemble_chunks("run-123", "entry-456", 1)

    @pytest.mark.asyncio
    async def test_single_chunk_present_returns_data(self):
        """When single chunk exists, must return its data."""
        from app.services.context_engine import ContextEngine

        engine = ContextEngine.__new__(ContextEngine)
        engine._redis = AsyncMock()
        engine._redis.get = AsyncMock(return_value=b"hello-data")

        result = await engine._reassemble_chunks("run-123", "entry-456", 1)
        assert result == b"hello-data"

    @pytest.mark.asyncio
    async def test_multi_chunk_none_also_raises(self):
        """Multi-chunk miss must also raise ContextIntegrityError.

        R27-FIX-12 changed multi-chunk from sequential GETs to MGET.
        Test updated to mock mget instead of get side_effect.
        """
        from app.services.context_engine import ContextEngine, ContextIntegrityError

        engine = ContextEngine.__new__(ContextEngine)
        engine._redis = AsyncMock()
        # R27-FIX-12: Now uses mget, so mock returns list with None
        engine._redis.mget = AsyncMock(return_value=[b"chunk0", None])

        with pytest.raises(ContextIntegrityError, match="Missing chunk 1"):
            await engine._reassemble_chunks("run-123", "entry-456", 2)


# ── R25-13: require_admin reuses TenantSession ───────────────────


class TestR25_13_RequireAdminSession:
    """require_admin must have JWT fast-reject before DB session.

    R25-FIX-13: require_admin opens its internal session ONLY after the
    JWT fast-reject check passes. This ensures non-admin users get 403
    without touching the DB, even when the DB is down. The internal
    session is short-lived (one SELECT + close).
    """

    def test_require_admin_fast_rejects_before_db(self):
        """require_admin must check JWT role BEFORE opening DB session."""
        from app.dependencies import require_admin
        source = inspect.getsource(require_admin)
        # The fast-reject check must appear BEFORE get_session_factory()
        fast_reject_pos = source.find("org_admin")
        factory_pos = source.find("get_session_factory")
        assert fast_reject_pos < factory_pos, \
            "JWT fast-reject must come before DB session creation"

    def test_require_admin_internal_session_is_short_lived(self):
        """require_admin's DB session must be enclosed in async with."""
        from app.dependencies import require_admin
        source = inspect.getsource(require_admin)
        assert "async with factory()" in source, \
            "Internal session must be properly scoped with async with"

    def test_require_admin_does_not_depend_on_tenant_session(self):
        """require_admin must NOT depend on get_tenant_session (breaks fast-reject)."""
        from app.dependencies import require_admin
        sig = inspect.signature(require_admin)
        param_names = list(sig.parameters.keys())
        # Should NOT have 'session' parameter (which would be a Depends(get_tenant_session))
        # because FastAPI resolves ALL dependencies before function body runs,
        # which would break the fast-reject path if DB is down.
        assert "session" not in param_names, \
            "require_admin must not depend on get_tenant_session"

    def test_admin_context_type_alias(self):
        """AdminContext type alias must still work."""
        from app.dependencies import AdminContext
        assert AdminContext is not None
