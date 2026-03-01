"""R35 Brutal Integration Tests: Real DB + Real Valkey.

Covers bugs found in R35 review:
1. Non-Celery resume path missing double-execution guard (HIGH)
2. Token revocation init logs raw exception with Valkey password (MEDIUM)
3. SSRF filter misses 0.0.0.0 (is_unspecified) and IPv4-mapped IPv6 (MEDIUM)
4. checkpoint reject/request_changes skip expiry/status guards (MEDIUM)
5. Pipeline concurrent guard missing org_id defense-in-depth (MEDIUM)
6. WebSocket broadcast blocks on half-open TCP (HIGH) — asyncio.wait_for
7. audit.log_action bracketed IPv6 fails validation (MEDIUM)
8. notification._check_rate_limit dead code removed (MEDIUM)
"""

from __future__ import annotations

import asyncio
import inspect
import ipaddress
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote_plus

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# ── Module-scoped event loop for shared lifespan ────────────────
pytestmark = pytest.mark.asyncio(loop_scope="module")

# ── Infrastructure ──────────────────────────────────────────────
_DB_HOST = os.environ.get("DB_HOST", "34.93.166.39")
_DB_NAME = os.environ.get("DB_NAME", "nexsidi-database")
_DB_USER = os.environ.get("DB_USER", "postgres")
_DB_PASSWORD = os.environ.get("DB_PASSWORD", 'J>3y&s~#F|*S)f""')
_VALKEY_URL = os.environ.get("VALKEY_URL", "redis://localhost:6379/15")
_TEST_DOMAIN = "nexsidi-inttest.dev"


def _build_db_url() -> str:
    return (
        f"postgresql+asyncpg://{_DB_USER}:{quote_plus(_DB_PASSWORD)}"
        f"@{_DB_HOST}:5432/{_DB_NAME}"
    )


def _email(prefix: str = "r35") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}@{_TEST_DOMAIN}"


try:
    async def _can_connect() -> bool:
        try:
            from sqlalchemy import text
            from sqlalchemy.ext.asyncio import create_async_engine
            engine = create_async_engine(_build_db_url(), pool_size=1, connect_args={"timeout": 5})
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            await engine.dispose()

            import redis.asyncio as aioredis
            r = aioredis.from_url(_VALKEY_URL, socket_connect_timeout=3)
            await r.ping()
            await r.aclose()
            return True
        except Exception:
            return False

    _INFRA_OK = asyncio.run(_can_connect())
except Exception:
    _INFRA_OK = False

requires_infra = pytest.mark.skipif(not _INFRA_OK, reason="Real DB + Valkey required")


def _reset_rate_limiter():
    from app.routers.auth import _rate_limiter
    _rate_limiter._attempts.clear()


# ── Fixtures ────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="module")
async def client() -> AsyncGenerator[AsyncClient, None]:
    os.environ["DATABASE_URL"] = _build_db_url()
    os.environ["VALKEY_URL"] = _VALKEY_URL
    os.environ.setdefault("JWT_SECRET_KEY", "r35-brutal-test-key-at-least-32-chars-long-enough")
    os.environ.setdefault("ENVIRONMENT", "development")
    os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-key")
    os.environ.setdefault("GOOGLE_AI_API_KEY", "test-google-key")

    from app.config import get_settings
    get_settings.cache_clear()

    from asgi_lifespan import LifespanManager
    from app.main import create_app

    app = create_app()
    async with LifespanManager(app, startup_timeout=120, shutdown_timeout=30) as mgr:
        _reset_rate_limiter()
        transport = ASGITransport(app=mgr.app, raise_app_exceptions=False)
        async with AsyncClient(
            transport=transport, base_url="http://test", follow_redirects=True,
        ) as ac:
            yield ac

    get_settings.cache_clear()


async def _register(c: AsyncClient, prefix: str = "r35") -> dict[str, Any]:
    _reset_rate_limiter()
    email = _email(prefix)
    resp = await c.post("/api/v1/auth/register", json={
        "email": email, "password": "BrutalTest123!",
        "name": f"R35 {prefix}", "organization_name": f"R35Org-{uuid.uuid4().hex[:6]}",
    })
    assert resp.status_code == 201, f"Register failed ({email}): {resp.text}"
    data = resp.json()
    data["email"] = email
    data["headers"] = {"Authorization": f"Bearer {data['access_token']}"}
    return data


# ══════════════════════════════════════════════════════════════════
# 1. NON-CELERY RESUME DOUBLE-EXECUTION GUARD
# ══════════════════════════════════════════════════════════════════


class TestNonCeleryResumeGuard:
    """Non-Celery resume path must set status=RUNNING before dispatch."""

    def test_resume_noncelery_sets_running_before_dispatch(self):
        """Inspect source: non-Celery else branch must set RUNNING + persist."""
        from app.routers import pipeline

        source = inspect.getsource(pipeline.resume_pipeline)

        # Find the non-Celery else branch
        else_idx = source.find("else:\n        # R35-FIX")
        assert else_idx > 0, "Non-Celery else branch with R35-FIX not found"

        # Get the else block (needs to be large enough to include both
        # _persist_run and create_task calls)
        else_block = source[else_idx:else_idx + 1200]

        # Must set status to RUNNING
        assert "PipelineRunStatus.RUNNING" in else_block, (
            "Non-Celery path must set run.status = PipelineRunStatus.RUNNING"
        )

        # Must persist before dispatch
        assert "_persist_run" in else_block, (
            "Non-Celery path must call _persist_run before create_task"
        )

        # Verify _persist_run comes BEFORE create_task in actual code lines
        # (skip comment lines which may mention create_task earlier)
        lines = else_block.split("\n")
        persist_line = None
        task_line = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "_persist_run" in stripped and persist_line is None:
                persist_line = i
            if "create_task" in stripped and task_line is None:
                task_line = i

        assert persist_line is not None, "_persist_run not found in else block"
        assert task_line is not None, "create_task not found in else block"
        assert persist_line < task_line, (
            f"_persist_run (line {persist_line}) must come before "
            f"create_task (line {task_line})"
        )

    def test_resume_noncelery_has_revert_on_failure(self):
        """Non-Celery path must revert status on persist failure."""
        from app.routers import pipeline

        source = inspect.getsource(pipeline.resume_pipeline)

        # Find the non-Celery branch
        else_idx = source.find("else:\n        # R35-FIX")
        else_block = source[else_idx:else_idx + 1200]

        # Must save original_status for revert
        assert "original_status" in else_block, (
            "Non-Celery path must save original_status for revert"
        )

        # Must have except block for persist failure
        assert "resume_persist_failed" in else_block or "503" in else_block, (
            "Non-Celery path must handle persist failure"
        )


# ══════════════════════════════════════════════════════════════════
# 2. TOKEN REVOCATION SANITIZES VALKEY PASSWORD IN LOGS
# ══════════════════════════════════════════════════════════════════


class TestRevocationLogSanitization:
    """init_revocation_store() must not leak Valkey password in logs."""

    def test_exception_sanitized_in_init(self):
        """Check that str(exc) is NOT used directly — must be sanitized."""
        from app.services import token_revocation

        source = inspect.getsource(token_revocation.init_revocation_store)

        # Find the except block
        except_idx = source.find("except Exception as exc:")
        except_block = source[except_idx:except_idx + 400]

        # Must NOT use raw str(exc) as error= value
        # The pattern error=str(exc) without sanitization is the bug
        # The fix uses re.sub to redact passwords from URLs
        assert "REDACTED" in except_block, (
            "Exception logging must sanitize Valkey password with REDACTED"
        )

    def test_sanitization_regex_works(self):
        """The sanitization regex must redact redis:// URLs with passwords."""
        # Simulate the sanitization logic
        exc_msg = "Error connecting to redis://:s3cret_passw0rd@10.0.1.5:6379/0"
        sanitized = re.sub(r"://:[^@]+@", "://[REDACTED]@", exc_msg)

        assert "s3cret_passw0rd" not in sanitized
        assert "[REDACTED]" in sanitized
        assert "10.0.1.5" in sanitized  # Host should remain for debugging

    def test_sanitization_handles_no_password(self):
        """URLs without password should pass through unchanged."""
        exc_msg = "Error connecting to redis://localhost:6379/0"
        sanitized = re.sub(r"://:[^@]+@", "://[REDACTED]@", exc_msg)

        assert sanitized == exc_msg  # No change — no password to redact


# ══════════════════════════════════════════════════════════════════
# 3. SSRF FILTER: 0.0.0.0 AND IPv4-MAPPED IPv6
# ══════════════════════════════════════════════════════════════════


class TestSSRFFilterEnhanced:
    """SSRF URL validator must block 0.0.0.0 and IPv4-mapped IPv6."""

    def test_blocks_zero_ip(self):
        """0.0.0.0 maps to localhost on many systems — must be blocked."""
        from app.services.input_processor import _is_valid_url

        assert _is_valid_url("http://0.0.0.0:8080/admin") is False

    def test_blocks_ipv4_mapped_ipv6_loopback(self):
        """::ffff:127.0.0.1 is IPv4-mapped loopback — must be blocked."""
        from app.services.input_processor import _is_valid_url

        assert _is_valid_url("http://[::ffff:127.0.0.1]/admin") is False

    def test_blocks_ipv4_mapped_ipv6_metadata(self):
        """::ffff:169.254.169.254 is IPv4-mapped link-local — must be blocked."""
        from app.services.input_processor import _is_valid_url

        assert _is_valid_url("http://[::ffff:169.254.169.254]/metadata") is False

    def test_blocks_ipv4_mapped_ipv6_private(self):
        """::ffff:10.0.0.1 is IPv4-mapped private — must be blocked."""
        from app.services.input_processor import _is_valid_url

        assert _is_valid_url("http://[::ffff:10.0.0.1]:6379/") is False

    def test_allows_valid_public_url(self):
        """Public URLs should still pass."""
        from app.services.input_processor import _is_valid_url

        assert _is_valid_url("https://example.com/api") is True

    def test_blocks_existing_ssrf_targets(self):
        """Pre-existing SSRF blocks still work (regression)."""
        from app.services.input_processor import _is_valid_url

        assert _is_valid_url("http://127.0.0.1/admin") is False
        assert _is_valid_url("http://localhost/admin") is False
        assert _is_valid_url("http://169.254.169.254/latest/meta-data/") is False
        assert _is_valid_url("http://metadata.google.internal/") is False

    def test_is_unspecified_in_source(self):
        """Source code must check is_unspecified (not just is_private/is_loopback)."""
        from app.services import input_processor

        # R36 refactored: is_unspecified may be in _is_ip_unsafe() helper
        source = inspect.getsource(input_processor._is_valid_url)
        if hasattr(input_processor, "_is_ip_unsafe"):
            source += inspect.getsource(input_processor._is_ip_unsafe)
        assert "is_unspecified" in source, (
            "_is_valid_url must check ip.is_unspecified to block 0.0.0.0"
        )

    def test_ipv4_mapped_check_in_source(self):
        """Source must check ipv4_mapped for IPv4-mapped IPv6 bypass."""
        from app.services import input_processor

        # R36 refactored: ipv4_mapped may be in _is_ip_unsafe() helper
        source = inspect.getsource(input_processor._is_valid_url)
        if hasattr(input_processor, "_is_ip_unsafe"):
            source += inspect.getsource(input_processor._is_ip_unsafe)
        assert "ipv4_mapped" in source, (
            "_is_valid_url must check IPv4-mapped IPv6 addresses"
        )


# ══════════════════════════════════════════════════════════════════
# 4. CHECKPOINT REJECT/REQUEST_CHANGES GUARDS
# ══════════════════════════════════════════════════════════════════


class TestCheckpointRejectGuards:
    """reject() and request_changes() must enforce same guards as approve()."""

    def test_reject_requires_pending_status(self):
        """reject() must raise ValueError if checkpoint is not PENDING."""
        from app.services.checkpoint import (
            ApprovalStatus,
            CheckpointData,
            CheckpointService,
        )

        svc = CheckpointService()
        cp = CheckpointData(
            checkpoint_id="cp-1",
            pipeline_run_id="run-1",
        )
        cp.status = ApprovalStatus.APPROVED  # Already approved
        svc._checkpoints["cp-1"] = cp

        with pytest.raises(ValueError, match="not pending"):
            svc.reject("cp-1", organization_id=cp.organization_id)

    def test_reject_checks_expiry(self):
        """reject() must raise ValueError if checkpoint has expired."""
        from app.services.checkpoint import (
            ApprovalStatus,
            CheckpointData,
            CheckpointService,
        )

        svc = CheckpointService()
        cp = CheckpointData(
            checkpoint_id="cp-2",
            pipeline_run_id="run-2",
        )
        cp.status = ApprovalStatus.PENDING
        cp.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)  # Expired
        svc._checkpoints["cp-2"] = cp

        with pytest.raises(ValueError, match="expired"):
            svc.reject("cp-2", organization_id=cp.organization_id)

        # Status should be set to EXPIRED
        assert cp.status == ApprovalStatus.EXPIRED

    def test_request_changes_requires_pending_status(self):
        """request_changes() must raise ValueError if not PENDING."""
        from app.services.checkpoint import (
            ApprovalStatus,
            CheckpointData,
            CheckpointService,
        )

        svc = CheckpointService()
        cp = CheckpointData(
            checkpoint_id="cp-3",
            pipeline_run_id="run-3",
        )
        cp.status = ApprovalStatus.REJECTED  # Already rejected
        svc._checkpoints["cp-3"] = cp

        with pytest.raises(ValueError, match="not pending"):
            svc.request_changes("cp-3", organization_id=cp.organization_id, changes=["fix this"])

    def test_request_changes_checks_expiry(self):
        """request_changes() must raise ValueError if expired."""
        from app.services.checkpoint import (
            ApprovalStatus,
            CheckpointData,
            CheckpointService,
        )

        svc = CheckpointService()
        cp = CheckpointData(
            checkpoint_id="cp-4",
            pipeline_run_id="run-4",
        )
        cp.status = ApprovalStatus.PENDING
        cp.expires_at = datetime.now(timezone.utc) - timedelta(days=1)  # Expired
        svc._checkpoints["cp-4"] = cp

        with pytest.raises(ValueError, match="expired"):
            svc.request_changes("cp-4", organization_id=cp.organization_id, changes=["fix this"])

    def test_reject_pending_succeeds(self):
        """reject() should work for a valid PENDING checkpoint."""
        from app.services.checkpoint import (
            ApprovalStatus,
            CheckpointData,
            CheckpointService,
        )

        svc = CheckpointService()
        cp = CheckpointData(
            checkpoint_id="cp-5",
            pipeline_run_id="run-5",
        )
        cp.status = ApprovalStatus.PENDING
        svc._checkpoints["cp-5"] = cp

        result = svc.reject("cp-5", organization_id=cp.organization_id, comments="Not good enough")
        assert result.status == ApprovalStatus.REJECTED
        assert result.reviewer_comments == "Not good enough"


# ══════════════════════════════════════════════════════════════════
# 5. PIPELINE CONCURRENT GUARD ORG_ID DEFENSE-IN-DEPTH
# ══════════════════════════════════════════════════════════════════


class TestPipelineConcurrentGuardOrgId:
    """Concurrent run count query must include organization_id."""

    def test_concurrent_guard_includes_org_id(self):
        """Source must filter by organization_id in active run count query."""
        from app.routers import pipeline

        source = inspect.getsource(pipeline.start_pipeline)

        # Find the active_run_q query
        query_start = source.find("active_run_q")
        query_block = source[query_start:query_start + 400]

        assert "organization_id" in query_block, (
            "Concurrent run check must filter by organization_id (defense-in-depth)"
        )


# ══════════════════════════════════════════════════════════════════
# 6. WEBSOCKET BROADCAST TIMEOUT
# ══════════════════════════════════════════════════════════════════


class TestWebSocketBroadcastTimeout:
    """WebSocket broadcast must use asyncio.wait_for to prevent blocking."""

    def test_broadcast_uses_wait_for(self):
        """Source must wrap send_json in asyncio.wait_for with timeout."""
        from app.routers import websocket

        source = inspect.getsource(websocket.ConnectionManager.broadcast)
        assert "wait_for" in source, (
            "broadcast() must use asyncio.wait_for to prevent blocking on slow clients"
        )
        assert "timeout" in source, (
            "broadcast() must specify a timeout for send_json"
        )

    async def test_broadcast_handles_timeout(self):
        """broadcast() must disconnect clients that time out."""
        from app.routers.websocket import ConnectionManager

        mgr = ConnectionManager()

        # Create mock websocket that hangs forever
        slow_ws = AsyncMock()
        slow_ws.send_json = AsyncMock(side_effect=asyncio.TimeoutError)

        # Create mock websocket that works normally
        fast_ws = AsyncMock()
        fast_ws.send_json = AsyncMock()

        mgr._connections["run-1"] = [slow_ws, fast_ws]

        await mgr.broadcast("run-1", {"type": "test"})

        # Fast client should have received the message
        fast_ws.send_json.assert_called_once_with({"type": "test"})

        # Slow client should have been disconnected
        assert slow_ws not in mgr._connections.get("run-1", [])

    async def test_broadcast_logs_disconnects(self):
        """broadcast() must log when it disconnects dead clients."""
        from app.routers import websocket

        source = inspect.getsource(websocket.ConnectionManager.broadcast)
        assert "ws_broadcast_disconnect" in source, (
            "broadcast() must log disconnected clients for observability"
        )


# ══════════════════════════════════════════════════════════════════
# 7. AUDIT LOG IPv6 BRACKET HANDLING
# ══════════════════════════════════════════════════════════════════


class TestAuditLogIPv6Brackets:
    """audit.log_action must handle bracketed IPv6 addresses."""

    def test_source_strips_brackets(self):
        """Source must strip [] from IPv6 addresses before validation."""
        from app.services import audit

        source = inspect.getsource(audit.log_action)
        assert '"[]"' in source or "'[]'" in source, (
            "log_action must strip brackets from IPv6 addresses via .strip('[]')"
        )

    async def test_bracketed_ipv6_preserved(self):
        """Bracketed IPv6 like [::1] should produce ip_address='::1', not None."""
        from app.services.audit import log_action
        from unittest.mock import AsyncMock

        # Mock session — we just want to verify log_action sets ip_address correctly
        mock_session = AsyncMock()

        entry = await log_action(
            mock_session,
            action="test.ipv6_bracket",
            entity_type="test",
            ip_address="[::1]",
        )
        # Should be stored as "::1" (brackets stripped), not None
        assert entry.ip_address == "::1", (
            f"Bracketed IPv6 should be stored as ::1, got: {entry.ip_address}"
        )

    async def test_full_bracketed_ipv6_preserved(self):
        """Bracketed full IPv6 like [2001:db8::1] should work."""
        from app.services.audit import log_action
        from unittest.mock import AsyncMock

        mock_session = AsyncMock()
        entry = await log_action(
            mock_session,
            action="test.ipv6_full",
            entity_type="test",
            ip_address="[2001:db8::1]",
        )
        assert entry.ip_address == "2001:db8::1", (
            f"Bracketed IPv6 should be stored without brackets, got: {entry.ip_address}"
        )

    async def test_invalid_ip_still_dropped(self):
        """Invalid IP addresses should still be set to None."""
        from app.services.audit import log_action
        from unittest.mock import AsyncMock

        mock_session = AsyncMock()
        entry = await log_action(
            mock_session,
            action="test.invalid_ip",
            entity_type="test",
            ip_address="not-an-ip",
        )
        assert entry.ip_address is None, (
            "Invalid IP should be dropped (set to None)"
        )

    def test_regular_ipv4_still_works(self):
        """Plain IPv4 addresses should still pass validation."""
        import ipaddress as _ipaddress
        ip_str = "192.168.1.1"
        cleaned = ip_str.strip().strip("[]")
        _ipaddress.ip_address(cleaned)
        assert cleaned == "192.168.1.1"

    def test_regular_ipv6_still_works(self):
        """Plain IPv6 addresses (no brackets) should still pass."""
        import ipaddress as _ipaddress
        ip_str = "::1"
        cleaned = ip_str.strip().strip("[]")
        _ipaddress.ip_address(cleaned)
        assert cleaned == "::1"


# ══════════════════════════════════════════════════════════════════
# 8. NOTIFICATION DEAD CODE REMOVED
# ══════════════════════════════════════════════════════════════════


class TestNotificationDeadCodeRemoved:
    """Dead _check_rate_limit method should be removed."""

    def test_no_check_rate_limit_method(self):
        """NotificationService should NOT have _check_rate_limit method."""
        from app.services.notification import NotificationService

        assert not hasattr(NotificationService, "_check_rate_limit"), (
            "Dead code _check_rate_limit should be removed from NotificationService"
        )

    def test_check_and_increment_rate_exists(self):
        """The replacement _check_and_increment_rate should still exist."""
        from app.services.notification import NotificationService

        assert hasattr(NotificationService, "_check_and_increment_rate"), (
            "_check_and_increment_rate (the TOCTOU-fix replacement) must exist"
        )


# ══════════════════════════════════════════════════════════════════
# 9. END-TO-END REGRESSION: CHAT + PIPELINE ENDPOINTS
# ══════════════════════════════════════════════════════════════════


class TestEndToEndRegression:
    """Ensure R35 fixes didn't break existing flows."""

    @requires_infra
    async def test_register_login_lifecycle(self, client: AsyncClient):
        """Full register → login → token use flow still works."""
        user = await _register(client, "r35-lifecycle")
        assert "access_token" in user
        assert "refresh_token" in user

        # Use access token
        resp = await client.get("/api/v1/projects/", headers=user["headers"])
        assert resp.status_code == 200

    @requires_infra
    async def test_chat_lifecycle_still_works(self, client: AsyncClient):
        """Chat create session → send message → get messages still works."""
        user = await _register(client, "r35-chat")

        # Create project
        proj_resp = await client.post(
            "/api/v1/projects/",
            headers=user["headers"],
            json={"name": f"R35-ChatTest-{uuid.uuid4().hex[:6]}"},
        )
        assert proj_resp.status_code == 201
        project_id = proj_resp.json()["id"]

        # Create chat session
        session_resp = await client.post(
            f"/api/v1/chat/sessions/{project_id}",
            headers=user["headers"],
        )
        assert session_resp.status_code == 201
        session_id = session_resp.json()["id"]

        # Send message
        msg_resp = await client.post(
            f"/api/v1/chat/session/{session_id}/messages",
            headers=user["headers"],
            json={"content": "R35 test message"},
        )
        assert msg_resp.status_code == 201

        # Get messages
        msgs_resp = await client.get(
            f"/api/v1/chat/session/{session_id}/messages",
            headers=user["headers"],
        )
        assert msgs_resp.status_code == 200
        messages = msgs_resp.json()
        assert len(messages) >= 1
        assert messages[0]["content"] == "R35 test message"

    @requires_infra
    async def test_notifications_endpoint(self, client: AsyncClient):
        """Notifications endpoint returns proper structure."""
        user = await _register(client, "r35-notif")

        resp = await client.get("/api/v1/notifications/", headers=user["headers"])
        assert resp.status_code == 200
        data = resp.json()
        assert "notifications" in data
        assert "total" in data
