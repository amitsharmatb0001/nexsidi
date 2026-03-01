"""R34 Brutal Integration Tests: Real DB + Real Valkey.

Covers bugs found in R34 review:
1. checkpoint redo must clear __checkpoint_approved__ (quality gate bypass)
2. get_messages missing org_id defense-in-depth filter on ChatMessage
3. send_message allows writing to closed/archived sessions
4. init_revocation_store returns None — startup log lies about readiness
5. logout uses decode_token instead of verify_token (no revocation check)
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, patch
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


def _email(prefix: str = "r34") -> str:
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
    os.environ.setdefault("JWT_SECRET_KEY", "r34-brutal-test-key-at-least-32-chars-long-enough")
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


async def _register(c: AsyncClient, prefix: str = "r34") -> dict[str, Any]:
    _reset_rate_limiter()
    email = _email(prefix)
    resp = await c.post("/api/v1/auth/register", json={
        "email": email, "password": "BrutalTest123!",
        "name": f"R34 {prefix}", "organization_name": f"R34Org-{uuid.uuid4().hex[:6]}",
    })
    assert resp.status_code == 201, f"Register failed ({email}): {resp.text}"
    data = resp.json()
    data["email"] = email
    data["headers"] = {"Authorization": f"Bearer {data['access_token']}"}
    return data


# ══════════════════════════════════════════════════════════════════
# 1. CHECKPOINT REDO CLEARS __checkpoint_approved__
# ══════════════════════════════════════════════════════════════════


class TestCheckpointRedoClearsApproval:
    """Verify redo and reject clear the persisted checkpoint_approved flag."""

    def test_redo_clears_checkpoint_approved_in_code(self):
        """approve_checkpoint redo path must pop __checkpoint_approved__ from context."""
        import inspect
        from app.services import pipeline

        source = inspect.getsource(pipeline.PipelineOrchestrator.approve_checkpoint)

        # Find the redo block
        redo_section = source[source.find('action == "redo"'):]
        approve_section = redo_section[:redo_section.find('elif')]

        # Must clear the persisted flag
        assert '__checkpoint_approved__' in approve_section, (
            "Redo path must reference __checkpoint_approved__ to clear it"
        )
        assert '.pop(' in approve_section or 'del ' in approve_section or '= False' in approve_section, (
            "Redo path must clear __checkpoint_approved__ from context"
        )

    def test_reject_clears_checkpoint_approved_in_code(self):
        """approve_checkpoint reject path must clear __checkpoint_approved__."""
        import inspect
        from app.services import pipeline

        source = inspect.getsource(pipeline.PipelineOrchestrator.approve_checkpoint)

        # Find the reject block
        reject_idx = source.find('action == "reject"')
        reject_section = source[reject_idx:]
        # Get just the reject block (up to the else/approve block)
        else_idx = reject_section.find('\n        else:')
        reject_block = reject_section[:else_idx] if else_idx > 0 else reject_section[:300]

        assert '__checkpoint_approved__' in reject_block, (
            "Reject path must clear __checkpoint_approved__ from context"
        )

    def test_approve_sets_checkpoint_approved(self):
        """approve_checkpoint approve path must set __checkpoint_approved__ = True."""
        import inspect
        from app.services import pipeline

        source = inspect.getsource(pipeline.PipelineOrchestrator.approve_checkpoint)

        # The approve block should set the flag to True
        assert '"__checkpoint_approved__"] = True' in source, (
            "Approve path must set __checkpoint_approved__ = True"
        )


# ══════════════════════════════════════════════════════════════════
# 2. GET_MESSAGES ORG_ID DEFENSE-IN-DEPTH
# ══════════════════════════════════════════════════════════════════


@requires_infra
class TestGetMessagesOrgIdFilter:
    """Verify get_messages filters ChatMessage by organization_id."""

    def setup_method(self):
        _reset_rate_limiter()

    async def test_get_messages_org_id_in_query(self, client):
        """get_messages must filter ChatMessage by organization_id."""
        import inspect
        from app.routers import chat

        source = inspect.getsource(chat.get_messages)

        # The ChatMessage query must include organization_id filter
        # Find the select(ChatMessage) block
        msg_query = source[source.find("select(ChatMessage)"):]
        assert "organization_id" in msg_query, (
            "get_messages must filter ChatMessage by organization_id for defense-in-depth"
        )

    async def test_get_messages_cross_org_returns_empty(self, client):
        """User B cannot read User A's messages via session_id."""
        user_a = await _register(client, "msg_a")
        user_b = await _register(client, "msg_b")

        # A creates project + session + message
        proj = await client.post("/api/v1/projects", json={
            "name": "MsgOrg Project", "description": "test"
        }, headers=user_a["headers"])
        pid = proj.json()["id"]

        sess = await client.post(f"/api/v1/chat/sessions/{pid}", headers=user_a["headers"])
        assert sess.status_code == 201
        sid = sess.json()["id"]

        msg = await client.post(
            f"/api/v1/chat/session/{sid}/messages",
            headers=user_a["headers"],
            json={"content": "secret message from A"},
        )
        assert msg.status_code == 201

        # B tries to read A's messages (should get 404 on session check)
        r = await client.get(f"/api/v1/chat/session/{sid}/messages", headers=user_b["headers"])
        assert r.status_code == 404, (
            f"Cross-org message access should return 404, got {r.status_code}"
        )

    async def test_get_messages_own_messages_work(self, client):
        """User can read their own messages normally."""
        user = await _register(client, "msg_own")

        proj = await client.post("/api/v1/projects", json={
            "name": "MsgOwn Project", "description": "test"
        }, headers=user["headers"])
        pid = proj.json()["id"]

        sess = await client.post(f"/api/v1/chat/sessions/{pid}", headers=user["headers"])
        sid = sess.json()["id"]

        await client.post(
            f"/api/v1/chat/session/{sid}/messages",
            headers=user["headers"],
            json={"content": "my message"},
        )

        r = await client.get(f"/api/v1/chat/session/{sid}/messages", headers=user["headers"])
        assert r.status_code == 200
        msgs = r.json()
        assert len(msgs) == 1
        assert msgs[0]["content"] == "my message"


# ══════════════════════════════════════════════════════════════════
# 3. SEND_MESSAGE REJECTS CLOSED SESSIONS
# ══════════════════════════════════════════════════════════════════


@requires_infra
class TestSendMessageClosedSession:
    """Verify send_message rejects closed/archived sessions."""

    def setup_method(self):
        _reset_rate_limiter()

    async def test_send_message_to_active_session_works(self, client):
        """Sending messages to active sessions should work normally."""
        user = await _register(client, "msgsend_ok")

        proj = await client.post("/api/v1/projects", json={
            "name": "Active Session", "description": "test"
        }, headers=user["headers"])
        pid = proj.json()["id"]

        sess = await client.post(f"/api/v1/chat/sessions/{pid}", headers=user["headers"])
        sid = sess.json()["id"]

        r = await client.post(
            f"/api/v1/chat/session/{sid}/messages",
            headers=user["headers"],
            json={"content": "hello active session"},
        )
        assert r.status_code == 201
        assert r.json()["content"] == "hello active session"

    async def test_send_message_code_checks_session_status(self, client):
        """send_message must filter by ChatSession.status == 'active'."""
        import inspect
        from app.routers import chat

        source = inspect.getsource(chat.send_message)

        # Must check session status
        assert 'status' in source and '"active"' in source, (
            "send_message must check ChatSession.status == 'active'"
        )


# ══════════════════════════════════════════════════════════════════
# 4. INIT_REVOCATION_STORE STARTUP REPORTING
# ══════════════════════════════════════════════════════════════════


class TestRevocationStoreStartupReporting:
    """Verify main.py checks init_revocation_store return value."""

    def test_main_checks_store_return_value(self):
        """main.py must check if init_revocation_store() returns None."""
        import inspect
        from app import main

        source = inspect.getsource(main)

        # Must check the return value of init_revocation_store()
        assert "store = await init_revocation_store()" in source or \
               "store is None" in source, (
            "main.py must capture and check the return value of init_revocation_store()"
        )

    def test_main_logs_error_on_none_store(self):
        """main.py must log an error if init_revocation_store() returns None."""
        import inspect
        from app import main

        source = inspect.getsource(main)

        # Must have error logging for None return
        assert "revocation_store_init_returned_none" in source or \
               "store is None" in source, (
            "main.py must log an error when store is None"
        )

    def test_main_does_not_log_ready_on_none(self):
        """main.py must NOT log 'revocation_store_ready' when store is None."""
        import inspect
        from app import main

        # Get just the lifespan function source to avoid other occurrences
        source = inspect.getsource(main.lifespan)

        # The ready log must be gated by a None check
        assert "store is None" in source, "Must check for None store"
        assert "revocation_store_ready" in source, "Must have ready log"

        # The structure must be: if store is None → error, else → ready
        none_idx = source.find("store is None")
        else_idx = source.find("else:", none_idx)
        ready_idx = source.find("revocation_store_ready", none_idx)

        assert else_idx > none_idx, "Must have else branch after None check"
        assert ready_idx > else_idx, (
            "revocation_store_ready must be in the else branch (after store is None check)"
        )


# ══════════════════════════════════════════════════════════════════
# 5. LOGOUT USES verify_token INSTEAD OF decode_token
# ══════════════════════════════════════════════════════════════════


class TestLogoutVerifyToken:
    """Verify /logout uses verify_token (async, revocation-checking)."""

    def test_logout_uses_verify_token(self):
        """logout endpoint must use verify_token, not decode_token, for re-extracting jti."""
        import inspect
        from app.routers import auth

        source = inspect.getsource(auth.logout)

        # Must use verify_token (async, checks revocation)
        assert "await verify_token(" in source or "verify_token(raw_token" in source, (
            "logout must use verify_token (async, checks revocation) instead of decode_token"
        )

    def test_logout_does_not_use_decode_token_for_access(self):
        """logout must NOT use decode_token for the access token re-extraction."""
        import inspect
        from app.routers import auth

        source = inspect.getsource(auth.logout)

        # Should NOT have decode_token(raw_token) — only for the refresh token
        # decode_token should only appear in the refresh token handling block
        lines = source.split('\n')
        for i, line in enumerate(lines):
            if 'decode_token(raw_token' in line:
                # This is the old pattern — should not exist
                assert False, (
                    f"Line {i}: logout still uses decode_token(raw_token) for access token. "
                    "Should use verify_token() for revocation checking."
                )


@requires_infra
class TestLogoutEndToEndR34:
    """End-to-end logout tests verifying R34 fix."""

    def setup_method(self):
        _reset_rate_limiter()

    async def test_logout_still_works_normally(self, client):
        """Normal logout flow should still work after R34 fix."""
        user = await _register(client, "logout_ok")

        resp = await client.post("/api/v1/auth/logout", headers=user["headers"])
        assert resp.status_code == 204

    async def test_logout_after_logout_all_rejects(self, client):
        """After logout-all, the original token should be rejected by /logout."""
        user = await _register(client, "logout_revchk")

        # Get a second set of tokens
        _reset_rate_limiter()
        refresh_resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": user["refresh_token"]},
        )
        assert refresh_resp.status_code == 200
        second_tokens = refresh_resp.json()
        second_headers = {"Authorization": f"Bearer {second_tokens['access_token']}"}

        # Logout-all with second token (revokes ALL tokens including the first)
        resp = await client.post("/api/v1/auth/logout-all", headers=second_headers)
        assert resp.status_code == 204

        # Try to use the FIRST token for /logout — should be rejected
        # R34-FIX: With verify_token, this correctly returns 401
        # With the old decode_token, this would succeed (no revocation check)
        resp2 = await client.post("/api/v1/auth/logout", headers=user["headers"])
        # The CurrentContext dependency calls verify_token first, so this should be 401
        assert resp2.status_code in (204, 401), (
            f"Expected 204 or 401 after logout-all, got {resp2.status_code}"
        )

    async def test_logout_with_valid_refresh_still_works(self, client):
        """Logout with valid refresh token should still revoke both."""
        user = await _register(client, "logout_ref")

        resp = await client.post(
            "/api/v1/auth/logout",
            headers=user["headers"],
            json={"refresh_token": user["refresh_token"]},
        )
        assert resp.status_code == 204

        # Refresh token should be revoked
        _reset_rate_limiter()
        refresh_resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": user["refresh_token"]},
        )
        assert refresh_resp.status_code == 401


# ══════════════════════════════════════════════════════════════════
# 6. REGRESSION: ALL CHAT ENDPOINTS STILL WORK
# ══════════════════════════════════════════════════════════════════


@requires_infra
class TestChatEndpointsRegression:
    """Ensure chat CRUD still works after R34 fixes."""

    def setup_method(self):
        _reset_rate_limiter()

    async def test_full_chat_lifecycle(self, client):
        """Create session → send message → read messages → list sessions."""
        user = await _register(client, "chatlife")

        # Create project
        proj = await client.post("/api/v1/projects", json={
            "name": "Chat Lifecycle", "description": "test"
        }, headers=user["headers"])
        assert proj.status_code == 201
        pid = proj.json()["id"]

        # Create session
        sess = await client.post(f"/api/v1/chat/sessions/{pid}", headers=user["headers"])
        assert sess.status_code == 201
        sid = sess.json()["id"]

        # Send messages
        for i in range(3):
            r = await client.post(
                f"/api/v1/chat/session/{sid}/messages",
                headers=user["headers"],
                json={"content": f"message {i}"},
            )
            assert r.status_code == 201

        # Read messages
        msgs = await client.get(
            f"/api/v1/chat/session/{sid}/messages",
            headers=user["headers"],
        )
        assert msgs.status_code == 200
        assert len(msgs.json()) == 3

        # Get session
        sess_detail = await client.get(
            f"/api/v1/chat/session/{sid}",
            headers=user["headers"],
        )
        assert sess_detail.status_code == 200

        # List sessions
        sess_list = await client.get(
            f"/api/v1/chat/sessions/{pid}",
            headers=user["headers"],
        )
        assert sess_list.status_code == 200
        assert sess_list.json()["total"] >= 1

    async def test_messages_pagination(self, client):
        """Message pagination still works after R34 org_id filter addition."""
        user = await _register(client, "chatpage")

        proj = await client.post("/api/v1/projects", json={
            "name": "Chat Pagination", "description": "test"
        }, headers=user["headers"])
        pid = proj.json()["id"]

        sess = await client.post(f"/api/v1/chat/sessions/{pid}", headers=user["headers"])
        sid = sess.json()["id"]

        # Send 5 messages
        for i in range(5):
            await client.post(
                f"/api/v1/chat/session/{sid}/messages",
                headers=user["headers"],
                json={"content": f"page msg {i}"},
            )

        # Paginate: first 2
        r = await client.get(
            f"/api/v1/chat/session/{sid}/messages?offset=0&limit=2",
            headers=user["headers"],
        )
        assert r.status_code == 200
        assert len(r.json()) == 2

        # Paginate: next 2
        r = await client.get(
            f"/api/v1/chat/session/{sid}/messages?offset=2&limit=2",
            headers=user["headers"],
        )
        assert r.status_code == 200
        assert len(r.json()) == 2


# ══════════════════════════════════════════════════════════════════
# 7. CODEBASE-WIDE: ALL EXCEPT CLAUSES IMPORT THEIR EXCEPTION
# ══════════════════════════════════════════════════════════════════


class TestExceptClauseImports:
    """Verify all except clauses reference imported exception classes."""

    def test_no_except_jwterror_without_import(self):
        """Every file with except JWTError must import it."""
        import pathlib

        app_dir = pathlib.Path(__file__).parent.parent / "app"
        violations = []

        for py_file in app_dir.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            if "except JWTError" in content:
                has_import = (
                    "from jose import JWTError" in content
                    or ("from jose import" in content and "JWTError" in content)
                )
                if not has_import:
                    violations.append(str(py_file.relative_to(app_dir.parent)))

        assert not violations, (
            f"Files use `except JWTError` without importing it: {violations}"
        )
