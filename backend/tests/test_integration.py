"""Integration tests: real DB + real Valkey.

Tests the full stack end-to-end with real PostgreSQL, RLS policies,
and Valkey. Skipped if infrastructure is unavailable.

Covers:
1. Auth flow: register -> login -> refresh -> logout
2. RLS tenant isolation: cross-org data access blocked
3. Project CRUD with tenant scoping
4. Chat session lifecycle
5. Token revocation via Valkey
6. Security headers on all responses
7. Rate limiting on auth endpoints
8. Database constraints (unique email, UUID validation)
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

# ── Override event loop scope for this module ─────────────────────
# All tests share one event loop + one app lifespan to avoid
# re-connecting to GCP Cloud SQL / running Alembic per test.
pytestmark = pytest.mark.asyncio(loop_scope="module")


# ── Infrastructure connectivity ──────────────────────────────────

_DB_HOST = os.environ.get("DB_HOST", "34.93.166.39")
_DB_NAME = os.environ.get("DB_NAME", "nexsidi-database")
_DB_USER = os.environ.get("DB_USER", "postgres")
_DB_PASSWORD = os.environ.get("DB_PASSWORD", 'J>3y&s~#F|*S)f""')
_VALKEY_URL = os.environ.get("VALKEY_URL", "redis://localhost:6379/15")

# Email domain: .test is reserved (RFC 2606) and rejected by
# email-validator. Use a real-looking but non-existent domain.
_TEST_DOMAIN = "nexsidi-inttest.dev"


def _build_db_url() -> str:
    return (
        f"postgresql+asyncpg://{_DB_USER}:{quote_plus(_DB_PASSWORD)}"
        f"@{_DB_HOST}:5432/{_DB_NAME}"
    )


def _test_email(prefix: str = "test") -> str:
    """Generate unique test email that passes pydantic EmailStr validation."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}@{_TEST_DOMAIN}"


async def _can_connect_db() -> bool:
    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(
            _build_db_url(), pool_size=1, connect_args={"timeout": 5}
        )
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await engine.dispose()
        return True
    except Exception:
        return False


async def _can_connect_valkey() -> bool:
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(_VALKEY_URL, socket_connect_timeout=3)
        await r.ping()
        await r.aclose()
        return True
    except Exception:
        return False


# Run connectivity checks at import time (safe: no event loop yet)
try:
    _DB_AVAILABLE = asyncio.run(_can_connect_db())
except Exception:
    _DB_AVAILABLE = False

try:
    _VALKEY_AVAILABLE = asyncio.run(_can_connect_valkey())
except Exception:
    _VALKEY_AVAILABLE = False

requires_db = pytest.mark.skipif(not _DB_AVAILABLE, reason="Real DB not available")
requires_valkey = pytest.mark.skipif(not _VALKEY_AVAILABLE, reason="Valkey not available")
requires_infra = pytest.mark.skipif(
    not (_DB_AVAILABLE and _VALKEY_AVAILABLE),
    reason="Real DB + Valkey required",
)


# ── Fixtures ──────────────────────────────────────────────────────


@pytest_asyncio.fixture(scope="module")
async def real_client() -> AsyncGenerator[AsyncClient, None]:
    """HTTP client with REAL lifespan (DB + Valkey initialized).

    Module-scoped: the app lifespan (DB engine, Valkey, Alembic migrations)
    runs ONCE for all tests in this module. This avoids re-connecting to
    GCP Cloud SQL and running migrations per test (~10s each).

    Uses asgi-lifespan.LifespanManager to trigger FastAPI startup/shutdown
    events. Without this, ASGITransport alone does NOT run lifespan events.
    """
    # Set environment for real infrastructure
    os.environ["DATABASE_URL"] = _build_db_url()
    os.environ["VALKEY_URL"] = _VALKEY_URL
    os.environ.setdefault(
        "JWT_SECRET_KEY", "integration-test-key-that-is-at-least-32-chars-long"
    )
    os.environ.setdefault("ENVIRONMENT", "development")
    os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-key")
    os.environ.setdefault("GOOGLE_AI_API_KEY", "test-google-key")

    from app.config import get_settings
    get_settings.cache_clear()

    from asgi_lifespan import LifespanManager
    from app.main import create_app

    app = create_app()
    async with LifespanManager(app, startup_timeout=120, shutdown_timeout=30) as manager:
        # Reset the in-memory rate limiter so integration tests aren't
        # blocked by the 5-registration / 10-login per-IP limits.
        from app.routers.auth import _rate_limiter
        _rate_limiter._attempts.clear()

        transport = ASGITransport(app=manager.app, raise_app_exceptions=False)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            follow_redirects=True,
        ) as ac:
            yield ac

    get_settings.cache_clear()


@pytest.fixture
def unique_email() -> str:
    """Generate unique test email that passes email-validator."""
    return _test_email("test")


# ── Known existing user/org (from production data) ────────────────

EXISTING_USER_ID = "69e38f98-043d-4985-852b-ab9da7d4cbf5"
EXISTING_ORG_ID = "aba37a01-60c9-49c7-af61-2a7c243d25b0"
EXISTING_EMAIL = "admin@nexsidi.com"


# ── Helper: register + get token ─────────────────────────────────


def _reset_rate_limiter() -> None:
    """Reset the in-memory rate limiter to avoid cross-test pollution."""
    from app.routers.auth import _rate_limiter
    _rate_limiter._attempts.clear()


async def _register_user(
    client: AsyncClient, prefix: str = "user"
) -> dict[str, Any]:
    """Register a new user and return the full token response."""
    email = _test_email(prefix)
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "TestPass123!",
            "name": f"Test {prefix}",
            "organization_name": f"Org-{uuid.uuid4().hex[:6]}",
        },
    )
    assert resp.status_code == 201, f"Register failed ({email}): {resp.text}"
    data = resp.json()
    data["email"] = email
    data["headers"] = {"Authorization": f"Bearer {data['access_token']}"}
    return data


# ── 1. Auth Flow Tests ───────────────────────────────────────────


@requires_infra
class TestAuthFlowIntegration:
    """End-to-end auth: register -> login -> access -> refresh -> logout."""

    async def test_health_with_real_db(self, real_client):
        """Health check should return healthy when DB is connected."""
        resp = await real_client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"

    async def test_readiness_with_real_db(self, real_client):
        """Readiness check should verify real DB connectivity."""
        resp = await real_client.get("/health/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["checks"]["database"] == "ok", f"DB check failed: {data}"

    async def test_register_new_user(self, real_client, unique_email):
        """Register creates org + user, returns valid tokens."""
        resp = await real_client.post(
            "/api/v1/auth/register",
            json={
                "email": unique_email,
                "password": "TestPassword123!",
                "name": "Integration Test User",
                "organization_name": f"Test Org {uuid.uuid4().hex[:6]}",
            },
        )
        assert resp.status_code == 201, f"Register failed: {resp.text}"
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["expires_in"] > 0

    async def test_login_existing_user(self, real_client):
        """Login with registered user returns tokens."""
        email = _test_email("login")
        password = "LoginTest123!"

        # Register
        reg_resp = await real_client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": password,
                "name": "Login Test",
                "organization_name": f"LoginOrg {uuid.uuid4().hex[:6]}",
            },
        )
        assert reg_resp.status_code == 201, f"Register failed: {reg_resp.text}"

        # Login with same credentials
        login_resp = await real_client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
        data = login_resp.json()
        assert "access_token" in data
        assert "refresh_token" in data

    async def test_login_wrong_password(self, real_client):
        """Login with wrong password returns 401."""
        user = await _register_user(real_client, "wrongpw")

        resp = await real_client.post(
            "/api/v1/auth/login",
            json={"email": user["email"], "password": "WrongPassword999!"},
        )
        assert resp.status_code == 401

    async def test_login_nonexistent_email(self, real_client):
        """Login with non-existent email returns 401 (not 404)."""
        resp = await real_client.post(
            "/api/v1/auth/login",
            json={
                "email": f"does-not-exist-{uuid.uuid4().hex[:8]}@{_TEST_DOMAIN}",
                "password": "SomePassword123!",
            },
        )
        # Must be 401, NOT 404 (email enumeration prevention)
        assert resp.status_code == 401

    async def test_refresh_token_flow(self, real_client):
        """Refresh token generates new access token."""
        user = await _register_user(real_client, "refresh")

        refresh_resp = await real_client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": user["refresh_token"]},
        )
        assert refresh_resp.status_code == 200, f"Refresh failed: {refresh_resp.text}"
        new_tokens = refresh_resp.json()
        assert "access_token" in new_tokens
        assert new_tokens["access_token"] != user["access_token"]

    async def test_me_endpoint(self, real_client):
        """GET /me returns user info with valid token."""
        user = await _register_user(real_client, "me")

        me_resp = await real_client.get("/api/v1/auth/me", headers=user["headers"])
        assert me_resp.status_code == 200, f"Me failed: {me_resp.text}"
        data = me_resp.json()
        assert data["email"] == user["email"]
        assert data["role"] == "org_admin"

    async def test_invalid_token_rejected(self, real_client):
        """Invalid JWT is rejected with 401/403."""
        resp = await real_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer invalid-jwt-token"},
        )
        assert resp.status_code in (401, 403)

    async def test_no_token_rejected(self, real_client):
        """Request without auth header is rejected."""
        resp = await real_client.get("/api/v1/auth/me")
        assert resp.status_code in (401, 403)


# ── 2. RLS Tenant Isolation Tests ─────────────────────────────────


@requires_infra
class TestRLSIsolation:
    """RLS must prevent cross-tenant data access.

    NOTE: PostgreSQL superusers (e.g., 'postgres') bypass ALL RLS policies.
    In production, the app connects as 'nexsidi_app' with FORCE ROW LEVEL
    SECURITY. When testing as 'postgres', the RLS enforcement test is skipped
    and we verify that RLS policies exist instead.
    """

    def setup_method(self):
        _reset_rate_limiter()

    async def test_two_orgs_cannot_see_each_other_projects(self, real_client):
        """Org A's projects must be invisible to Org B via RLS."""
        if _DB_USER == "postgres":
            pytest.skip(
                "Connected as 'postgres' (superuser) -- RLS policies are bypassed. "
                "Use a non-superuser DB_USER (e.g., 'nexsidi_app') to test RLS enforcement."
            )

        user_a = await _register_user(real_client, "orga")
        user_b = await _register_user(real_client, "orgb")

        # Create a project as User A
        proj_resp = await real_client.post(
            "/api/v1/projects",
            json={"name": "Secret Project A", "description": "For Org A only"},
            headers=user_a["headers"],
        )
        assert proj_resp.status_code == 201, f"Create project failed: {proj_resp.text}"
        project_id = proj_resp.json()["id"]

        # User B tries to list projects -- should NOT see Org A's project
        list_resp = await real_client.get(
            "/api/v1/projects", headers=user_b["headers"]
        )
        assert list_resp.status_code == 200
        data = list_resp.json()
        project_ids_b = [p["id"] for p in data.get("projects", data)]
        assert project_id not in project_ids_b, (
            "RLS FAIL: Org B can see Org A's project!"
        )

        # User B tries to access project directly -- should get 404
        get_resp = await real_client.get(
            f"/api/v1/projects/{project_id}", headers=user_b["headers"]
        )
        assert get_resp.status_code == 404, (
            "RLS FAIL: Org B can access Org A's project!"
        )

    async def test_rls_policies_exist(self, real_client):
        """Verify RLS policies are defined on key tables."""
        from sqlalchemy import text
        from app.database import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                text(
                    "SELECT schemaname, tablename, policyname "
                    "FROM pg_policies "
                    "WHERE schemaname IN ('core', 'pipeline', 'chat', 'audit') "
                    "ORDER BY schemaname, tablename"
                )
            )
            policies = result.fetchall()
            assert len(policies) > 0, (
                "NO RLS policies found! Expected policies on core.projects, "
                "pipeline.runs, chat.sessions, etc."
            )
            # Verify policies on critical tables
            table_names = {row[1] for row in policies}
            assert "projects" in table_names, "Missing RLS policy on projects table"


# ── 3. Project CRUD Tests ────────────────────────────────────────


@requires_infra
class TestProjectCRUD:
    """Project create/list/get/update/delete with real DB."""

    def setup_method(self):
        _reset_rate_limiter()

    async def test_create_and_get_project(self, real_client):
        """Create a project and retrieve it."""
        user = await _register_user(real_client, "proj")
        headers = user["headers"]

        # Create
        create_resp = await real_client.post(
            "/api/v1/projects",
            json={"name": "My App", "description": "Test project"},
            headers=headers,
        )
        assert create_resp.status_code == 201
        project = create_resp.json()
        assert project["name"] == "My App"
        project_id = project["id"]

        # Get
        get_resp = await real_client.get(
            f"/api/v1/projects/{project_id}", headers=headers
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["name"] == "My App"

        # List
        list_resp = await real_client.get("/api/v1/projects", headers=headers)
        assert list_resp.status_code == 200
        data = list_resp.json()
        assert data["total"] >= 1
        ids = [p["id"] for p in data["projects"]]
        assert project_id in ids

    async def test_update_project(self, real_client):
        """PATCH project updates fields."""
        user = await _register_user(real_client, "projupd")
        headers = user["headers"]

        create_resp = await real_client.post(
            "/api/v1/projects",
            json={"name": "Before", "description": "old"},
            headers=headers,
        )
        project_id = create_resp.json()["id"]

        patch_resp = await real_client.patch(
            f"/api/v1/projects/{project_id}",
            json={"name": "After", "description": "new"},
            headers=headers,
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["name"] == "After"
        assert patch_resp.json()["description"] == "new"

    async def test_delete_project(self, real_client):
        """DELETE project archives it."""
        user = await _register_user(real_client, "projdel")
        headers = user["headers"]

        create_resp = await real_client.post(
            "/api/v1/projects",
            json={"name": "To Delete", "description": "will be archived"},
            headers=headers,
        )
        project_id = create_resp.json()["id"]

        del_resp = await real_client.delete(
            f"/api/v1/projects/{project_id}", headers=headers
        )
        assert del_resp.status_code in (200, 204), f"Delete failed: {del_resp.text}"

        # After delete, GET should return 404 (archived)
        get_resp = await real_client.get(
            f"/api/v1/projects/{project_id}", headers=headers
        )
        assert get_resp.status_code == 404


# ── 4. Chat Session Tests ────────────────────────────────────────


@requires_infra
class TestChatIntegration:
    """Chat sessions + messages with real DB."""

    def setup_method(self):
        _reset_rate_limiter()

    async def test_chat_session_lifecycle(self, real_client):
        """Create session -> send message -> retrieve messages."""
        user = await _register_user(real_client, "chat")
        headers = user["headers"]

        # Create a project first
        proj = await real_client.post(
            "/api/v1/projects",
            json={"name": "Chat Project", "description": "For chat testing"},
            headers=headers,
        )
        assert proj.status_code == 201
        project_id = proj.json()["id"]

        # Create chat session
        session_resp = await real_client.post(
            f"/api/v1/chat/sessions/{project_id}",
            headers=headers,
        )
        assert session_resp.status_code == 201, (
            f"Create session failed: {session_resp.text}"
        )
        session_id = session_resp.json()["id"]

        # Send a message
        msg_resp = await real_client.post(
            f"/api/v1/chat/session/{session_id}/messages",
            json={"content": "Hello, world!"},
            headers=headers,
        )
        assert msg_resp.status_code == 201, f"Send message failed: {msg_resp.text}"
        assert msg_resp.json()["content"] == "Hello, world!"
        assert msg_resp.json()["role"] == "user"

        # List messages
        msgs_resp = await real_client.get(
            f"/api/v1/chat/session/{session_id}/messages",
            headers=headers,
        )
        assert msgs_resp.status_code == 200
        messages = msgs_resp.json()
        assert len(messages) >= 1
        assert messages[0]["content"] == "Hello, world!"

    async def test_chat_cross_user_isolation(self, real_client):
        """User B cannot access User A's chat session."""
        user_a = await _register_user(real_client, "chata")
        user_b = await _register_user(real_client, "chatb")

        # User A creates project + session
        proj = await real_client.post(
            "/api/v1/projects",
            json={"name": "A's Project", "description": "Private"},
            headers=user_a["headers"],
        )
        project_id = proj.json()["id"]

        session_resp = await real_client.post(
            f"/api/v1/chat/sessions/{project_id}",
            headers=user_a["headers"],
        )
        session_id = session_resp.json()["id"]

        # User B tries to access User A's session -> 404
        get_resp = await real_client.get(
            f"/api/v1/chat/session/{session_id}",
            headers=user_b["headers"],
        )
        assert get_resp.status_code == 404, (
            f"ISOLATION FAIL: User B can see User A's chat! Status: {get_resp.status_code}"
        )


# ── 5. Token Revocation Tests ────────────────────────────────────


@requires_infra
class TestTokenRevocation:
    """Logout should revoke tokens via Valkey."""

    def setup_method(self):
        _reset_rate_limiter()

    async def test_logout_revokes_access_token(self, real_client):
        """After logout, the access token should be rejected."""
        user = await _register_user(real_client, "logout")
        headers = user["headers"]

        # Verify token works
        me_resp = await real_client.get("/api/v1/auth/me", headers=headers)
        assert me_resp.status_code == 200

        # Logout
        logout_resp = await real_client.post("/api/v1/auth/logout", headers=headers)
        assert logout_resp.status_code == 204, f"Logout failed: {logout_resp.text}"

        # Token should now be rejected
        me_resp2 = await real_client.get("/api/v1/auth/me", headers=headers)
        assert me_resp2.status_code in (401, 403), (
            f"REVOCATION FAIL: Token still works after logout! Status: {me_resp2.status_code}"
        )

    async def test_refresh_after_logout_fails(self, real_client):
        """Refresh token included in logout body should also be revoked."""
        user = await _register_user(real_client, "logoutref")
        headers = user["headers"]

        # Logout with refresh token in body
        logout_resp = await real_client.post(
            "/api/v1/auth/logout",
            headers=headers,
            json={"refresh_token": user["refresh_token"]},
        )
        assert logout_resp.status_code == 204

        # Refresh should fail
        refresh_resp = await real_client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": user["refresh_token"]},
        )
        assert refresh_resp.status_code in (401, 503), (
            f"REVOCATION FAIL: Refresh works after logout! Status: {refresh_resp.status_code}"
        )

    async def test_logout_all_revokes_all_tokens(self, real_client):
        """POST /logout-all revokes all tokens for the user."""
        # Register and get two sets of tokens (login twice)
        email = _test_email("logoutall")
        password = "LogoutAll123!"

        reg = await real_client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": password,
                "name": "Logout All",
                "organization_name": f"LAOrg-{uuid.uuid4().hex[:6]}",
            },
        )
        assert reg.status_code == 201
        tokens1 = reg.json()
        headers1 = {"Authorization": f"Bearer {tokens1['access_token']}"}

        # Login again to get a second token set
        login = await real_client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        assert login.status_code == 200
        tokens2 = login.json()
        headers2 = {"Authorization": f"Bearer {tokens2['access_token']}"}

        # Verify both tokens work
        assert (await real_client.get("/api/v1/auth/me", headers=headers1)).status_code == 200
        assert (await real_client.get("/api/v1/auth/me", headers=headers2)).status_code == 200

        # Logout-all using token2
        resp = await real_client.post("/api/v1/auth/logout-all", headers=headers2)
        assert resp.status_code == 204

        # Both tokens should be rejected (family revocation)
        me1 = await real_client.get("/api/v1/auth/me", headers=headers1)
        me2 = await real_client.get("/api/v1/auth/me", headers=headers2)
        assert me1.status_code in (401, 403), (
            f"FAMILY REVOCATION FAIL: Token 1 still works! Status: {me1.status_code}"
        )
        assert me2.status_code in (401, 403), (
            f"FAMILY REVOCATION FAIL: Token 2 still works! Status: {me2.status_code}"
        )


# ── 6. Security Header Tests ─────────────────────────────────────


@requires_infra
class TestSecurityHeaders:
    """Security headers must be set on all responses."""

    async def test_all_security_headers_present(self, real_client):
        """Health endpoint should have all security headers."""
        resp = await real_client.get("/health")
        assert resp.status_code == 200

        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert resp.headers.get("X-XSS-Protection") == "0"
        assert "strict-origin" in resp.headers.get("Referrer-Policy", "")
        assert "Content-Security-Policy" in resp.headers
        assert "Permissions-Policy" in resp.headers

    async def test_csp_has_required_directives(self, real_client):
        """CSP must have default-src, script-src, frame-ancestors."""
        resp = await real_client.get("/health")
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "default-src" in csp
        assert "script-src" in csp
        assert "frame-ancestors 'none'" in csp

    async def test_permissions_policy_restricts_features(self, real_client):
        """Permissions-Policy must disable camera, microphone, etc."""
        resp = await real_client.get("/health")
        pp = resp.headers.get("Permissions-Policy", "")
        assert "camera=()" in pp
        assert "microphone=()" in pp
        assert "geolocation=()" in pp

    async def test_security_headers_on_error_responses(self, real_client):
        """Security headers must be present even on 404/401 responses."""
        resp = await real_client.get("/api/v1/nonexistent")
        # Even error responses should have security headers
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"


# ── 7. Rate Limiting Tests ───────────────────────────────────────


@requires_infra
class TestRateLimiting:
    """Auth rate limiting should block excessive attempts."""

    def setup_method(self):
        _reset_rate_limiter()

    async def test_login_rate_limit(self, real_client):
        """More than 10 login attempts for the same email should be rate-limited."""
        # RATEFIX: Rate limit is now per-email (primary). Use same email address
        # for all attempts so the per-email limit of 10 is triggered.
        target_email = f"bruteforce-target@{_TEST_DOMAIN}"
        responses = []
        for _i in range(12):
            resp = await real_client.post(
                "/api/v1/auth/login",
                json={
                    "email": target_email,
                    "password": "WrongPass123!",
                },
            )
            responses.append(resp.status_code)

        # At least one should be 429 (limit is 10 per email per 15min)
        assert 429 in responses, f"No login rate limiting! Statuses: {responses}"


# ── 8. Database Constraint Tests ──────────────────────────────────


@requires_infra
class TestDatabaseConstraints:
    """Verify database-level constraints work with real DB."""

    def setup_method(self):
        _reset_rate_limiter()

    async def test_duplicate_email_rejected(self, real_client):
        """Registering same email twice should be rejected (DB unique constraint)."""
        email = _test_email("dupe")

        resp1 = await real_client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "DupeTest123!",
                "name": "Dupe 1",
                "organization_name": f"DupeOrg-{uuid.uuid4().hex[:6]}",
            },
        )
        assert resp1.status_code == 201

        resp2 = await real_client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "DupeTest123!",
                "name": "Dupe 2",
                "organization_name": f"DupeOrg2-{uuid.uuid4().hex[:6]}",
            },
        )
        assert resp2.status_code == 409, (
            f"Duplicate email not rejected! Status: {resp2.status_code}"
        )

    async def test_project_requires_valid_uuid(self, real_client):
        """GET project with invalid UUID should return 422."""
        user = await _register_user(real_client, "baduuid")
        resp = await real_client.get(
            "/api/v1/projects/not-a-uuid", headers=user["headers"]
        )
        assert resp.status_code == 422

    async def test_project_nonexistent_uuid_returns_404(self, real_client):
        """GET project with valid but nonexistent UUID should return 404."""
        user = await _register_user(real_client, "noproj")
        fake_id = str(uuid.uuid4())
        resp = await real_client.get(
            f"/api/v1/projects/{fake_id}", headers=user["headers"]
        )
        assert resp.status_code == 404
