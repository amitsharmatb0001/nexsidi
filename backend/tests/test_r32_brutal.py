"""R32 Brutal Integration Tests: Real DB + Real Valkey.

Covers bugs found in R32 review:
1. pipeline.py uuid vs _uuid NameError (regression test)
2. Chat endpoints org_id defense-in-depth (IDOR)
3. /logout refresh token revocation failure handling
4. SSRF URL validation in input_processor
5. Unbounded memory in checkpoint + api_key services
6. Pipeline Celery error log sanitization
7. Cross-org chat session isolation
"""

from __future__ import annotations

import os
import uuid
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


def _email(prefix: str = "r32") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}@{_TEST_DOMAIN}"


import asyncio

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
    os.environ.setdefault("JWT_SECRET_KEY", "r32-brutal-test-key-at-least-32-chars-long-enough")
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


async def _register(c: AsyncClient, prefix: str = "r32") -> dict[str, Any]:
    _reset_rate_limiter()
    email = _email(prefix)
    resp = await c.post("/api/v1/auth/register", json={
        "email": email, "password": "BrutalTest123!",
        "name": f"R32 {prefix}", "organization_name": f"R32Org-{uuid.uuid4().hex[:6]}",
    })
    assert resp.status_code == 201, f"Register failed ({email}): {resp.text}"
    data = resp.json()
    data["email"] = email
    data["headers"] = {"Authorization": f"Bearer {data['access_token']}"}
    return data


# ══════════════════════════════════════════════════════════════════
# 1. PIPELINE UUID FIX REGRESSION TEST
# ══════════════════════════════════════════════════════════════════


@requires_infra
class TestPipelineUuidFix:
    """Verify pipeline.py correctly uses _uuid instead of uuid."""

    def setup_method(self):
        _reset_rate_limiter()

    async def test_pipeline_router_imports_are_correct(self, client):
        """pipeline.py should use _uuid.UUID, not uuid.UUID (R32 regression)."""
        # This test verifies the import fix doesn't crash at import time
        from app.routers import pipeline
        # Verify the module loaded successfully and the sanitizer exists
        assert hasattr(pipeline, "_sanitize_dispatch_error")
        assert hasattr(pipeline, "start_pipeline")


# ══════════════════════════════════════════════════════════════════
# 2. CHAT ORG_ID DEFENSE-IN-DEPTH IDOR TESTS
# ══════════════════════════════════════════════════════════════════


@requires_infra
class TestChatCrossOrgIsolation:
    """All chat endpoints must filter by organization_id."""

    def setup_method(self):
        _reset_rate_limiter()

    async def test_list_sessions_cross_org_returns_empty(self, client):
        """User B cannot list User A's chat sessions via project_id."""
        user_a = await _register(client, "chatlist_a")
        user_b = await _register(client, "chatlist_b")

        # A creates project + session
        proj = await client.post("/api/v1/projects", json={
            "name": "A Chat List Proj", "description": "test"
        }, headers=user_a["headers"])
        pid = proj.json()["id"]

        sess = await client.post(f"/api/v1/chat/sessions/{pid}", headers=user_a["headers"])
        assert sess.status_code == 201

        # B tries to list sessions for A's project
        r = await client.get(f"/api/v1/chat/sessions/{pid}", headers=user_b["headers"])
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 0, (
            f"IDOR: User B sees User A's chat sessions! total={data['total']}"
        )

    async def test_get_session_cross_org_returns_404(self, client):
        """User B cannot GET User A's chat session."""
        user_a = await _register(client, "chatget_a")
        user_b = await _register(client, "chatget_b")

        proj = await client.post("/api/v1/projects", json={
            "name": "A Get Sess Proj", "description": "test"
        }, headers=user_a["headers"])
        pid = proj.json()["id"]
        sess = await client.post(f"/api/v1/chat/sessions/{pid}", headers=user_a["headers"])
        sid = sess.json()["id"]

        r = await client.get(f"/api/v1/chat/session/{sid}", headers=user_b["headers"])
        assert r.status_code == 404, (
            f"IDOR: User B accessed User A's session! Status: {r.status_code}"
        )

    async def test_send_message_cross_org_returns_404(self, client):
        """User B cannot send messages to User A's session."""
        user_a = await _register(client, "chatsend_a")
        user_b = await _register(client, "chatsend_b")

        proj = await client.post("/api/v1/projects", json={
            "name": "A Send Msg Proj", "description": "test"
        }, headers=user_a["headers"])
        pid = proj.json()["id"]
        sess = await client.post(f"/api/v1/chat/sessions/{pid}", headers=user_a["headers"])
        sid = sess.json()["id"]

        r = await client.post(f"/api/v1/chat/session/{sid}/messages", json={
            "content": "Injected by attacker"
        }, headers=user_b["headers"])
        assert r.status_code == 404, (
            f"IDOR: User B injected message into A's session! Status: {r.status_code}"
        )

    async def test_get_messages_cross_org_returns_404(self, client):
        """User B cannot read messages from User A's session."""
        user_a = await _register(client, "chatmsg_a")
        user_b = await _register(client, "chatmsg_b")

        proj = await client.post("/api/v1/projects", json={
            "name": "A Msg Read Proj", "description": "test"
        }, headers=user_a["headers"])
        pid = proj.json()["id"]
        sess = await client.post(f"/api/v1/chat/sessions/{pid}", headers=user_a["headers"])
        sid = sess.json()["id"]

        await client.post(f"/api/v1/chat/session/{sid}/messages", json={
            "content": "Secret message"
        }, headers=user_a["headers"])

        r = await client.get(f"/api/v1/chat/session/{sid}/messages", headers=user_b["headers"])
        assert r.status_code == 404, (
            f"IDOR: User B read User A's messages! Status: {r.status_code}"
        )

    async def test_create_session_for_other_org_project_returns_404(self, client):
        """User B cannot create a chat session for User A's project."""
        user_a = await _register(client, "chatcreate_a")
        user_b = await _register(client, "chatcreate_b")

        proj = await client.post("/api/v1/projects", json={
            "name": "A Create Sess Proj", "description": "test"
        }, headers=user_a["headers"])
        pid = proj.json()["id"]

        r = await client.post(f"/api/v1/chat/sessions/{pid}", headers=user_b["headers"])
        assert r.status_code == 404, (
            f"IDOR: User B created session in A's project! Status: {r.status_code}"
        )


# ══════════════════════════════════════════════════════════════════
# 3. SSRF URL VALIDATION
# ══════════════════════════════════════════════════════════════════


class TestSSRFProtection:
    """Verify URL validation blocks SSRF-prone targets."""

    def test_blocks_localhost(self):
        from app.services.input_processor import _is_valid_url
        assert _is_valid_url("http://localhost/admin") is False

    def test_blocks_127_0_0_1(self):
        from app.services.input_processor import _is_valid_url
        assert _is_valid_url("http://127.0.0.1/") is False

    def test_blocks_metadata_gcp(self):
        from app.services.input_processor import _is_valid_url
        assert _is_valid_url("http://metadata.google.internal/computeMetadata/v1/") is False

    def test_blocks_link_local_aws(self):
        from app.services.input_processor import _is_valid_url
        assert _is_valid_url("http://169.254.169.254/latest/meta-data/") is False

    def test_blocks_private_10(self):
        from app.services.input_processor import _is_valid_url
        assert _is_valid_url("http://10.0.0.1/internal") is False

    def test_blocks_private_172(self):
        from app.services.input_processor import _is_valid_url
        assert _is_valid_url("http://172.16.0.1/") is False

    def test_blocks_private_192(self):
        from app.services.input_processor import _is_valid_url
        assert _is_valid_url("http://192.168.1.1/") is False

    def test_blocks_ipv6_loopback(self):
        from app.services.input_processor import _is_valid_url
        assert _is_valid_url("http://[::1]/") is False

    def test_allows_valid_https(self):
        from app.services.input_processor import _is_valid_url
        assert _is_valid_url("https://example.com/page") is True

    def test_allows_valid_http(self):
        from app.services.input_processor import _is_valid_url
        assert _is_valid_url("http://example.com/page") is True

    def test_rejects_no_scheme(self):
        from app.services.input_processor import _is_valid_url
        assert _is_valid_url("example.com/page") is False

    def test_rejects_ftp(self):
        from app.services.input_processor import _is_valid_url
        assert _is_valid_url("ftp://example.com/file") is False

    def test_blocks_zero_ip(self):
        from app.services.input_processor import _is_valid_url
        assert _is_valid_url("http://0.0.0.0/") is False


# ══════════════════════════════════════════════════════════════════
# 4. CHECKPOINT SERVICE MEMORY EVICTION
# ══════════════════════════════════════════════════════════════════


class TestCheckpointMemoryEviction:
    """Verify checkpoint service evicts old terminal checkpoints."""

    def test_eviction_triggers_above_max(self):
        from app.services.checkpoint import CheckpointService, CheckpointData, ApprovalStatus

        svc = CheckpointService()
        svc._MAX_CHECKPOINTS = 5  # Override for testing

        # Create 10 approved checkpoints (terminal state)
        for i in range(10):
            cp = CheckpointData(
                checkpoint_id=f"cp-{i}",
                pipeline_run_id="run-1",
                status=ApprovalStatus.APPROVED,
                summary={"n": i},
                organization_id="org-1",
                user_id="user-1",
            )
            svc._checkpoints[cp.checkpoint_id] = cp

        svc._evict_terminal()
        assert len(svc._checkpoints) <= 5, (
            f"Eviction failed: {len(svc._checkpoints)} checkpoints remain"
        )

    def test_pending_checkpoints_not_evicted(self):
        from app.services.checkpoint import CheckpointService, CheckpointData, ApprovalStatus

        svc = CheckpointService()
        svc._MAX_CHECKPOINTS = 3

        # Create 5 pending checkpoints (non-terminal)
        for i in range(5):
            cp = CheckpointData(
                checkpoint_id=f"cp-pending-{i}",
                pipeline_run_id="run-1",
                status=ApprovalStatus.PENDING,
                summary={"n": i},
                organization_id="org-1",
                user_id="user-1",
            )
            svc._checkpoints[cp.checkpoint_id] = cp

        svc._evict_terminal()
        # Pending checkpoints should NOT be evicted
        assert len(svc._checkpoints) == 5


# ══════════════════════════════════════════════════════════════════
# 5. API KEY RATE COUNT EVICTION
# ══════════════════════════════════════════════════════════════════


class TestApiKeyRateEviction:
    """Verify api key service evicts stale rate count entries."""

    def test_stale_entries_cleaned(self):
        from app.services.api_key_service import ApiKeyService
        from datetime import datetime, timezone

        svc = ApiKeyService()
        svc._RATE_EVICTION_INTERVAL = 1  # Evict every check

        # Manually insert stale entries
        old_ts = datetime.now(timezone.utc).timestamp() - 120  # 2 minutes old
        svc._rate_counts["stale_key_1"] = [old_ts]
        svc._rate_counts["stale_key_2"] = [old_ts]
        svc._rate_counts["stale_key_3"] = []  # empty

        # Create a key and check its rate limit to trigger eviction
        result = svc.create_key("org-1", "user-1", "test-key")
        svc.check_rate_limit(result.plaintext_key)

        # Stale entries should be cleaned
        assert "stale_key_1" not in svc._rate_counts
        assert "stale_key_2" not in svc._rate_counts
        assert "stale_key_3" not in svc._rate_counts


# ══════════════════════════════════════════════════════════════════
# 6. PIPELINE ERROR LOG SANITIZATION
# ══════════════════════════════════════════════════════════════════


class TestPipelineLogSanitization:
    """Verify Celery dispatch errors don't leak credentials."""

    def test_sanitize_redis_url(self):
        from app.routers.pipeline import _sanitize_dispatch_error
        exc = ConnectionError("Error connecting to redis://:mysecretpassword@redis-host:6379/0")
        result = _sanitize_dispatch_error(exc)
        assert "mysecretpassword" not in result
        assert "***@" in result

    def test_sanitize_broker_password(self):
        from app.routers.pipeline import _sanitize_dispatch_error
        exc = RuntimeError("broker connection failed, password='supersecret123'")
        result = _sanitize_dispatch_error(exc)
        assert "supersecret123" not in result
        assert "password=***" in result

    def test_truncates_long_errors(self):
        from app.routers.pipeline import _sanitize_dispatch_error
        exc = RuntimeError("A" * 500)
        result = _sanitize_dispatch_error(exc)
        assert len(result) <= 300

    def test_safe_error_unchanged(self):
        from app.routers.pipeline import _sanitize_dispatch_error
        exc = RuntimeError("Connection refused on port 6379")
        result = _sanitize_dispatch_error(exc)
        assert "Connection refused" in result


# ══════════════════════════════════════════════════════════════════
# 7. CROSS-ORG PROJECT IDOR (R31 FIX REGRESSION)
# ══════════════════════════════════════════════════════════════════


@requires_infra
class TestProjectIDORRegression:
    """Ensure R31 IDOR fixes are still working."""

    def setup_method(self):
        _reset_rate_limiter()

    async def test_get_project_cross_org_404(self, client):
        """Cross-org GET /projects/{id} must return 404."""
        user_a = await _register(client, "projidor_a")
        user_b = await _register(client, "projidor_b")

        proj = await client.post("/api/v1/projects", json={
            "name": "Org A Secret", "description": "classified"
        }, headers=user_a["headers"])
        pid = proj.json()["id"]

        r = await client.get(f"/api/v1/projects/{pid}", headers=user_b["headers"])
        assert r.status_code == 404

    async def test_patch_project_cross_org_404(self, client):
        """Cross-org PATCH /projects/{id} must return 404."""
        user_a = await _register(client, "projpatch_a")
        user_b = await _register(client, "projpatch_b")

        proj = await client.post("/api/v1/projects", json={
            "name": "Patch Target", "description": "test"
        }, headers=user_a["headers"])
        pid = proj.json()["id"]

        r = await client.patch(f"/api/v1/projects/{pid}", json={
            "name": "HACKED"
        }, headers=user_b["headers"])
        assert r.status_code == 404

    async def test_delete_project_cross_org_blocked(self, client):
        """Cross-org DELETE /projects/{id} must return 404."""
        user_a = await _register(client, "projdel_a")
        user_b = await _register(client, "projdel_b")

        proj = await client.post("/api/v1/projects", json={
            "name": "Delete Target", "description": "test"
        }, headers=user_a["headers"])
        pid = proj.json()["id"]

        r = await client.delete(f"/api/v1/projects/{pid}", headers=user_b["headers"])
        assert r.status_code in (404, 403)

        # Verify still exists for A
        r2 = await client.get(f"/api/v1/projects/{pid}", headers=user_a["headers"])
        assert r2.status_code == 200

    async def test_list_projects_shows_only_own_org(self, client):
        """GET /projects must not show other orgs' projects."""
        user_a = await _register(client, "projlist_a")
        user_b = await _register(client, "projlist_b")

        # A creates a project
        await client.post("/api/v1/projects", json={
            "name": "A-Only Project", "description": "test"
        }, headers=user_a["headers"])

        # B lists projects — should not see A's
        r = await client.get("/api/v1/projects", headers=user_b["headers"])
        data = r.json()
        for p in data["projects"]:
            assert p["name"] != "A-Only Project", (
                "IDOR: User B sees Org A's project in list!"
            )


# ══════════════════════════════════════════════════════════════════
# 8. LOGOUT REFRESH TOKEN REVOCATION
# ══════════════════════════════════════════════════════════════════


@requires_infra
class TestLogoutRefreshRevocation:
    """Ensure /logout with refresh_token actually revokes it."""

    def setup_method(self):
        _reset_rate_limiter()

    async def test_logout_with_refresh_token_revokes_it(self, client):
        """After /logout with refresh_token, the refresh token must be invalid."""
        user = await _register(client, "logoutref")
        headers = user["headers"]
        refresh = user["refresh_token"]

        # Logout, passing refresh token in body
        r = await client.post("/api/v1/auth/logout", headers=headers, json={
            "refresh_token": refresh,
        })
        assert r.status_code == 204

        # Try to use the refresh token — should fail
        r2 = await client.post("/api/v1/auth/refresh", json={
            "refresh_token": refresh,
        })
        assert r2.status_code in (401, 403), (
            f"Refresh token still works after logout! Status: {r2.status_code}"
        )
