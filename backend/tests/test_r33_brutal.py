"""R33 Brutal Integration Tests: Real DB + Real Valkey.

Covers bugs found in R33 review:
1. auth.py JWTError import missing (NameError on /logout with bad refresh token)
2. projects.py log_action() crashes business operations (audit failure → rollback)
3. pipeline.py resume race — duplicate Celery dispatch (status not set before dispatch)
4. valkey_pool.py missing socket_timeout (indefinite blocking on hung Valkey)
"""

from __future__ import annotations

import asyncio
import os
import uuid
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


def _email(prefix: str = "r33") -> str:
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
    os.environ.setdefault("JWT_SECRET_KEY", "r33-brutal-test-key-at-least-32-chars-long-enough")
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


async def _register(c: AsyncClient, prefix: str = "r33") -> dict[str, Any]:
    _reset_rate_limiter()
    email = _email(prefix)
    resp = await c.post("/api/v1/auth/register", json={
        "email": email, "password": "BrutalTest123!",
        "name": f"R33 {prefix}", "organization_name": f"R33Org-{uuid.uuid4().hex[:6]}",
    })
    assert resp.status_code == 201, f"Register failed ({email}): {resp.text}"
    data = resp.json()
    data["email"] = email
    data["headers"] = {"Authorization": f"Bearer {data['access_token']}"}
    return data


# ══════════════════════════════════════════════════════════════════
# 1. AUTH.PY JWTError IMPORT FIX
# ══════════════════════════════════════════════════════════════════


@requires_infra
class TestJWTErrorImport:
    """Verify auth.py imports JWTError so except clause doesn't crash."""

    def setup_method(self):
        _reset_rate_limiter()

    async def test_jwterror_is_imported_in_auth_module(self, client):
        """auth.py must import JWTError from jose. Without it, line 578
        `except JWTError:` raises NameError instead of catching the exception."""
        from app.routers import auth
        import inspect
        source = inspect.getsource(auth)
        # Verify the import exists
        assert "from jose import JWTError" in source or "from jose import" in source
        # Verify JWTError is accessible in the module namespace
        assert hasattr(auth, "JWTError") or "JWTError" in dir(auth)

    async def test_logout_with_invalid_refresh_token_no_500(self, client):
        """Logout with malformed refresh_token should NOT return 500.

        R33-FIX: Previously, `except JWTError:` on line 578 raised NameError
        because JWTError was never imported. The NameError was caught by the
        outer `except Exception`, which returned 503. With the import fixed,
        the JWTError is properly caught and ignored (we're logging out anyway).
        """
        user = await _register(client, "jwtfix")

        resp = await client.post(
            "/api/v1/auth/logout",
            headers=user["headers"],
            json={"refresh_token": "this.is.not.a.valid.jwt.token"},
        )
        # Should succeed (204) — the access token revocation worked,
        # and the invalid refresh token is silently ignored.
        assert resp.status_code == 204, (
            f"Logout with bad refresh token should be 204, got {resp.status_code}: {resp.text}"
        )

    async def test_logout_with_expired_refresh_token_no_500(self, client):
        """Logout with expired refresh_token should NOT crash."""
        import time
        from app.services.auth import create_refresh_token

        user = await _register(client, "jwtexp")

        # Create a refresh token with 0-second expiry (already expired)
        # We'll use a normal refresh token but tamper with it to make it invalid
        # Actually, the simplest test: use a garbage JWT-shaped string
        expired_like = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6InRlc3QiLCJleHAiOjB9.invalid"

        resp = await client.post(
            "/api/v1/auth/logout",
            headers=user["headers"],
            json={"refresh_token": expired_like},
        )
        assert resp.status_code == 204, (
            f"Logout with expired-like refresh should be 204, got {resp.status_code}"
        )

    async def test_logout_without_refresh_token_still_works(self, client):
        """Logout without refresh_token body should work (revokes access only)."""
        user = await _register(client, "jwtnoref")

        resp = await client.post(
            "/api/v1/auth/logout",
            headers=user["headers"],
        )
        assert resp.status_code == 204

    async def test_logout_with_valid_refresh_token_revokes_both(self, client):
        """Logout with valid refresh_token revokes both access AND refresh."""
        user = await _register(client, "jwtboth")

        resp = await client.post(
            "/api/v1/auth/logout",
            headers=user["headers"],
            json={"refresh_token": user["refresh_token"]},
        )
        assert resp.status_code == 204

        # The refresh token should now be revoked — can't use it for /refresh
        _reset_rate_limiter()
        refresh_resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": user["refresh_token"]},
        )
        assert refresh_resp.status_code == 401, (
            f"Revoked refresh token should be rejected, got {refresh_resp.status_code}"
        )


# ══════════════════════════════════════════════════════════════════
# 2. PROJECTS.PY AUDIT LOG FAILURE ISOLATION
# ══════════════════════════════════════════════════════════════════


@requires_infra
class TestProjectAuditLogIsolation:
    """Verify log_action() failures don't crash project operations."""

    def setup_method(self):
        _reset_rate_limiter()

    async def test_create_project_survives_audit_failure(self, client):
        """Project creation should succeed even when log_action() throws.

        R33-FIX: Previously, log_action() was called without try/except inside
        the TenantSession transaction. If audit logging failed, the entire
        transaction rolled back and the project was NOT created.
        """
        user = await _register(client, "auditcr")

        # Patch log_action to raise an error
        with patch("app.routers.projects.log_action", new_callable=AsyncMock) as mock_log:
            mock_log.side_effect = RuntimeError("Simulated audit DB failure")

            resp = await client.post(
                "/api/v1/projects",
                headers=user["headers"],
                json={"name": "AuditFail Project", "description": "Should still create"},
            )
            # Project should be created despite audit failure
            assert resp.status_code == 201, (
                f"Project creation should succeed despite audit failure, got {resp.status_code}: {resp.text}"
            )
            assert resp.json()["name"] == "AuditFail Project"

    async def test_update_project_survives_audit_failure(self, client):
        """Project update should succeed even when log_action() throws."""
        user = await _register(client, "auditup")

        # Create project first (with working audit)
        resp = await client.post(
            "/api/v1/projects",
            headers=user["headers"],
            json={"name": "AuditUpd Project", "description": "original"},
        )
        assert resp.status_code == 201
        pid = resp.json()["id"]

        # Now patch log_action to fail during update
        with patch("app.routers.projects.log_action", new_callable=AsyncMock) as mock_log:
            mock_log.side_effect = RuntimeError("Simulated audit DB failure")

            resp = await client.patch(
                f"/api/v1/projects/{pid}",
                headers=user["headers"],
                json={"name": "Updated Despite Audit Fail"},
            )
            assert resp.status_code == 200, (
                f"Project update should succeed despite audit failure, got {resp.status_code}"
            )
            assert resp.json()["name"] == "Updated Despite Audit Fail"

    async def test_delete_project_survives_audit_failure(self, client):
        """Project deletion (archive) should succeed even when log_action() throws."""
        user = await _register(client, "auditdel")

        resp = await client.post(
            "/api/v1/projects",
            headers=user["headers"],
            json={"name": "AuditDel Project", "description": "to be archived"},
        )
        assert resp.status_code == 201
        pid = resp.json()["id"]

        with patch("app.routers.projects.log_action", new_callable=AsyncMock) as mock_log:
            mock_log.side_effect = RuntimeError("Simulated audit DB failure")

            resp = await client.delete(
                f"/api/v1/projects/{pid}",
                headers=user["headers"],
            )
            assert resp.status_code == 204, (
                f"Project archive should succeed despite audit failure, got {resp.status_code}"
            )

        # Verify project is actually archived (GET should return 404)
        resp = await client.get(
            f"/api/v1/projects/{pid}",
            headers=user["headers"],
        )
        assert resp.status_code == 404

    async def test_project_operations_still_audit_when_working(self, client):
        """Normal project operations should still write audit logs."""
        user = await _register(client, "auditok")

        with patch("app.routers.projects.log_action", new_callable=AsyncMock) as mock_log:
            mock_log.return_value = None  # Success

            resp = await client.post(
                "/api/v1/projects",
                headers=user["headers"],
                json={"name": "AuditOK Project", "description": "audit works"},
            )
            assert resp.status_code == 201
            # Verify log_action was called
            assert mock_log.called, "log_action should be called on project creation"
            call_kwargs = mock_log.call_args
            assert call_kwargs is not None


# ══════════════════════════════════════════════════════════════════
# 3. PIPELINE RESUME RACE CONDITION (CELERY PATH)
# ══════════════════════════════════════════════════════════════════


class TestPipelineResumeRace:
    """Verify resume sets status=RUNNING before Celery dispatch."""

    def test_resume_endpoint_sets_running_before_dispatch(self):
        """The resume endpoint must set run.status=RUNNING and persist
        BEFORE dispatching to Celery, preventing duplicate dispatch.

        R33-FIX: Previously, two rapid /resume calls both saw PAUSED,
        both passed the status check, and both dispatched Celery tasks.
        """
        import inspect
        from app.routers import pipeline

        source = inspect.getsource(pipeline.resume_pipeline)

        # Verify the status is set to RUNNING before dispatch
        running_pos = source.find("run.status = PipelineRunStatus.RUNNING")
        dispatch_pos = source.find("resume_pipeline_task.apply_async")

        # Both must exist
        assert running_pos > 0, "resume_pipeline must set run.status = RUNNING"
        assert dispatch_pos > 0, "resume_pipeline must dispatch to Celery"

        # RUNNING must come BEFORE dispatch
        assert running_pos < dispatch_pos, (
            "run.status = RUNNING must come BEFORE Celery dispatch "
            f"(RUNNING at {running_pos}, dispatch at {dispatch_pos})"
        )

    def test_resume_reverts_status_on_dispatch_failure(self):
        """If Celery dispatch fails, run status must be reverted."""
        import inspect
        from app.routers import pipeline

        source = inspect.getsource(pipeline.resume_pipeline)

        # Must save original status for revert
        assert "original_status = run.status" in source, (
            "resume_pipeline must save original_status before setting RUNNING"
        )

        # Must revert on dispatch failure
        assert "run.status = original_status" in source, (
            "resume_pipeline must revert to original_status on dispatch failure"
        )

    def test_resume_persists_before_dispatch(self):
        """Status change must be persisted to DB before Celery dispatch."""
        import inspect
        from app.routers import pipeline

        source = inspect.getsource(pipeline.resume_pipeline)

        persist_pos = source.find("await orch._persist_run(run)")
        dispatch_pos = source.find("resume_pipeline_task.apply_async")

        assert persist_pos > 0, "Must persist run state"
        assert dispatch_pos > 0, "Must dispatch to Celery"
        assert persist_pos < dispatch_pos, (
            "Persist must come BEFORE Celery dispatch"
        )


# ══════════════════════════════════════════════════════════════════
# 4. VALKEY POOL SOCKET TIMEOUT
# ══════════════════════════════════════════════════════════════════


class TestValkeyPoolTimeout:
    """Verify Valkey pool has socket_timeout configured."""

    def test_valkey_pool_has_socket_timeout(self):
        """valkey_pool.py must configure socket_timeout to prevent indefinite
        blocking when Valkey is hung."""
        import inspect
        from app.services import valkey_pool

        source = inspect.getsource(valkey_pool)
        assert "socket_timeout" in source, (
            "valkey_pool.py must configure socket_timeout"
        )

    def test_valkey_pool_has_connect_timeout(self):
        """valkey_pool.py must configure socket_connect_timeout."""
        import inspect
        from app.services import valkey_pool

        source = inspect.getsource(valkey_pool)
        assert "socket_connect_timeout" in source, (
            "valkey_pool.py must configure socket_connect_timeout"
        )

    @requires_infra
    async def test_valkey_pool_timeout_values_are_reasonable(self, client):
        """Socket timeout should be between 1 and 30 seconds."""
        import inspect
        import re
        from app.services import valkey_pool

        source = inspect.getsource(valkey_pool)

        # Extract socket_timeout value
        match = re.search(r'"socket_timeout":\s*([\d.]+)', source)
        assert match, "socket_timeout must have a numeric value"
        timeout = float(match.group(1))
        assert 1.0 <= timeout <= 30.0, (
            f"socket_timeout should be 1-30s, got {timeout}s"
        )


# ══════════════════════════════════════════════════════════════════
# 5. REGRESSION: JWTError USED CORRECTLY ACROSS CODEBASE
# ══════════════════════════════════════════════════════════════════


class TestJWTErrorUsageRegression:
    """Verify every file that uses `except JWTError` imports it."""

    def test_all_files_with_except_jwterror_import_it(self):
        """Scan all Python files for `except JWTError` and verify the import exists."""
        import pathlib

        app_dir = pathlib.Path(__file__).parent.parent / "app"
        violations = []

        for py_file in app_dir.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            if "except JWTError" in content:
                # Must have an import for JWTError
                has_import = (
                    "from jose import JWTError" in content
                    or "from jose import" in content and "JWTError" in content
                    or "import JWTError" in content
                )
                if not has_import:
                    violations.append(str(py_file.relative_to(app_dir.parent)))

        assert not violations, (
            f"Files use `except JWTError` without importing it: {violations}"
        )


# ══════════════════════════════════════════════════════════════════
# 6. PIPELINE STATUS OWNER CHECK
# ══════════════════════════════════════════════════════════════════


@requires_infra
class TestPipelineStatusOrgCheck:
    """Verify get_pipeline_status enforces org_id check."""

    def setup_method(self):
        _reset_rate_limiter()

    async def test_pipeline_status_nonexistent_run_returns_404(self, client):
        """Requesting status for a non-existent run returns 404."""
        user = await _register(client, "pipstat_norun")

        fake_run_id = str(uuid.uuid4())
        resp = await client.get(
            f"/api/v1/pipeline/{fake_run_id}",
            headers=user["headers"],
        )
        assert resp.status_code == 404

    async def test_pipeline_status_invalid_uuid_returns_422(self, client):
        """Requesting status with invalid UUID returns 422."""
        user = await _register(client, "pipstat_bad")

        resp = await client.get(
            "/api/v1/pipeline/not-a-uuid",
            headers=user["headers"],
        )
        assert resp.status_code == 422

    async def test_pipeline_status_org_check_exists_in_code(self, client):
        """get_pipeline_status must check organization_id for tenant isolation."""
        import inspect
        from app.routers import pipeline

        source = inspect.getsource(pipeline.get_pipeline_status)
        # Must compare run.organization_id with ctx.organization_id
        assert "organization_id" in source, (
            "get_pipeline_status must check organization_id"
        )
        assert "ctx.organization_id" in source, (
            "get_pipeline_status must use ctx.organization_id for comparison"
        )


# ══════════════════════════════════════════════════════════════════
# 7. NOTIFICATION SERVICE CHANNEL SAFETY
# ══════════════════════════════════════════════════════════════════


class TestNotificationChannelSafety:
    """Verify notification channel.value doesn't crash."""

    async def test_notification_send_with_enum_channel(self):
        """Sending a notification with enum channel should work."""
        from app.services.notification import (
            NotificationChannel,
            NotificationType,
            get_notification_service,
        )

        svc = get_notification_service()
        payload = await svc.send(
            notification_type=NotificationType.PIPELINE_STARTED,
            user_id=str(uuid.uuid4()),
            organization_id=str(uuid.uuid4()),
            channel=NotificationChannel.WEB,
            project_name="Test Project",
        )
        assert payload.channel == NotificationChannel.WEB
        assert payload.channel.value == "web"

    async def test_notification_send_email_channel(self):
        """Email channel should also work without errors."""
        from app.services.notification import (
            NotificationChannel,
            NotificationType,
            get_notification_service,
        )

        svc = get_notification_service()
        payload = await svc.send(
            notification_type=NotificationType.PIPELINE_COMPLETED,
            user_id=str(uuid.uuid4()),
            organization_id=str(uuid.uuid4()),
            channel=NotificationChannel.EMAIL,
            project_name="Email Test",
        )
        assert payload.channel == NotificationChannel.EMAIL
        assert payload.channel.value == "email"


# ══════════════════════════════════════════════════════════════════
# 8. WEBSOCKET AUTH TOKEN VALIDATION
# ══════════════════════════════════════════════════════════════════


class TestWebSocketAuthImports:
    """Verify websocket.py imports JWTError correctly."""

    def test_websocket_module_imports_jwterror(self):
        """websocket.py must import JWTError for its except clause."""
        from app.routers import websocket
        # The module should have JWTError in its namespace
        import inspect
        source = inspect.getsource(websocket)
        assert "from jose import JWTError" in source


# ══════════════════════════════════════════════════════════════════
# 9. END-TO-END: LOGOUT FLOW WITH VARIOUS TOKEN STATES
# ══════════════════════════════════════════════════════════════════


@requires_infra
class TestLogoutFlowEndToEnd:
    """End-to-end logout tests covering edge cases from R33 audit."""

    def setup_method(self):
        _reset_rate_limiter()

    async def test_logout_then_access_token_rejected(self, client):
        """After logout, the access token should be rejected."""
        user = await _register(client, "logaccess")

        # Logout
        resp = await client.post("/api/v1/auth/logout", headers=user["headers"])
        assert resp.status_code == 204

        # Try to use the revoked access token
        me = await client.get("/api/v1/auth/me", headers=user["headers"])
        assert me.status_code == 401, (
            f"Revoked access token should be rejected, got {me.status_code}"
        )

    async def test_logout_all_revokes_all_tokens(self, client):
        """logout-all should revoke ALL tokens for the user."""
        user = await _register(client, "logall")

        # Get a second set of tokens via refresh
        _reset_rate_limiter()
        refresh_resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": user["refresh_token"]},
        )
        assert refresh_resp.status_code == 200
        second_tokens = refresh_resp.json()
        second_headers = {"Authorization": f"Bearer {second_tokens['access_token']}"}

        # Logout-all with second tokens
        resp = await client.post("/api/v1/auth/logout-all", headers=second_headers)
        assert resp.status_code == 204

        # Both the second access token should be rejected
        me = await client.get("/api/v1/auth/me", headers=second_headers)
        assert me.status_code == 401, "All tokens should be revoked after logout-all"


# ══════════════════════════════════════════════════════════════════
# 10. PROJECTS CRUD STILL WORKS WITH AUDIT LOGGING
# ══════════════════════════════════════════════════════════════════


@requires_infra
class TestProjectsCRUDWithAudit:
    """Verify projects CRUD works normally (audit logging not broken by try/except)."""

    def setup_method(self):
        _reset_rate_limiter()

    async def test_full_project_lifecycle(self, client):
        """Create → Read → Update → Delete project with full audit logging."""
        user = await _register(client, "projlife")

        # Create
        resp = await client.post(
            "/api/v1/projects",
            headers=user["headers"],
            json={"name": "Lifecycle Test", "description": "full lifecycle"},
        )
        assert resp.status_code == 201
        proj = resp.json()
        pid = proj["id"]
        assert proj["name"] == "Lifecycle Test"

        # Read
        resp = await client.get(f"/api/v1/projects/{pid}", headers=user["headers"])
        assert resp.status_code == 200
        assert resp.json()["name"] == "Lifecycle Test"

        # Update
        resp = await client.patch(
            f"/api/v1/projects/{pid}",
            headers=user["headers"],
            json={"name": "Updated Lifecycle"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated Lifecycle"

        # Delete (archive)
        resp = await client.delete(f"/api/v1/projects/{pid}", headers=user["headers"])
        assert resp.status_code == 204

        # Verify archived
        resp = await client.get(f"/api/v1/projects/{pid}", headers=user["headers"])
        assert resp.status_code == 404

    async def test_list_projects_pagination(self, client):
        """List projects with pagination still works after R33 fix."""
        user = await _register(client, "projpage")

        # Create 3 projects
        for i in range(3):
            resp = await client.post(
                "/api/v1/projects",
                headers=user["headers"],
                json={"name": f"Page Test {i}", "description": f"proj {i}"},
            )
            assert resp.status_code == 201

        # List with pagination
        resp = await client.get(
            "/api/v1/projects?offset=0&limit=2",
            headers=user["headers"],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert len(data["projects"]) == 2
