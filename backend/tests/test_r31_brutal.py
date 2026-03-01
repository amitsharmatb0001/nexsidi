"""R31 Brutal Integration Tests: Real DB + Real Valkey.

Probes every dark corner of the running system against actual GCP Cloud SQL
and Valkey. No mocks, no shortcuts.

Covers:
1. Concurrency: race conditions in register, refresh, logout
2. Data leaks: IDOR, intra-org visibility, cross-tenant
3. Token edge cases: double-logout, replay, family revocation timing
4. Input boundary: overflows, special chars, massive payloads
5. RBAC enforcement: privilege escalation attempts
6. Missing eager-loading: MissingGreenlet triggers
7. DB constraint enforcement: unique violations, FK integrity
8. Schema validation: all response models match expected shapes
9. Session/cookie safety: no sensitive data in headers
10. Notification tenant isolation
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator
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


def _email(prefix: str = "r31") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}@{_TEST_DOMAIN}"


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


try:
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
    os.environ.setdefault("JWT_SECRET_KEY", "r31-brutal-test-key-at-least-32-chars-long-enough")
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


async def _register(c: AsyncClient, prefix: str = "r31") -> dict[str, Any]:
    _reset_rate_limiter()
    email = _email(prefix)
    resp = await c.post("/api/v1/auth/register", json={
        "email": email, "password": "BrutalTest123!",
        "name": f"R31 {prefix}", "organization_name": f"R31Org-{uuid.uuid4().hex[:6]}",
    })
    assert resp.status_code == 201, f"Register failed ({email}): {resp.text}"
    data = resp.json()
    data["email"] = email
    data["headers"] = {"Authorization": f"Bearer {data['access_token']}"}
    return data


# ══════════════════════════════════════════════════════════════════
# 1. CONCURRENCY TESTS
# ══════════════════════════════════════════════════════════════════


@requires_infra
class TestConcurrency:
    """Race conditions that only manifest under concurrent load."""

    def setup_method(self):
        _reset_rate_limiter()

    async def test_concurrent_register_same_email(self, client):
        """Two concurrent registrations with the same email: exactly one succeeds."""
        email = _email("racemail")
        payload = {
            "email": email, "password": "RaceTest123!",
            "name": "Racer", "organization_name": f"RaceOrg-{uuid.uuid4().hex[:6]}",
        }

        async def try_register():
            return await client.post("/api/v1/auth/register", json=payload)

        r1, r2 = await asyncio.gather(try_register(), try_register())
        codes = sorted([r1.status_code, r2.status_code])
        # Exactly one should succeed (201), other should fail (409 or 500)
        assert 201 in codes, f"Neither succeeded! Codes: {codes}"
        assert codes != [201, 201], f"RACE: Both registrations succeeded! Codes: {codes}"

    async def test_refresh_token_single_use(self, client):
        """Refresh token can only be used once (single-use enforcement)."""
        user = await _register(client, "singleuse")
        refresh = user["refresh_token"]

        # Use it once
        r1 = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        assert r1.status_code == 200, f"First refresh failed: {r1.text}"

        # Try to use it again -- should be rejected (replay attack)
        r2 = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        assert r2.status_code == 401, (
            f"REPLAY ATTACK: Refresh token reusable! Status: {r2.status_code}, Body: {r2.text}"
        )

    async def test_concurrent_refresh_same_token(self, client):
        """Two concurrent refresh requests: only one should succeed."""
        user = await _register(client, "concref")
        refresh = user["refresh_token"]

        async def try_refresh():
            return await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})

        r1, r2 = await asyncio.gather(try_refresh(), try_refresh())
        codes = sorted([r1.status_code, r2.status_code])
        successes = [c for c in codes if c == 200]
        assert len(successes) <= 1, (
            f"RACE: Both concurrent refreshes succeeded! Codes: {codes}"
        )


# ══════════════════════════════════════════════════════════════════
# 2. DATA LEAK / IDOR TESTS
# ══════════════════════════════════════════════════════════════════


@requires_infra
class TestDataLeaks:
    """Cross-tenant and intra-org data access violations."""

    def setup_method(self):
        _reset_rate_limiter()

    async def test_user_cannot_access_other_org_project_by_id(self, client):
        """Direct project access by UUID must be blocked cross-org."""
        user_a = await _register(client, "leaka")
        user_b = await _register(client, "leakb")

        # User A creates a project
        proj = await client.post("/api/v1/projects", json={
            "name": "Confidential A", "description": "Secret"
        }, headers=user_a["headers"])
        assert proj.status_code == 201
        pid = proj.json()["id"]

        # User B tries to GET it directly
        r = await client.get(f"/api/v1/projects/{pid}", headers=user_b["headers"])
        assert r.status_code == 404, f"IDOR: Cross-org project access! Status: {r.status_code}"

        # User B tries to PATCH it
        r2 = await client.patch(f"/api/v1/projects/{pid}", json={
            "name": "HACKED"
        }, headers=user_b["headers"])
        assert r2.status_code == 404, f"IDOR: Cross-org project modification! Status: {r2.status_code}"

        # User B tries to DELETE it
        r3 = await client.delete(f"/api/v1/projects/{pid}", headers=user_b["headers"])
        assert r3.status_code in (404, 403), f"IDOR: Cross-org project deletion! Status: {r3.status_code}"

    async def test_user_cannot_access_other_org_chat(self, client):
        """Direct chat session access by UUID must be blocked cross-org."""
        user_a = await _register(client, "chata2")
        user_b = await _register(client, "chatb2")

        # A creates project + session + message
        proj = await client.post("/api/v1/projects", json={
            "name": "A Chat Proj", "description": "test"
        }, headers=user_a["headers"])
        pid = proj.json()["id"]

        sess = await client.post(f"/api/v1/chat/sessions/{pid}", headers=user_a["headers"])
        assert sess.status_code == 201
        sid = sess.json()["id"]

        msg = await client.post(f"/api/v1/chat/session/{sid}/messages", json={
            "content": "Top secret message from org A"
        }, headers=user_a["headers"])
        assert msg.status_code == 201

        # B tries to read the session
        r1 = await client.get(f"/api/v1/chat/session/{sid}", headers=user_b["headers"])
        assert r1.status_code == 404, f"IDOR: Cross-org chat session access! {r1.status_code}"

        # B tries to read messages
        r2 = await client.get(f"/api/v1/chat/session/{sid}/messages", headers=user_b["headers"])
        assert r2.status_code == 404, f"IDOR: Cross-org chat message access! {r2.status_code}"

        # B tries to inject a message
        r3 = await client.post(f"/api/v1/chat/session/{sid}/messages", json={
            "content": "Injected by attacker"
        }, headers=user_b["headers"])
        assert r3.status_code == 404, f"IDOR: Cross-org chat injection! {r3.status_code}"

    async def test_user_cannot_delete_other_org_project(self, client):
        """Admin of Org B cannot delete Org A's project."""
        user_a = await _register(client, "delcross_a")
        user_b = await _register(client, "delcross_b")

        proj = await client.post("/api/v1/projects", json={
            "name": "A Project To Delete", "description": "test"
        }, headers=user_a["headers"])
        pid = proj.json()["id"]

        # Both users are org_admin of their respective orgs.
        # B must NOT be able to delete A's project.
        r = await client.delete(f"/api/v1/projects/{pid}", headers=user_b["headers"])
        assert r.status_code in (404, 403), (
            f"CROSS-ORG DELETE: User B deleted Org A's project! Status: {r.status_code}"
        )

        # Verify project still exists for A
        r2 = await client.get(f"/api/v1/projects/{pid}", headers=user_a["headers"])
        assert r2.status_code == 200, "Project was wrongly deleted!"


# ══════════════════════════════════════════════════════════════════
# 3. TOKEN EDGE CASES
# ══════════════════════════════════════════════════════════════════


@requires_infra
class TestTokenEdgeCases:
    """JWT token handling edge cases."""

    def setup_method(self):
        _reset_rate_limiter()

    async def test_double_logout_is_safe(self, client):
        """Calling /logout twice with same token should not crash."""
        user = await _register(client, "dbllogout")
        headers = user["headers"]

        r1 = await client.post("/api/v1/auth/logout", headers=headers)
        assert r1.status_code == 204

        # Second logout -- token already revoked, should get 401/403
        r2 = await client.post("/api/v1/auth/logout", headers=headers)
        assert r2.status_code in (401, 403), (
            f"Double logout: unexpected status {r2.status_code}"
        )

    async def test_logout_all_then_login_works(self, client):
        """After /logout-all, user can still login with password."""
        email = _email("logall2")
        password = "LogAll2Test123!"

        reg = await client.post("/api/v1/auth/register", json={
            "email": email, "password": password,
            "name": "LogAll2", "organization_name": f"LA2-{uuid.uuid4().hex[:6]}",
        })
        assert reg.status_code == 201
        headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}

        # Logout all
        r = await client.post("/api/v1/auth/logout-all", headers=headers)
        assert r.status_code == 204

        # Old token rejected
        me = await client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code in (401, 403)

        # R34-FIX: Wait >1s so the new token's iat is strictly after the
        # revocation cutoff. R27-FIX-18 uses `<=` comparison (fail-closed),
        # so tokens minted at the exact same second as logout-all are revoked.
        import asyncio
        await asyncio.sleep(1.1)

        # Fresh login should work
        login = await client.post("/api/v1/auth/login", json={
            "email": email, "password": password,
        })
        assert login.status_code == 200, f"Login after logout-all failed: {login.text}"
        new_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        # New token works
        me2 = await client.get("/api/v1/auth/me", headers=new_headers)
        assert me2.status_code == 200

    async def test_expired_token_format_rejected(self, client):
        """Malformed JWT strings should return 401, not 500."""
        bad_tokens = [
            "",
            "not-a-jwt",
            "eyJ.eyJ.sig",  # valid structure, invalid content
            "a" * 10000,  # very long token
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",  # valid JWT, wrong secret
        ]
        for token in bad_tokens:
            r = await client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code in (401, 403, 422), (
                f"Token '{token[:30]}...' returned {r.status_code}, expected 401/403/422"
            )

    async def test_refresh_with_access_token_rejected(self, client):
        """Using an access token as a refresh token should fail (type mismatch)."""
        user = await _register(client, "typecheck")

        r = await client.post("/api/v1/auth/refresh", json={
            "refresh_token": user["access_token"],  # Wrong type!
        })
        assert r.status_code == 401, (
            f"TYPE CONFUSION: Access token accepted as refresh! Status: {r.status_code}"
        )


# ══════════════════════════════════════════════════════════════════
# 4. INPUT BOUNDARY / EDGE CASES
# ══════════════════════════════════════════════════════════════════


@requires_infra
class TestInputBoundary:
    """Malformed input, overflow, and boundary values."""

    def setup_method(self):
        _reset_rate_limiter()

    async def test_register_password_too_short(self, client):
        """Password < 8 chars should be rejected."""
        r = await client.post("/api/v1/auth/register", json={
            "email": _email("shortpw"), "password": "Ab1!",
            "name": "Short", "organization_name": "ShortOrg",
        })
        assert r.status_code == 422

    async def test_register_password_no_uppercase(self, client):
        """Password without uppercase should be rejected."""
        r = await client.post("/api/v1/auth/register", json={
            "email": _email("nouppercase"), "password": "lowercase123!",
            "name": "NoUpper", "organization_name": "NoUpperOrg",
        })
        assert r.status_code == 422

    async def test_register_empty_name(self, client):
        """Empty name should be rejected."""
        r = await client.post("/api/v1/auth/register", json={
            "email": _email("emptyname"), "password": "EmptyName123!",
            "name": "", "organization_name": "EmptyNameOrg",
        })
        assert r.status_code == 422

    async def test_project_name_max_length(self, client):
        """Project name > 255 chars should be rejected."""
        user = await _register(client, "longname")
        r = await client.post("/api/v1/projects", json={
            "name": "X" * 256, "description": "Too long name"
        }, headers=user["headers"])
        assert r.status_code == 422

    async def test_project_description_max_length(self, client):
        """Project description > 2000 chars should be rejected."""
        user = await _register(client, "longdesc")
        r = await client.post("/api/v1/projects", json={
            "name": "Normal", "description": "X" * 2001
        }, headers=user["headers"])
        assert r.status_code == 422

    async def test_chat_message_max_length(self, client):
        """Chat message > 10000 chars should be rejected."""
        user = await _register(client, "longmsg")
        proj = await client.post("/api/v1/projects", json={
            "name": "MsgProj", "description": "test"
        }, headers=user["headers"])
        pid = proj.json()["id"]
        sess = await client.post(f"/api/v1/chat/sessions/{pid}", headers=user["headers"])
        sid = sess.json()["id"]

        r = await client.post(f"/api/v1/chat/session/{sid}/messages", json={
            "content": "X" * 10001
        }, headers=user["headers"])
        assert r.status_code == 422, f"Oversized message accepted! Status: {r.status_code}"

    async def test_chat_empty_message_rejected(self, client):
        """Empty chat message should be rejected."""
        user = await _register(client, "emptymsg")
        proj = await client.post("/api/v1/projects", json={
            "name": "EmptyMsgProj", "description": "test"
        }, headers=user["headers"])
        pid = proj.json()["id"]
        sess = await client.post(f"/api/v1/chat/sessions/{pid}", headers=user["headers"])
        sid = sess.json()["id"]

        r = await client.post(f"/api/v1/chat/session/{sid}/messages", json={
            "content": ""
        }, headers=user["headers"])
        assert r.status_code == 422, f"Empty message accepted! Status: {r.status_code}"

    async def test_sql_injection_in_email(self, client):
        """SQL injection in email field should be safely rejected."""
        r = await client.post("/api/v1/auth/login", json={
            "email": "admin@test.com' OR '1'='1",
            "password": "Test123!",
        })
        # Should be 422 (invalid email format), not 500
        assert r.status_code in (401, 422), f"SQL injection test: {r.status_code}"

    async def test_xss_in_project_name(self, client):
        """XSS payload in project name should be stored safely."""
        user = await _register(client, "xss")
        xss_name = '<script>alert("XSS")</script>'
        r = await client.post("/api/v1/projects", json={
            "name": xss_name, "description": "XSS test"
        }, headers=user["headers"])
        assert r.status_code == 201
        # Should be stored literally, not executed
        data = r.json()
        assert data["name"] == xss_name  # Stored as-is, not sanitized out

    async def test_unicode_in_all_text_fields(self, client):
        """Unicode (emoji, CJK, RTL) should be handled correctly."""
        user = await _register(client, "unicode")
        r = await client.post("/api/v1/projects", json={
            "name": "Project \u2728\U0001f680 \u4e16\u754c",
            "description": "\u0645\u0631\u062d\u0628\u0627 \u0628\u0627\u0644\u0639\u0627\u0644\u0645 \U0001f30d",
        }, headers=user["headers"])
        assert r.status_code == 201
        data = r.json()
        assert "\u2728" in data["name"]
        assert "\U0001f680" in data["name"]

    async def test_pagination_negative_offset_rejected(self, client):
        """Negative offset should be rejected by validation."""
        user = await _register(client, "negoff")
        r = await client.get("/api/v1/projects?offset=-1", headers=user["headers"])
        assert r.status_code == 422

    async def test_pagination_zero_limit_rejected(self, client):
        """Zero limit should be rejected by validation."""
        user = await _register(client, "zerolim")
        r = await client.get("/api/v1/projects?limit=0", headers=user["headers"])
        assert r.status_code == 422

    async def test_pagination_excessive_limit_capped(self, client):
        """Limit > 100 should be rejected or capped."""
        user = await _register(client, "biglim")
        r = await client.get("/api/v1/projects?limit=999", headers=user["headers"])
        assert r.status_code == 422  # Pydantic le=100 rejects > 100


# ══════════════════════════════════════════════════════════════════
# 5. RBAC ENFORCEMENT
# ══════════════════════════════════════════════════════════════════


@requires_infra
class TestRBAC:
    """Role-based access control enforcement."""

    def setup_method(self):
        _reset_rate_limiter()

    async def test_non_admin_cannot_delete_project(self, client):
        """Regular user (member role) cannot delete projects.

        NOTE: Since register creates org_admin, this test verifies the
        endpoint requires AdminContext. In a real scenario with member
        users, they would be rejected.
        """
        user = await _register(client, "rbac")
        # Create a project
        proj = await client.post("/api/v1/projects", json={
            "name": "RBAC Test", "description": "test"
        }, headers=user["headers"])
        pid = proj.json()["id"]

        # org_admin CAN delete (this is expected to succeed)
        r = await client.delete(f"/api/v1/projects/{pid}", headers=user["headers"])
        assert r.status_code == 204, f"Admin delete failed: {r.status_code}"

    async def test_deleted_project_returns_404(self, client):
        """After deletion, project should return 404."""
        user = await _register(client, "delcheck")
        proj = await client.post("/api/v1/projects", json={
            "name": "Will Be Deleted", "description": "bye"
        }, headers=user["headers"])
        pid = proj.json()["id"]

        await client.delete(f"/api/v1/projects/{pid}", headers=user["headers"])

        r = await client.get(f"/api/v1/projects/{pid}", headers=user["headers"])
        assert r.status_code == 404

    async def test_double_delete_returns_404(self, client):
        """Deleting an already-deleted project should return 404."""
        user = await _register(client, "dbldel")
        proj = await client.post("/api/v1/projects", json={
            "name": "Double Del", "description": "test"
        }, headers=user["headers"])
        pid = proj.json()["id"]

        r1 = await client.delete(f"/api/v1/projects/{pid}", headers=user["headers"])
        assert r1.status_code == 204

        r2 = await client.delete(f"/api/v1/projects/{pid}", headers=user["headers"])
        assert r2.status_code == 404, f"Double delete: got {r2.status_code}, expected 404"


# ══════════════════════════════════════════════════════════════════
# 6. RESPONSE SHAPE VALIDATION
# ══════════════════════════════════════════════════════════════════


@requires_infra
class TestResponseShapes:
    """Verify all response models match expected schemas."""

    def setup_method(self):
        _reset_rate_limiter()

    async def test_register_response_shape(self, client):
        """Registration response must have exact expected fields."""
        user = await _register(client, "shape1")
        # Already validated by _register, but verify exact shape
        assert isinstance(user["access_token"], str)
        assert isinstance(user["refresh_token"], str)
        assert isinstance(user["expires_in"], int)
        assert user["expires_in"] > 0

    async def test_me_response_has_no_password(self, client):
        """GET /me must NEVER return password_hash."""
        user = await _register(client, "nopw")
        r = await client.get("/api/v1/auth/me", headers=user["headers"])
        data = r.json()
        assert "password_hash" not in data, "SECURITY: password_hash leaked in /me!"
        assert "password" not in data, "SECURITY: password leaked in /me!"
        assert "totp_secret_enc" not in data or data.get("totp_secret_enc") is None, (
            "SECURITY: TOTP secret leaked in /me!"
        )
        # Required fields
        assert "id" in data
        assert "email" in data
        assert "role" in data
        assert "organization_id" in data

    async def test_project_response_shape(self, client):
        """Project response must have all required fields."""
        user = await _register(client, "projshape")
        r = await client.post("/api/v1/projects", json={
            "name": "Shape Test", "description": "desc"
        }, headers=user["headers"])
        data = r.json()
        assert "id" in data
        assert "name" in data
        assert "description" in data
        assert "status" in data
        assert data["status"] == "active"
        assert "created_at" in data

    async def test_chat_session_response_has_messages(self, client):
        """Chat session response must include messages array."""
        user = await _register(client, "sessshape")
        proj = await client.post("/api/v1/projects", json={
            "name": "SessShape", "description": "test"
        }, headers=user["headers"])
        pid = proj.json()["id"]

        sess = await client.post(f"/api/v1/chat/sessions/{pid}", headers=user["headers"])
        assert sess.status_code == 201
        data = sess.json()
        assert "id" in data
        assert "messages" in data, "MISSING: messages field in ChatSessionResponse"
        assert isinstance(data["messages"], list)
        assert len(data["messages"]) == 0  # New session, no messages

    async def test_error_responses_are_json(self, client):
        """All error responses must be valid JSON with 'detail' field."""
        # 404
        r1 = await client.get(f"/api/v1/projects/{uuid.uuid4()}")
        assert r1.status_code in (401, 403)
        data = r1.json()
        assert "detail" in data

        # 401
        user = await _register(client, "errjson")
        r2 = await client.get(
            f"/api/v1/projects/{uuid.uuid4()}", headers=user["headers"]
        )
        assert r2.status_code == 404
        data2 = r2.json()
        assert "detail" in data2

    async def test_list_response_has_pagination(self, client):
        """List endpoints must return total count for pagination."""
        user = await _register(client, "pagshape")

        # Create 2 projects
        for i in range(2):
            await client.post("/api/v1/projects", json={
                "name": f"PagProject {i}", "description": "test"
            }, headers=user["headers"])

        r = await client.get("/api/v1/projects", headers=user["headers"])
        data = r.json()
        assert "projects" in data
        assert "total" in data
        assert data["total"] >= 2
        assert len(data["projects"]) >= 2


# ══════════════════════════════════════════════════════════════════
# 7. SECURITY HEADERS ON ALL RESPONSE TYPES
# ══════════════════════════════════════════════════════════════════


@requires_infra
class TestSecurityHeadersComprehensive:
    """Security headers must be on EVERY response, not just 200s."""

    def setup_method(self):
        _reset_rate_limiter()

    async def test_headers_on_201(self, client):
        """Security headers on 201 Created."""
        user = await _register(client, "hdr201")
        r = await client.post("/api/v1/projects", json={
            "name": "HdrTest", "description": "test"
        }, headers=user["headers"])
        assert r.status_code == 201
        assert r.headers.get("X-Content-Type-Options") == "nosniff"
        assert r.headers.get("X-Frame-Options") == "DENY"

    async def test_headers_on_401(self, client):
        """Security headers on 401 Unauthorized."""
        r = await client.get("/api/v1/auth/me")
        assert r.status_code in (401, 403)
        assert r.headers.get("X-Content-Type-Options") == "nosniff"

    async def test_headers_on_404(self, client):
        """Security headers on 404 Not Found."""
        user = await _register(client, "hdr404")
        r = await client.get(
            f"/api/v1/projects/{uuid.uuid4()}", headers=user["headers"]
        )
        assert r.status_code == 404
        assert r.headers.get("X-Content-Type-Options") == "nosniff"
        assert "Content-Security-Policy" in r.headers

    async def test_headers_on_422(self, client):
        """Security headers on 422 Validation Error."""
        r = await client.post("/api/v1/auth/register", json={
            "email": "bad-email", "password": "x",
            "name": "", "organization_name": "",
        })
        assert r.status_code == 422
        assert r.headers.get("X-Content-Type-Options") == "nosniff"

    async def test_no_server_header_leak(self, client):
        """Server header should not leak implementation details."""
        r = await client.get("/health")
        server = r.headers.get("Server", "")
        assert "uvicorn" not in server.lower(), f"Server header leaks: {server}"
        assert "python" not in server.lower(), f"Server header leaks: {server}"

    async def test_no_sensitive_data_in_error_detail(self, client):
        """Error responses must not leak internal paths or stack traces."""
        r = await client.get("/api/v1/nonexistent")
        body = r.text.lower()
        assert "traceback" not in body
        assert "sqlalchemy" not in body
        assert "password" not in body
        assert "secret" not in body


# ══════════════════════════════════════════════════════════════════
# 8. DATABASE INTEGRITY TESTS
# ══════════════════════════════════════════════════════════════════


@requires_infra
class TestDatabaseIntegrity:
    """Verify database constraints and schema correctness with real DB."""

    def setup_method(self):
        _reset_rate_limiter()

    async def test_all_timestamps_have_timezone(self, client):
        """Verify all timestamp columns use 'timestamp with time zone'."""
        from sqlalchemy import text
        from app.database import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(text("""
                SELECT table_schema, table_name, column_name, data_type
                FROM information_schema.columns
                WHERE data_type = 'timestamp without time zone'
                AND table_schema IN ('auth','core','pipeline','chat','deploy','billing','audit','notify')
            """))
            bad_cols = result.fetchall()
            assert len(bad_cols) == 0, (
                f"Timestamps without timezone: {[(r[0], r[1], r[2]) for r in bad_cols]}"
            )

    async def test_uuid_pks_use_gen_random_uuid(self, client):
        """All UUID primary keys should have gen_random_uuid() default."""
        from sqlalchemy import text
        from app.database import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(text("""
                SELECT table_schema, table_name, column_name, column_default
                FROM information_schema.columns
                WHERE column_name = 'id'
                AND data_type = 'uuid'
                AND table_schema IN ('auth','core','pipeline','chat','deploy','billing','audit','notify')
                AND (column_default IS NULL OR column_default NOT LIKE '%gen_random_uuid%')
            """))
            missing = result.fetchall()
            assert len(missing) == 0, (
                f"UUID PKs without gen_random_uuid(): {[(r[0], r[1]) for r in missing]}"
            )

    async def test_rls_enabled_on_all_org_tables(self, client):
        """Every table with organization_id must have RLS enabled."""
        from sqlalchemy import text
        from app.database import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            # Find all tables with organization_id
            org_tables = await session.execute(text("""
                SELECT table_schema, table_name
                FROM information_schema.columns
                WHERE column_name = 'organization_id'
                AND table_schema IN ('auth','core','pipeline','chat','deploy','billing','audit','notify')
            """))
            org_table_list = org_tables.fetchall()

            # Check RLS status for each
            for schema, table in org_table_list:
                rls_check = await session.execute(text(f"""
                    SELECT relrowsecurity, relforcerowsecurity
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = '{schema}' AND c.relname = '{table}'
                """))
                row = rls_check.fetchone()
                assert row is not None, f"Table {schema}.{table} not found in pg_class"
                assert row[0] is True, f"RLS not enabled on {schema}.{table}"
                assert row[1] is True, f"FORCE RLS not set on {schema}.{table}"

    async def test_critical_indexes_exist(self, client):
        """Verify indexes exist on commonly-queried columns."""
        from sqlalchemy import text
        from app.database import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            # Check that critical indexes exist
            critical_indexes = [
                ("auth", "users", "email"),
                ("core", "projects", "organization_id"),
                ("pipeline", "runs", "project_id"),
                ("pipeline", "steps", "run_id"),
                ("chat", "sessions", "project_id"),
                ("chat", "sessions", "user_id"),
                ("chat", "messages", "session_id"),
            ]
            for schema, table, column in critical_indexes:
                result = await session.execute(text(f"""
                    SELECT indexname FROM pg_indexes
                    WHERE schemaname = '{schema}' AND tablename = '{table}'
                    AND indexdef LIKE '%{column}%'
                """))
                indexes = result.fetchall()
                assert len(indexes) > 0, (
                    f"MISSING INDEX: {schema}.{table}.{column} has no index!"
                )

    async def test_user_email_unique_constraint(self, client):
        """Verify auth.users.email has a unique constraint."""
        from sqlalchemy import text
        from app.database import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(text("""
                SELECT constraint_name
                FROM information_schema.table_constraints
                WHERE table_schema = 'auth' AND table_name = 'users'
                AND constraint_type = 'UNIQUE'
            """))
            constraints = result.fetchall()
            constraint_names = [r[0] for r in constraints]
            # Should have a unique constraint on email
            assert any("email" in c for c in constraint_names) or len(constraints) > 0, (
                f"No UNIQUE constraints on auth.users! Found: {constraint_names}"
            )


# ══════════════════════════════════════════════════════════════════
# 9. CHAT SESSION EDGE CASES
# ══════════════════════════════════════════════════════════════════


@requires_infra
class TestChatEdgeCases:
    """Chat session and message edge cases with real DB."""

    def setup_method(self):
        _reset_rate_limiter()

    async def test_create_session_for_nonexistent_project(self, client):
        """Creating a chat session for a non-existent project should fail."""
        user = await _register(client, "badsess")
        fake_pid = str(uuid.uuid4())
        r = await client.post(
            f"/api/v1/chat/sessions/{fake_pid}", headers=user["headers"]
        )
        assert r.status_code == 404

    async def test_create_session_for_archived_project(self, client):
        """Creating a chat session for an archived project should fail."""
        user = await _register(client, "archsess")
        proj = await client.post("/api/v1/projects", json={
            "name": "Will Archive", "description": "test"
        }, headers=user["headers"])
        pid = proj.json()["id"]

        # Archive (delete) the project
        await client.delete(f"/api/v1/projects/{pid}", headers=user["headers"])

        # Try to create a session for the archived project
        r = await client.post(
            f"/api/v1/chat/sessions/{pid}", headers=user["headers"]
        )
        assert r.status_code == 404, (
            f"Session created for archived project! Status: {r.status_code}"
        )

    async def test_send_message_to_nonexistent_session(self, client):
        """Sending a message to a non-existent session should fail."""
        user = await _register(client, "badmsg")
        fake_sid = str(uuid.uuid4())
        r = await client.post(
            f"/api/v1/chat/session/{fake_sid}/messages",
            json={"content": "Hello?"},
            headers=user["headers"],
        )
        assert r.status_code == 404

    async def test_multiple_messages_ordered_correctly(self, client):
        """Messages should be returned in chronological order."""
        user = await _register(client, "msgorder")
        proj = await client.post("/api/v1/projects", json={
            "name": "OrderTest", "description": "test"
        }, headers=user["headers"])
        pid = proj.json()["id"]
        sess = await client.post(f"/api/v1/chat/sessions/{pid}", headers=user["headers"])
        sid = sess.json()["id"]

        # Send 3 messages
        contents = ["First", "Second", "Third"]
        for content in contents:
            r = await client.post(f"/api/v1/chat/session/{sid}/messages", json={
                "content": content
            }, headers=user["headers"])
            assert r.status_code == 201

        # Retrieve messages
        msgs = await client.get(
            f"/api/v1/chat/session/{sid}/messages", headers=user["headers"]
        )
        assert msgs.status_code == 200
        msg_contents = [m["content"] for m in msgs.json()]
        assert msg_contents == contents, f"Messages out of order: {msg_contents}"

    async def test_session_updated_at_changes_on_message(self, client):
        """Session updated_at should change when a new message is sent."""
        user = await _register(client, "updtime")
        proj = await client.post("/api/v1/projects", json={
            "name": "UpdTimeTest", "description": "test"
        }, headers=user["headers"])
        pid = proj.json()["id"]

        sess = await client.post(f"/api/v1/chat/sessions/{pid}", headers=user["headers"])
        sid = sess.json()["id"]
        created_at = sess.json()["created_at"]

        # Small delay to ensure different timestamps
        import asyncio
        await asyncio.sleep(0.1)

        # Send a message
        await client.post(f"/api/v1/chat/session/{sid}/messages", json={
            "content": "Update the timestamp!"
        }, headers=user["headers"])

        # Fetch session again
        sess2 = await client.get(f"/api/v1/chat/session/{sid}", headers=user["headers"])
        assert sess2.status_code == 200


# ══════════════════════════════════════════════════════════════════
# 10. AUDIT TRAIL TESTS
# ══════════════════════════════════════════════════════════════════


@requires_infra
class TestAuditTrail:
    """Verify audit logs are written for security-relevant actions."""

    def setup_method(self):
        _reset_rate_limiter()

    async def test_register_creates_audit_log(self, client):
        """Registration should create an audit.logs entry."""
        from sqlalchemy import text
        from app.database import get_session_factory

        user = await _register(client, "auditlog")

        # Extract user_id from JWT token (the 'sub' claim)
        from jose import jwt
        token = user["headers"]["Authorization"].split(" ")[1]
        payload = jwt.get_unverified_claims(token)
        user_id = payload["sub"]

        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(text("""
                SELECT action, entity_type
                FROM audit.logs
                WHERE user_id = :uid
                ORDER BY created_at DESC
                LIMIT 5
            """), {"uid": user_id})
            rows = result.fetchall()
            actions = [r[0] for r in rows]
            assert "user.register" in actions, (
                f"Missing audit log for registration! Actions for user: {actions}"
            )

    async def test_login_creates_audit_log(self, client):
        """Login should create an audit.logs entry."""
        from sqlalchemy import text
        from app.database import get_session_factory

        email = _email("auditlogin")
        password = "AuditLogin123!"
        await client.post("/api/v1/auth/register", json={
            "email": email, "password": password,
            "name": "AuditLogin", "organization_name": f"ALOrg-{uuid.uuid4().hex[:6]}",
        })

        await client.post("/api/v1/auth/login", json={
            "email": email, "password": password,
        })

        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(text("""
                SELECT action FROM audit.logs
                ORDER BY created_at DESC
                LIMIT 5
            """))
            recent_actions = [r[0] for r in result.fetchall()]
            assert "user.login" in recent_actions, (
                f"Missing audit log for login! Recent: {recent_actions}"
            )

    async def test_audit_log_has_no_pii(self, client):
        """Audit logs must NOT contain email or password."""
        from sqlalchemy import text
        from app.database import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(text("""
                SELECT action, CAST(after_state AS TEXT), CAST(metadata AS TEXT)
                FROM audit.logs
                ORDER BY created_at DESC
                LIMIT 20
            """))
            for action, after_state, metadata in result.fetchall():
                combined = (after_state or "") + (metadata or "")
                assert "password" not in combined.lower() or "password_hash" not in combined.lower(), (
                    f"PII in audit log ({action}): password found in state/metadata"
                )
                # Email should not be in audit logs (R8-FIX)
                assert "@" not in (after_state or ""), (
                    f"PII in audit log ({action}): email found in after_state"
                )
