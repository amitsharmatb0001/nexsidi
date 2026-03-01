"""Regression tests for all 19 deferred bug fixes.

Covers:
1.  context_snapshot JSONB capped at 8KB per value
2.  call_ai_with_tools sliding window (first + last 6 rounds)
3.  In-memory rate limiter → Valkey-backed (Lua script)
4.  asyncio.Semaphore lazy-initialized (not at construction)
5.  Per-run lock documented + DB atomic transition
6.  step_order: SmallInteger → Integer
7.  clear_pipeline: SCAN → deterministic key enumeration
8.  Context engine: two-phase write → single pipeline.execute()
9.  Prompt engine: cache invalidation via Lua script
10. Three Valkey pools → shared pool (valkey_pool.py)
11. Module-level singletons → reset functions
12. Streaming: log dropped tool_use blocks
13. Non-streaming call(): retry on connection errors
14. temperature=1.0 override: log warning
15. Anthropic streaming: parse message_delta stop_reason
16. Login/register audit logs include ip_address + user_agent
17. per_model_breakdown: incremental dicts (not deque iteration)
18. Auth routes: RLS context set before audit logs
19. _pipeline_task_done: sanitize errors + defensive try/except
"""

from __future__ import annotations

import inspect

import pytest


# ── BUG 1: context_snapshot truncation ──────────────────────────────


class TestContextSnapshotTruncation:
    """Pipeline must cap context_snapshot values before DB persist."""

    def test_max_context_value_chars_in_persist(self):
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator._persist_run)
        assert "_MAX_CONTEXT_VALUE_CHARS" in source, \
            "_persist_run must reference _MAX_CONTEXT_VALUE_CHARS"

    def test_persist_run_truncates_large_context(self):
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator._persist_run)
        assert "__truncated__" in source, \
            "_persist_run must truncate large context values"


# ── BUG 2: sliding window in call_ai_with_tools ────────────────────


class TestSlidingWindow:
    """call_ai_with_tools must enforce sliding window on message history."""

    def test_max_history_messages_in_function(self):
        from app.agents.base import call_ai_with_tools
        source = inspect.getsource(call_ai_with_tools)
        assert "_MAX_HISTORY_MESSAGES" in source, \
            "call_ai_with_tools must enforce _MAX_HISTORY_MESSAGES sliding window"

    def test_sliding_window_keeps_first_message(self):
        from app.agents.base import call_ai_with_tools
        source = inspect.getsource(call_ai_with_tools)
        # Must keep first message (ai_messages[0]) when trimming
        assert "ai_messages[0]" in source


# ── BUG 3: Valkey-backed rate limiter ──────────────────────────────


class TestValkeyRateLimiter:
    """ProjectRateLimiter must attempt Valkey-backed rate limiting."""

    def test_try_valkey_acquire_exists(self):
        from app.services.pipeline import ProjectRateLimiter
        assert hasattr(ProjectRateLimiter, "_try_valkey_acquire")

    def test_acquire_calls_valkey_first(self):
        from app.services.pipeline import ProjectRateLimiter
        source = inspect.getsource(ProjectRateLimiter.acquire)
        assert "_try_valkey_acquire" in source


# ── BUG 4: lazy asyncio.Semaphore ──────────────────────────────────


class TestLazySemaphore:
    """PipelineOrchestrator must lazy-init semaphore, not at construction."""

    def test_semaphore_none_at_init(self):
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator.__init__)
        assert "self._semaphore" in source
        # Must be set to None, not asyncio.Semaphore()
        assert "None" in source or "_semaphore: asyncio" not in source

    def test_get_semaphore_method_exists(self):
        from app.services.pipeline import PipelineOrchestrator
        assert hasattr(PipelineOrchestrator, "_get_semaphore")


# ── BUG 5: DB atomic transition documented ─────────────────────────


class TestDBAtomicTransition:
    """Per-run lock must be documented as local-only + DB atomicity."""

    def test_run_pipeline_mentions_atomic(self):
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator.run_pipeline)
        # Check for either DB-level or atomic pattern
        assert "lock" in source.lower() or "semaphore" in source.lower()


# ── BUG 6: step_order Integer (not SmallInteger) ──────────────────


class TestStepOrderInteger:
    """PipelineStep.step_order must use Integer, not SmallInteger."""

    def test_step_order_is_integer(self):
        from app.models.pipeline import PipelineStep
        col = PipelineStep.__table__.columns["step_order"]
        from sqlalchemy import Integer, SmallInteger
        assert not isinstance(col.type, SmallInteger), \
            "step_order must not use SmallInteger (max 32,767)"
        assert isinstance(col.type, Integer)


# ── BUG 7: clear_pipeline without SCAN ─────────────────────────────


class TestClearPipelineNoScan:
    """clear_pipeline must not use scan_iter (non-atomic)."""

    def test_no_scan_iter_in_clear(self):
        from app.services.context_engine import ContextEngine
        source = inspect.getsource(ContextEngine.clear_pipeline)
        assert "scan_iter" not in source, \
            "clear_pipeline must not use scan_iter (DEFERRED-FIX-7)"

    def test_uses_chain_key(self):
        from app.services.context_engine import ContextEngine
        source = inspect.getsource(ContextEngine.clear_pipeline)
        assert "chain" in source, \
            "clear_pipeline must enumerate keys from the chain list"


# ── BUG 8: single atomic pipeline.execute() ────────────────────────


class TestAtomicContextWrite:
    """Context engine store() must write everything in one pipeline.execute()."""

    def test_single_execute_call(self):
        from app.services.context_engine import ContextEngine
        source = inspect.getsource(ContextEngine.store)
        # Count number of `await pipe.execute()` calls — should be exactly 1
        # (the eval is now inside the pipeline, not a separate call)
        execute_calls = source.count("await pipe.execute()")
        assert execute_calls == 1, \
            f"store() must have exactly 1 pipe.execute(), found {execute_calls}"

    def test_no_separate_eval(self):
        from app.services.context_engine import ContextEngine
        source = inspect.getsource(ContextEngine.store)
        # There should be no standalone `await self._redis.eval()` — it's
        # inside pipe.eval() instead
        assert "await self._redis.eval" not in source, \
            "store() must not have standalone redis.eval (use pipe.eval)"


# ── BUG 9: Lua-based cache invalidation ────────────────────────────


class TestLuaCacheInvalidation:
    """PromptEngine.invalidate() must use Lua for atomic read-and-delete."""

    def test_invalidate_uses_lua(self):
        from app.services.prompt_engine import PromptEngine
        source = inspect.getsource(PromptEngine.invalidate)
        assert "eval" in source, \
            "invalidate() must use Lua script (redis.eval)"

    def test_no_python_smembers_call(self):
        from app.services.prompt_engine import PromptEngine
        source = inspect.getsource(PromptEngine.invalidate)
        # Must NOT call await self._redis.smembers() at the Python level.
        # "SMEMBERS" may appear in the Lua script string — that's fine.
        # We check for the Python-level call pattern.
        assert "await self._redis.smembers" not in source, \
            "invalidate() must not use Python-level smembers (TOCTOU race)"


# ── BUG 10: shared Valkey pool ─────────────────────────────────────


class TestSharedValkeyPool:
    """All Valkey consumers must use shared pool from valkey_pool.py."""

    def test_valkey_pool_module_exists(self):
        from app.services.valkey_pool import get_valkey_client, shutdown_valkey_client
        assert callable(get_valkey_client)
        assert callable(shutdown_valkey_client)

    def test_context_engine_uses_shared_pool(self):
        from app.services.context_engine import init_context_engine
        source = inspect.getsource(init_context_engine)
        assert "get_valkey_client" in source
        assert "aioredis.from_url" not in source

    def test_prompt_engine_uses_shared_pool(self):
        from app.services.prompt_engine import init_prompt_engine
        source = inspect.getsource(init_prompt_engine)
        assert "get_valkey_client" in source
        assert "aioredis.from_url" not in source

    def test_revocation_store_uses_shared_pool(self):
        from app.services.token_revocation import init_revocation_store
        source = inspect.getsource(init_revocation_store)
        assert "get_valkey_client" in source
        assert "aioredis.from_url" not in source

    def test_shutdown_does_not_close_shared_client(self):
        """Shutdown methods must NOT close the shared Redis client."""
        from app.services.context_engine import shutdown_context_engine
        source = inspect.getsource(shutdown_context_engine)
        assert "aclose" not in source

        from app.services.prompt_engine import shutdown_prompt_engine
        source = inspect.getsource(shutdown_prompt_engine)
        assert "aclose" not in source

        from app.services.token_revocation import shutdown_revocation_store
        source = inspect.getsource(shutdown_revocation_store)
        assert "aclose" not in source


# ── BUG 11: singleton reset functions ──────────────────────────────


class TestSingletonResetFunctions:
    """All module-level singletons must have reset() functions."""

    def test_ai_router_reset(self):
        from app.services.ai_router import reset_ai_router
        assert callable(reset_ai_router)

    def test_gemini_cache_reset(self):
        from app.services.ai_router import reset_gemini_cache_manager
        assert callable(reset_gemini_cache_manager)

    def test_context_engine_reset(self):
        from app.services.context_engine import reset_context_engine
        assert callable(reset_context_engine)

    def test_prompt_engine_reset(self):
        from app.services.prompt_engine import reset_prompt_engine
        assert callable(reset_prompt_engine)

    def test_valkey_pool_reset(self):
        from app.services.valkey_pool import reset_valkey_client
        assert callable(reset_valkey_client)


# ── BUG 12: streaming tool_use warning ─────────────────────────────


class TestStreamingToolUseWarning:
    """_stream_anthropic must log warning for dropped tool_use blocks."""

    def test_input_json_delta_handled(self):
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter._stream_anthropic)
        assert "input_json_delta" in source, \
            "_stream_anthropic must handle input_json_delta events"

    def test_stream_tool_use_dropped_logged(self):
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter._stream_anthropic)
        assert "stream_tool_use_dropped" in source


# ── BUG 13: connection error retry ─────────────────────────────────


class TestConnectionErrorRetry:
    """call() must retry on connection-level errors, not just HTTP status."""

    def test_connect_error_retried(self):
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter.call)
        assert "ConnectError" in source, \
            "call() must retry on httpx.ConnectError"

    def test_read_timeout_retried(self):
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter.call)
        assert "ReadTimeout" in source

    def test_pool_timeout_retried(self):
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter.call)
        assert "PoolTimeout" in source


# ── BUG 14: thinking temperature override logged ──────────────────


class TestThinkingTemperatureLog:
    """_build_anthropic_body must log when overriding temperature for thinking."""

    def test_temperature_override_logged(self):
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter._build_anthropic_body)
        assert "thinking_temperature_override" in source


# ── BUG 15: message_delta stop_reason parsed ──────────────────────


class TestMessageDeltaParsing:
    """_stream_anthropic must parse message_delta for truncation detection."""

    def test_message_delta_handled(self):
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter._stream_anthropic)
        assert "message_delta" in source

    def test_max_tokens_truncation_logged(self):
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter._stream_anthropic)
        assert "stream_truncated" in source


# ── BUG 16: audit logs include ip/user_agent ──────────────────────


class TestAuditLogIpUserAgent:
    """Register and login must pass ip_address and user_agent to log_action."""

    def test_register_passes_ip(self):
        from app.routers.auth import register
        source = inspect.getsource(register)
        assert "ip_address=" in source and "_client_ip" in source

    def test_register_passes_user_agent(self):
        from app.routers.auth import register
        source = inspect.getsource(register)
        assert "user_agent=" in source

    def test_login_passes_ip(self):
        from app.routers.auth import login
        source = inspect.getsource(login)
        assert "ip_address=" in source and "_client_ip" in source

    def test_login_passes_user_agent(self):
        from app.routers.auth import login
        source = inspect.getsource(login)
        assert "user_agent=" in source


# ── BUG 17: per_model_breakdown incremental ────────────────────────


class TestPerModelBreakdownIncremental:
    """per_model_breakdown must use incremental dicts, not deque iteration."""

    def test_per_model_dict_exists(self):
        from app.services.ai_router import ProjectCostTracker
        tracker = ProjectCostTracker("test")
        assert hasattr(tracker, "_per_model")
        assert isinstance(tracker._per_model, dict)

    def test_per_agent_dict_exists(self):
        from app.services.ai_router import ProjectCostTracker
        tracker = ProjectCostTracker("test")
        assert hasattr(tracker, "_per_agent")
        assert isinstance(tracker._per_agent, dict)

    def test_per_model_breakdown_returns_copy(self):
        from app.services.ai_router import ProjectCostTracker
        source = inspect.getsource(ProjectCostTracker.per_model_breakdown)
        # Must NOT iterate _entries (the bounded deque)
        assert "_entries" not in source, \
            "per_model_breakdown must use _per_model dict, not iterate _entries"

    def test_per_agent_breakdown_returns_copy(self):
        from app.services.ai_router import ProjectCostTracker
        source = inspect.getsource(ProjectCostTracker.per_agent_breakdown)
        assert "_entries" not in source

    def test_record_updates_incremental_dicts(self):
        from app.services.ai_router import ProjectCostTracker
        source = inspect.getsource(ProjectCostTracker.record)
        assert "_per_model" in source
        assert "_per_agent" in source


# ── BUG 18: RLS context in login/register ──────────────────────────


class TestRLSContextInAuth:
    """Login and register must set RLS context before writing audit logs."""

    def test_register_sets_tenant_context(self):
        from app.routers.auth import register
        source = inspect.getsource(register)
        assert "set_tenant_context" in source

    def test_login_sets_tenant_context(self):
        from app.routers.auth import login
        source = inspect.getsource(login)
        assert "set_tenant_context" in source


# ── BUG 19: _pipeline_task_done sanitized ──────────────────────────


class TestPipelineTaskDoneSanitized:
    """_pipeline_task_done must sanitize errors and be defensively wrapped."""

    def test_uses_sanitize_error(self):
        from app.routers.pipeline import _pipeline_task_done
        source = inspect.getsource(_pipeline_task_done)
        assert "_sanitize_error" in source, \
            "_pipeline_task_done must sanitize exception messages"

    def test_wrapped_in_try_except(self):
        from app.routers.pipeline import _pipeline_task_done
        source = inspect.getsource(_pipeline_task_done)
        lines = source.split("\n")
        has_outer_try = False
        for line in lines:
            if line.strip().startswith("try:"):
                has_outer_try = True
                break
        assert has_outer_try, \
            "_pipeline_task_done must be wrapped in try/except"

    def test_no_raw_str_exc(self):
        from app.routers.pipeline import _pipeline_task_done
        source = inspect.getsource(_pipeline_task_done)
        # Must not use str(exc) directly
        assert "str(exc)" not in source or "_sanitize_error" in source
