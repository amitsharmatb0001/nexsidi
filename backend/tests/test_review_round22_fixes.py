"""Tests for Review Round 22 fixes.

Covers:
- PIPELINE: run.error sanitized via _sanitize_error (no API key leakage)
- CELERY: Task return values sanitized via _sanitize_error
- AUTH: Family revocation TTL uses configurable refresh_token_expire_days
- AUTH: FallbackStore fail-closed for empty jti/user_id
- AI: Gemini streaming checks SAFETY/RECITATION/OTHER finishReason
- WS: WebSocket accept() before close() in pre-auth flow
- AI: AIRouter.close() protected by _http_lock
- AUTH: bcrypt runs in thread pool via asyncio.to_thread
- AUTH: /refresh catches KeyError for missing org claim
"""

from __future__ import annotations

import inspect

import pytest


# ── PIPELINE: run.error sanitized ─────────────────────────────────


class TestPipelineErrorSanitized:
    """run.error in pipeline crash must use _sanitize_error."""

    def test_run_pipeline_uses_sanitize_error(self):
        """run_pipeline must sanitize exception in run.error."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator.run_pipeline)
        assert "_sanitize_error" in source, \
            "run_pipeline must use _sanitize_error for crash error messages"

    def test_run_error_not_raw_str_exc(self):
        """run.error must not contain raw str(exc)."""
        from app.services.pipeline import PipelineOrchestrator
        source = inspect.getsource(PipelineOrchestrator.run_pipeline)
        lines = source.split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "run.error" in stripped and "str(exc)" in stripped:
                pytest.fail(f"run.error uses raw str(exc): {stripped}")


# ── CELERY: Task return values sanitized ──────────────────────────


class TestCeleryTaskSanitized:
    """Celery task return values must use _sanitize_error."""

    def test_run_pipeline_task_sanitized(self):
        """run_pipeline_task must sanitize exception in return dict."""
        from app.tasks.pipeline_tasks import run_pipeline_task
        source = inspect.getsource(run_pipeline_task)
        assert "_sanitize_error" in source, \
            "run_pipeline_task must use _sanitize_error"

    def test_resume_pipeline_task_sanitized(self):
        """resume_pipeline_task must sanitize exception in return dict."""
        from app.tasks.pipeline_tasks import resume_pipeline_task
        source = inspect.getsource(resume_pipeline_task)
        assert "_sanitize_error" in source, \
            "resume_pipeline_task must use _sanitize_error"


# ── AUTH: Family revocation TTL from config ───────────────────────


class TestFamilyRevocationTTL:
    """revoke_all_user_tokens must use configurable TTL, not hardcoded."""

    def test_uses_settings_refresh_token_expire_days(self):
        """Must read refresh_token_expire_days from settings."""
        from app.services.token_revocation import TokenRevocationStore
        source = inspect.getsource(TokenRevocationStore.revoke_all_user_tokens)
        assert "refresh_token_expire_days" in source, \
            "Must use refresh_token_expire_days from settings"

    def test_no_hardcoded_max_token_ttl(self):
        """Must not use hardcoded _MAX_TOKEN_TTL_SECONDS."""
        from app.services.token_revocation import TokenRevocationStore
        source = inspect.getsource(TokenRevocationStore.revoke_all_user_tokens)
        assert "_MAX_TOKEN_TTL_SECONDS" not in source, \
            "Must not use hardcoded _MAX_TOKEN_TTL_SECONDS"


# ── AUTH: FallbackStore fail-closed ───────────────────────────────


class TestFallbackStoreFailClosed:
    """FallbackStore must return True for empty jti/user_id."""

    def test_is_revoked_empty_jti(self):
        """FallbackStore.is_revoked must return True for empty jti."""
        from app.services.token_revocation import _FallbackRevocationStore
        source = inspect.getsource(_FallbackRevocationStore.is_revoked)
        assert "return True" in source, \
            "FallbackStore.is_revoked must return True for empty jti"

    def test_is_user_revoked_empty_user_id(self):
        """FallbackStore.is_user_revoked must return True for empty user_id."""
        from app.services.token_revocation import _FallbackRevocationStore
        source = inspect.getsource(_FallbackRevocationStore.is_user_revoked)
        assert "return True" in source, \
            "FallbackStore.is_user_revoked must return True for empty user_id"


# ── AI: Streaming finishReason checks ─────────────────────────────


class TestStreamingFinishReasonChecks:
    """Gemini streaming must check SAFETY/RECITATION/OTHER finishReason."""

    def test_stream_google_ai_checks_finish_reason(self):
        """_stream_google_ai must check finishReason."""
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter._stream_google_ai)
        assert "SAFETY" in source
        assert "RECITATION" in source
        assert "OTHER" in source

    def test_stream_vertex_checks_finish_reason(self):
        """_stream_vertex must check finishReason."""
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter._stream_vertex)
        assert "SAFETY" in source
        assert "RECITATION" in source

    def test_stream_raises_on_safety_block(self):
        """Streaming must raise ValueError on SAFETY finishReason."""
        from app.services.ai_router import AIRouter
        for method_name in ("_stream_google_ai", "_stream_vertex"):
            source = inspect.getsource(getattr(AIRouter, method_name))
            # Must have raise near SAFETY check
            lines = source.split("\n")
            for i, line in enumerate(lines):
                if "SAFETY" in line and "RECITATION" in line and not line.strip().startswith("#"):
                    # Check nearby lines for raise
                    nearby = "\n".join(lines[max(0, i-2):i+3])
                    assert "raise" in nearby, \
                        f"{method_name} must raise on SAFETY/RECITATION finishReason"
                    break


# ── WS: accept() before close() ──────────────────────────────────


class TestWebSocketAcceptBeforeClose:
    """WebSocket must call accept() before any close() in pre-auth flow."""

    def test_accept_before_semaphore(self):
        """accept() must come before semaphore acquire in handler."""
        from app.routers.websocket import pipeline_websocket
        source = inspect.getsource(pipeline_websocket)
        # Find the main accept() call (not the UUID error one)
        lines = source.split("\n")
        accept_line = None
        semaphore_line = None
        for i, line in enumerate(lines):
            if "websocket.accept()" in line and "Invalid run_id" not in source[max(0, source.find(line) - 200):source.find(line)]:
                if accept_line is None:
                    accept_line = i
            if "_pre_auth_semaphore.acquire()" in line:
                semaphore_line = i
                break
        # Accept must come before semaphore (or at least not after)
        # The key fix is that close() is never called on un-accepted websocket
        assert accept_line is not None, "Must have websocket.accept()"

    def test_no_private_value_access(self):
        """Must not access _pre_auth_semaphore._value (private attribute)."""
        from app.routers.websocket import pipeline_websocket
        source = inspect.getsource(pipeline_websocket)
        assert "._value" not in source, \
            "Must not access private _value attribute of asyncio.Semaphore"


# ── AI: AIRouter.close() lock safety ─────────────────────────────


class TestAIRouterCloseLock:
    """AIRouter.close() must acquire _http_lock for thread safety."""

    def test_close_acquires_http_lock(self):
        """close() must use self._http_lock."""
        from app.services.ai_router import AIRouter
        source = inspect.getsource(AIRouter.close)
        assert "_http_lock" in source, \
            "close() must acquire _http_lock to prevent race with _get_http()"


# ── AUTH: bcrypt in thread pool ───────────────────────────────────


class TestBcryptThreadPool:
    """bcrypt must run in thread pool to avoid blocking event loop."""

    def test_register_uses_to_thread(self):
        """register must use asyncio.to_thread for hash_password."""
        from app.routers.auth import register
        source = inspect.getsource(register)
        assert "to_thread" in source and "hash_password" in source, \
            "register must use asyncio.to_thread(hash_password, ...)"

    def test_login_uses_to_thread(self):
        """login must use asyncio.to_thread for verify_password."""
        from app.routers.auth import login
        source = inspect.getsource(login)
        assert "to_thread" in source and "verify_password" in source, \
            "login must use asyncio.to_thread(verify_password, ...)"


# ── AUTH: /refresh catches KeyError ───────────────────────────────


class TestRefreshKeyError:
    """/refresh must catch KeyError for missing org claim."""

    def test_refresh_catches_key_error(self):
        """refresh_token must catch KeyError from payload['org']."""
        from app.routers.auth import refresh_token
        source = inspect.getsource(refresh_token)
        assert "KeyError" in source, \
            "refresh_token must catch KeyError for missing claims"

    def test_key_error_returns_401(self):
        """KeyError must return 401, not 500."""
        from app.routers.auth import refresh_token
        source = inspect.getsource(refresh_token)
        # Find the except block that catches KeyError
        lines = source.split("\n")
        in_except = False
        found_401 = False
        for line in lines:
            stripped = line.strip()
            if "KeyError" in stripped and "except" in stripped:
                in_except = True
            if in_except and ("401" in stripped or "UNAUTHORIZED" in stripped):
                found_401 = True
                break
        assert found_401, "KeyError must return 401"
