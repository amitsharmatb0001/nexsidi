"""Tests for Phase 2: WebSocket Auth via First Message.

Covers:
- New protocol: connect → accept → auth message → auth_ok
- Deprecated fallback: ?token= query param still works
- Auth timeout (5s)
- Invalid auth message format rejection
- _validate_ws_token() is async and uses verify_token()
- Backward compatibility
"""

from __future__ import annotations

import asyncio
import inspect

import pytest


# ── Protocol Tests ────────────────────────────────────────────────


class TestWebSocketAuthProtocol:
    """WebSocket must implement auth-via-first-message protocol."""

    def test_handler_accepts_before_auth(self):
        """Connection must be accepted BEFORE waiting for auth message."""
        from app.routers.websocket import pipeline_websocket
        source = inspect.getsource(pipeline_websocket)

        # websocket.accept() must come before receive_text()/receive_json()
        # R19-FIX: Now uses receive_text() with size check instead of receive_json()
        accept_pos = source.find("websocket.accept()")
        receive_pos = source.find("websocket.receive_text()")
        if receive_pos == -1:
            receive_pos = source.find("websocket.receive_json()")
        assert accept_pos != -1, "websocket.accept() not found"
        assert receive_pos != -1, "websocket.receive_text() or receive_json() not found"
        assert accept_pos < receive_pos, "accept() must come before receive"

    def test_handler_has_auth_timeout(self):
        """Auth message must have a timeout."""
        from app.routers.websocket import pipeline_websocket
        source = inspect.getsource(pipeline_websocket)
        assert "asyncio.wait_for" in source
        assert "timeout" in source.lower()

    def test_auth_timeout_is_5_seconds(self):
        """Auth timeout should be ~5 seconds."""
        from app.routers.websocket import _AUTH_TIMEOUT_SECONDS
        assert _AUTH_TIMEOUT_SECONDS == 5.0

    def test_handler_sends_auth_ok(self):
        """Server must send auth_ok message after successful auth."""
        from app.routers.websocket import pipeline_websocket
        source = inspect.getsource(pipeline_websocket)
        assert '"auth_ok"' in source

    def test_handler_checks_auth_message_type(self):
        """Handler must validate auth message has type=auth and token."""
        from app.routers.websocket import pipeline_websocket
        source = inspect.getsource(pipeline_websocket)
        assert '"auth"' in source
        assert '"token"' in source

    def test_handler_closes_on_invalid_auth(self):
        """Handler must close with 4001 on invalid auth."""
        from app.routers.websocket import pipeline_websocket
        source = inspect.getsource(pipeline_websocket)
        assert "4001" in source

    def test_handler_closes_on_timeout(self):
        """Handler must close on auth timeout."""
        from app.routers.websocket import pipeline_websocket
        source = inspect.getsource(pipeline_websocket)
        assert "TimeoutError" in source
        assert "Auth timeout" in source


# ── Deprecated Fallback Tests ────────────────────────────────────


class TestWebSocketDeprecatedFallback:
    """Query param ?token= must still work but log deprecation."""

    def test_deprecated_query_param_supported(self):
        """Handler must accept token as query param (deprecated)."""
        from app.routers.websocket import pipeline_websocket
        source = inspect.getsource(pipeline_websocket)
        # Must check for "if token:" to handle deprecated path
        assert "if token" in source

    def test_deprecated_query_param_logs_warning(self):
        """Deprecated query param must log a deprecation warning."""
        from app.routers.websocket import pipeline_websocket
        source = inspect.getsource(pipeline_websocket)
        assert "deprecated" in source.lower()


# ── _validate_ws_token Tests ──────────────────────────────────────


class TestValidateWsToken:
    """_validate_ws_token must be async and check revocation."""

    def test_is_async(self):
        """_validate_ws_token must be async (uses verify_token)."""
        from app.routers.websocket import _validate_ws_token
        assert asyncio.iscoroutinefunction(_validate_ws_token)

    def test_uses_verify_token(self):
        """Must use verify_token() (includes revocation check), not decode_token()."""
        from app.routers.websocket import _validate_ws_token
        source = inspect.getsource(_validate_ws_token)
        assert "verify_token" in source


# ── ConnectionManager Tests ───────────────────────────────────────


class TestConnectionManagerPhase2:
    """ConnectionManager no longer calls accept() — caller does."""

    def test_connect_does_not_accept(self):
        """ConnectionManager.connect() must NOT call websocket.accept()."""
        from app.routers.websocket import ConnectionManager
        source = inspect.getsource(ConnectionManager.connect)
        # accept() should NOT be in the connect method anymore
        assert "websocket.accept()" not in source
