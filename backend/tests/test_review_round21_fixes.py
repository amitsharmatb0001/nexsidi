"""Tests for Review Round 21 fixes.

Covers:
- AUTH: is_user_revoked fails-closed on corrupted Valkey data
- AUTH: fromtimestamp(exp) overflow guard in /refresh and /logout
- DB: Admin engine has pool_timeout
- PIPELINE: rebuild_run() restores fix_retest_cycle/challenge_retry_count
- AGENTS: 5 agents sanitize exceptions in AgentResult.error
- AI: GeminiCacheManager fast-path uses .get() not []
- WS: _pre_auth_semaphore replaces counter
- SECRET: get_vertex_credentials() thread-safe singleton
- PIPELINE: step_results output cleared after persist
- AI: Gemini RECITATION/OTHER finish reasons handled
"""

from __future__ import annotations

import inspect
import threading

import pytest


# ── AUTH: is_user_revoked fail-closed on corrupted data ──────────


class TestIsUserRevokedFailClosed:
    """is_user_revoked must return True on corrupted Valkey data."""

    def test_parse_error_returns_true(self):
        """ValueError/TypeError in is_user_revoked must return True, not False."""
        from app.services.token_revocation import TokenRevocationStore
        source = inspect.getsource(TokenRevocationStore.is_user_revoked)
        lines = source.split("\n")
        in_except = False
        found_return_true = False
        for line in lines:
            stripped = line.strip()
            if "ValueError" in stripped and "TypeError" in stripped and "except" in stripped:
                in_except = True
            if in_except and "return True" in stripped:
                found_return_true = True
                break
            if in_except and stripped.startswith("return") and "True" not in stripped:
                pytest.fail("is_user_revoked except handler returns non-True value")
        assert found_return_true, "is_user_revoked must return True on parse error"


# ── AUTH: fromtimestamp(exp) overflow guard ───────────────────────


class TestFromtimestampExpGuard:
    """All fromtimestamp(exp) calls in auth router must catch OverflowError."""

    def test_refresh_guards_fromtimestamp(self):
        """refresh_token() must guard fromtimestamp for old_exp."""
        from app.routers.auth import refresh_token
        source = inspect.getsource(refresh_token)
        # Find the section near old_expires_at
        assert "old_expires_at" in source
        # Must catch OverflowError near fromtimestamp
        pos_ft = source.find("old_expires_at")
        nearby = source[max(0, pos_ft - 200):pos_ft + 300]
        assert "OverflowError" in nearby, \
            "refresh_token must catch OverflowError around fromtimestamp(old_exp)"

    def test_logout_guards_fromtimestamp(self):
        """logout() must guard fromtimestamp for exp_ts."""
        from app.routers.auth import logout
        source = inspect.getsource(logout)
        assert "OverflowError" in source, \
            "logout must catch OverflowError around fromtimestamp(exp_ts)"

    def test_logout_guards_refresh_token_fromtimestamp(self):
        """logout() must also guard fromtimestamp for refresh token exp."""
        from app.routers.auth import logout
        source = inspect.getsource(logout)
        # Find r_expires_at section — must have OverflowError nearby
        pos = source.find("r_expires_at")
        assert pos > 0, "Must have r_expires_at for refresh token expiry"
        nearby = source[max(0, pos - 200):pos + 300]
        assert "OverflowError" in nearby, \
            "Refresh token fromtimestamp in logout must catch OverflowError"


# ── DB: Admin engine pool_timeout ────────────────────────────────


class TestAdminEnginePoolTimeout:
    """Admin engine must have pool_timeout to prevent indefinite hangs."""

    def test_admin_engine_has_pool_timeout(self):
        """setup_database admin engine must include pool_timeout."""
        from app.database import setup_database
        source = inspect.getsource(setup_database)
        # Find the admin engine section
        admin_pos = source.find("_admin_engine")
        assert admin_pos > 0
        # The admin engine block (from _admin_engine to the next major section)
        admin_section = source[admin_pos:]
        assert "pool_timeout" in admin_section, \
            "Admin engine must have pool_timeout parameter"


# ── PIPELINE: rebuild_run restores retry counters ────────────────


class TestRebuildRunRestoresCounters:
    """rebuild_run must restore fix_retest_cycle and challenge_retry_count."""

    def test_restores_fix_retest_cycle(self):
        """rebuild_run must set fix_retest_cycle from context."""
        from app.services.pipeline import PipelinePersistence
        source = inspect.getsource(PipelinePersistence.rebuild_run)
        assert "fix_retest_cycle" in source, \
            "rebuild_run must restore fix_retest_cycle"
        assert "__fix_retest_cycle__" in source, \
            "rebuild_run must read __fix_retest_cycle__ from context"

    def test_restores_challenge_retry_count(self):
        """rebuild_run must set challenge_retry_count from context."""
        from app.services.pipeline import PipelinePersistence
        source = inspect.getsource(PipelinePersistence.rebuild_run)
        assert "challenge_retry_count" in source, \
            "rebuild_run must restore challenge_retry_count"
        assert "__challenge_retry_count__" in source, \
            "rebuild_run must read __challenge_retry_count__ from context"

    def test_persist_run_saves_counters(self):
        """_persist_run must save retry counters to context before DB write."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator._persist_run)
        assert "__fix_retest_cycle__" in source, \
            "_persist_run must save fix_retest_cycle to context"
        assert "__challenge_retry_count__" in source, \
            "_persist_run must save challenge_retry_count to context"

    def test_counters_saved_before_save_run(self):
        """Retry counters must be saved to context BEFORE save_run() call."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator._persist_run)
        counter_pos = source.find("__fix_retest_cycle__")
        save_pos = source.find("save_run(run)")
        assert counter_pos < save_pos, \
            "Retry counters must be saved to context before calling save_run()"


# ── AGENTS: sanitized exception errors ───────────────────────────


class TestAgentsSanitizeErrors:
    """5 agents must use _sanitize_error instead of raw str(exc)."""

    @pytest.mark.parametrize("agent_module,class_name", [
        ("app.agents.vikram", "Vikram"),
        ("app.agents.saanvi", "Saanvi"),
        ("app.agents.tilotma", "Tilotma"),
        ("app.agents.dhruv", "Dhruv"),
        ("app.agents.vanya", "Vanya"),
    ])
    def test_agent_uses_sanitize_error(self, agent_module, class_name):
        """Agent must use _sanitize_error, not raw f'...{exc}'."""
        import importlib
        mod = importlib.import_module(agent_module)
        cls = getattr(mod, class_name)
        source = inspect.getsource(cls)
        assert "_sanitize_error" in source, \
            f"{class_name} must use _sanitize_error for exception handling"

    @pytest.mark.parametrize("agent_module,class_name", [
        ("app.agents.vikram", "Vikram"),
        ("app.agents.saanvi", "Saanvi"),
        ("app.agents.tilotma", "Tilotma"),
        ("app.agents.dhruv", "Dhruv"),
        ("app.agents.vanya", "Vanya"),
    ])
    def test_agent_no_raw_exc_in_error(self, agent_module, class_name):
        """Agent must not have raw f-string with {exc} in error= field."""
        import importlib
        mod = importlib.import_module(agent_module)
        cls = getattr(mod, class_name)
        source = inspect.getsource(cls)
        # Ensure there is no `error=f"...{exc}"` without sanitization
        lines = source.split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if 'error=f"' in stripped and "{exc}" in stripped:
                # Must contain _sanitize_error in the same line
                assert "_sanitize_error" in stripped, \
                    f"{class_name} has unsanitized exception in error field: {stripped}"


# ── AI: GeminiCacheManager fast-path safe read ───────────────────


class TestCacheManagerFastPath:
    """Fast-path cache read must use .get() not [] to avoid KeyError."""

    def test_fast_path_uses_get(self):
        """get_or_create_cache must use .get() for lockless fast path."""
        from app.services.ai_router import GeminiCacheManager
        source = inspect.getsource(GeminiCacheManager.get_or_create_cache)
        # Find the fast path section (before the global lock)
        global_lock_pos = source.find("_global_lock")
        fast_path = source[:global_lock_pos]
        # Must use .get() pattern, not self._cache[content_hash]
        assert "_cache.get(" in fast_path, \
            "Fast path must use _cache.get() not _cache[] to avoid KeyError"

    def test_fast_path_no_subscript(self):
        """Fast path must not use _cache[content_hash] (KeyError race)."""
        from app.services.ai_router import GeminiCacheManager
        source = inspect.getsource(GeminiCacheManager.get_or_create_cache)
        global_lock_pos = source.find("_global_lock")
        fast_path = source[:global_lock_pos]
        # Should NOT have direct subscript access on the fast path
        # (it's OK to have `in self._cache` check, but not `self._cache[`)
        lines = fast_path.split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert "self._cache[content_hash]" not in stripped, \
                f"Fast path has unsafe subscript access: {stripped}"


# ── WS: asyncio.Semaphore replaces counter ───────────────────────


class TestPreAuthSemaphore:
    """WebSocket pre-auth limiting must use asyncio.Semaphore."""

    def test_uses_semaphore(self):
        """Module must define _pre_auth_semaphore."""
        from app.routers import websocket as ws_mod
        assert hasattr(ws_mod, "_pre_auth_semaphore"), \
            "websocket module must have _pre_auth_semaphore"

    def test_no_global_counter(self):
        """Must not have mutable global _pre_auth_connections int counter."""
        from app.routers import websocket as ws_mod
        source = inspect.getsource(ws_mod.pipeline_websocket)
        # Must not have `global _pre_auth_connections` in the handler
        assert "global _pre_auth_connections" not in source, \
            "Handler must not use global counter; use semaphore instead"

    def test_semaphore_released_in_finally(self):
        """Must release semaphore in finally block."""
        from app.routers import websocket as ws_mod
        source = inspect.getsource(ws_mod.pipeline_websocket)
        assert "release()" in source, \
            "Semaphore must be released in finally block"


# ── SECRET: thread-safe singleton ─────────────────────────────────


class TestVertexCredentialsThreadSafe:
    """get_vertex_credentials must be thread-safe."""

    def test_uses_threading_lock(self):
        """Must use threading.Lock for thread-safe singleton."""
        from app.services import secret_manager as sm
        source = inspect.getsource(sm.get_vertex_credentials)
        assert "_vertex_lock" in source, \
            "get_vertex_credentials must use _vertex_lock for thread safety"

    def test_double_check_pattern(self):
        """Must use double-checked locking pattern."""
        from app.services import secret_manager as sm
        source = inspect.getsource(sm.get_vertex_credentials)
        # Must check _vertex_mgr before AND inside the lock
        lines = source.split("\n")
        checks_before_lock = 0
        checks_inside_lock = 0
        in_lock = False
        for line in lines:
            stripped = line.strip()
            if "_vertex_lock" in stripped:
                in_lock = True
            if "_vertex_mgr is not None" in stripped or "_vertex_mgr is None" in stripped:
                if in_lock:
                    checks_inside_lock += 1
                else:
                    checks_before_lock += 1
        assert checks_before_lock >= 1, "Must check _vertex_mgr before acquiring lock"
        assert checks_inside_lock >= 1, "Must double-check _vertex_mgr inside lock"

    def test_lock_module_exists(self):
        """The module must have a _vertex_lock attribute."""
        from app.services import secret_manager as sm
        assert hasattr(sm, "_vertex_lock")
        assert isinstance(sm._vertex_lock, type(threading.Lock()))


# ── PIPELINE: step_results output cleared ─────────────────────────


class TestStepResultsCleared:
    """step_results output must be cleared after persistence and loop checks."""

    def test_output_cleared_after_persist(self):
        """_run_pipeline_inner must clear step_result output after persist."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator._run_pipeline_inner)
        # Must have output = None after _persist_step
        persist_pos = source.find("_persist_step")
        after_persist = source[persist_pos:]
        assert "output = None" in after_persist or ".output = None" in after_persist, \
            "Must clear step_result output after persistence"

    def test_clear_comes_after_persist_step(self):
        """Output clear must come AFTER _persist_step (not before)."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator._run_pipeline_inner)
        persist_pos = source.find("_persist_step")
        clear_pos = source.find(".output = None", persist_pos)
        assert clear_pos > persist_pos, \
            "output = None must come after _persist_step"

    def test_clear_comes_after_fix_retest_check(self):
        """Output clear must come AFTER fix-retest loop check."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator._run_pipeline_inner)
        fix_check_pos = source.find("_has_errors_to_fix")
        clear_pos = source.find(".output = None")
        assert clear_pos > fix_check_pos, \
            "output = None must come after _has_errors_to_fix check"


# ── AI: Gemini RECITATION/OTHER finish reasons ───────────────────


class TestGeminiFinishReasons:
    """Gemini RECITATION and OTHER finish reasons must be handled."""

    def test_recitation_handled(self):
        """Must raise on RECITATION finish reason."""
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter._parse_google_response)
        assert "RECITATION" in source, \
            "Must handle RECITATION finish reason"

    def test_other_handled(self):
        """Must raise on OTHER finish reason."""
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter._parse_google_response)
        # Find OTHER as a finish reason check (not in comments)
        lines = source.split("\n")
        found = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if '"OTHER"' in stripped and "finish_reason" in stripped:
                found = True
                break
        assert found, "Must handle OTHER finish reason"

    def test_recitation_raises(self):
        """RECITATION must raise ValueError, not return empty content."""
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter._parse_google_response)
        # Find RECITATION block
        rec_pos = source.find('"RECITATION"')
        nearby = source[rec_pos:rec_pos + 200]
        assert "raise" in nearby, \
            "RECITATION finish reason must raise, not silently return"
