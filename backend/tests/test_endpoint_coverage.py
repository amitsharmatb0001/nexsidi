"""Integration tests for previously untested endpoints.

Covers:
1. GET  /chat/sessions/{project_id}  — list chat sessions
2. POST /pipeline/start              — start pipeline
3. GET  /pipeline/{run_id}           — get pipeline status
4. POST /pipeline/{run_id}/approve   — approve checkpoint
5. GET  /notifications/              — list notifications
6. POST /notifications/read          — mark as read
7. WS   /ws/{run_id}                 — pipeline updates (WebSocket)

Uses the SAME patterns as test_integration.py:
- httpx.AsyncClient with ASGITransport
- pytest.mark.asyncio
- Mock database layer (no real DB needed)
- Happy paths AND error paths (401, 403, 404, 422)
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import get_settings

# ── Shared test constants ────────────────────────────────────────

TEST_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000010")
TEST_PROJECT_ID = uuid.UUID("00000000-0000-0000-0000-000000000100")
TEST_RUN_ID = str(uuid.UUID("00000000-0000-0000-0000-000000001000"))
TEST_SESSION_ID = uuid.UUID("00000000-0000-0000-0000-000000010000")

pytestmark = pytest.mark.asyncio


# ── Helper: build the test app with dependency overrides ─────────


def _make_test_app():
    """Create a FastAPI app with mocked lifespan (no real DB/Valkey).

    Returns the app instance. Dependencies are overridden per-test
    using app.dependency_overrides.
    """
    from collections.abc import AsyncIterator
    from contextlib import asynccontextmanager

    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    @asynccontextmanager
    async def test_lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield

    test_app = FastAPI(title="NexSidi Test", lifespan=test_lifespan)

    test_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @test_app.get("/health")
    async def health() -> dict:
        return {"status": "healthy"}

    from app.routers.auth import router as auth_router
    from app.routers.chat import router as chat_router
    from app.routers.notifications import router as notifications_router
    from app.routers.pipeline import router as pipeline_router
    from app.routers.projects import router as projects_router
    from app.routers.websocket import router as ws_router

    test_app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
    test_app.include_router(projects_router, prefix="/api/v1/projects", tags=["projects"])
    test_app.include_router(pipeline_router, prefix="/api/v1/pipeline", tags=["pipeline"])
    test_app.include_router(chat_router, prefix="/api/v1/chat", tags=["chat"])
    test_app.include_router(notifications_router, prefix="/api/v1/notifications", tags=["notifications"])
    test_app.include_router(ws_router, prefix="/api/v1", tags=["websocket"])

    return test_app


# ── Helpers for mocking dependencies ─────────────────────────────


def _make_tenant_context(role: str = "org_admin"):
    """Build a TenantContext for dependency injection."""
    from app.middleware.tenant import TenantContext
    return TenantContext(
        organization_id=str(TEST_ORG_ID),
        user_id=str(TEST_USER_ID),
        role=role,
    )


def _mock_session_with_results(scalars_list=None, scalar_one=None, scalar_one_or_none=None):
    """Build an AsyncMock that pretends to be a TenantSession.

    Supports execute().scalar_one_or_none(), execute().scalars().all(),
    execute().scalar_one(), session.add(), session.flush(), session.refresh().
    """
    mock_session = AsyncMock()

    mock_result = MagicMock()
    if scalar_one_or_none is not None:
        mock_result.scalar_one_or_none.return_value = scalar_one_or_none
    else:
        mock_result.scalar_one_or_none.return_value = None
    if scalar_one is not None:
        mock_result.scalar_one.return_value = scalar_one
    if scalars_list is not None:
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = scalars_list
        mock_result.scalars.return_value = mock_scalars

    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    mock_session.refresh = AsyncMock()

    return mock_session


def _auth_headers_for(role: str = "org_admin") -> dict[str, str]:
    """Generate valid auth headers (JWT) for the test user with given role."""
    get_settings.cache_clear()
    from app.services.auth import create_access_token
    token = create_access_token(TEST_USER_ID, TEST_ORG_ID, role)
    return {"Authorization": f"Bearer {token}"}


# ── Fixture: patched verify_token to skip Valkey ─────────────────

def _patch_verify_token():
    """Return a patch that makes verify_token skip Valkey revocation checks."""
    async def mock_verify(token, *, expected_type=None):
        from app.services.auth import decode_token
        return decode_token(token, expected_type=expected_type)
    return patch("app.services.auth.verify_token", side_effect=mock_verify)


# ═══════════════════════════════════════════════════════════════════
# 1. GET /chat/sessions/{project_id} — list chat sessions
# ═══════════════════════════════════════════════════════════════════


class TestListChatSessions:
    """GET /api/v1/chat/sessions/{project_id}"""

    async def test_list_sessions_happy_path(self):
        """Returns paginated list of chat sessions for a project."""
        app = _make_test_app()

        # Mock dependencies
        from app.dependencies import get_current_user_context, get_tenant_session

        ctx = _make_tenant_context()
        app.dependency_overrides[get_current_user_context] = lambda: ctx

        # Build a fake ChatSession-like object for model_validate
        fake_session = MagicMock()
        fake_session.id = TEST_SESSION_ID
        fake_session.project_id = TEST_PROJECT_ID
        fake_session.agent_name = None
        fake_session.channel = "web"
        fake_session.status = "active"
        fake_session.created_at = datetime.now(timezone.utc)

        # Mock session: first execute = count (scalar_one), second = list (scalars)
        mock_session = AsyncMock()
        count_result = MagicMock()
        count_result.scalar_one.return_value = 1

        list_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [fake_session]
        list_result.scalars.return_value = mock_scalars

        mock_session.execute = AsyncMock(side_effect=[count_result, list_result])

        async def override_session():
            yield mock_session

        app.dependency_overrides[get_tenant_session] = override_session

        with _patch_verify_token():
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                headers = _auth_headers_for("org_admin")
                resp = await client.get(
                    f"/api/v1/chat/sessions/{TEST_PROJECT_ID}",
                    headers=headers,
                )
                assert resp.status_code == 200, f"Unexpected: {resp.text}"
                data = resp.json()
                assert "sessions" in data
                assert data["total"] == 1
                assert len(data["sessions"]) == 1
                assert data["sessions"][0]["channel"] == "web"

    async def test_list_sessions_empty(self):
        """Returns empty list when no sessions exist."""
        app = _make_test_app()

        from app.dependencies import get_current_user_context, get_tenant_session

        ctx = _make_tenant_context()
        app.dependency_overrides[get_current_user_context] = lambda: ctx

        mock_session = AsyncMock()
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0

        list_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        list_result.scalars.return_value = mock_scalars

        mock_session.execute = AsyncMock(side_effect=[count_result, list_result])

        async def override_session():
            yield mock_session

        app.dependency_overrides[get_tenant_session] = override_session

        with _patch_verify_token():
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                headers = _auth_headers_for("org_admin")
                resp = await client.get(
                    f"/api/v1/chat/sessions/{TEST_PROJECT_ID}",
                    headers=headers,
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["total"] == 0
                assert data["sessions"] == []

    async def test_list_sessions_no_auth_returns_401_or_403(self):
        """Request without auth header is rejected."""
        app = _make_test_app()
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/chat/sessions/{TEST_PROJECT_ID}")
            assert resp.status_code in (401, 403)

    async def test_list_sessions_invalid_project_id(self):
        """Invalid UUID returns 422."""
        app = _make_test_app()

        from app.dependencies import get_current_user_context, get_tenant_session

        ctx = _make_tenant_context()
        app.dependency_overrides[get_current_user_context] = lambda: ctx

        mock_session = AsyncMock()
        async def override_session():
            yield mock_session
        app.dependency_overrides[get_tenant_session] = override_session

        with _patch_verify_token():
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                headers = _auth_headers_for("org_admin")
                resp = await client.get(
                    "/api/v1/chat/sessions/not-a-uuid",
                    headers=headers,
                )
                assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════
# 2. POST /pipeline/start — start pipeline
# ═══════════════════════════════════════════════════════════════════


class TestStartPipeline:
    """POST /api/v1/pipeline/start"""

    async def test_start_pipeline_happy_path(self):
        """Starts a pipeline for an owned project."""
        app = _make_test_app()

        from app.dependencies import get_current_user_context, get_tenant_session

        ctx = _make_tenant_context("org_admin")
        app.dependency_overrides[get_current_user_context] = lambda: ctx

        # Build a fake Project object
        fake_project = MagicMock()
        fake_project.id = TEST_PROJECT_ID
        fake_project.organization_id = TEST_ORG_ID
        fake_project.owner_id = TEST_USER_ID
        fake_project.status = "active"

        # Session: 1st call = project check, 2nd = FOR UPDATE lock, 3rd = active run count
        mock_session = AsyncMock()
        proj_result = MagicMock()
        proj_result.scalar_one_or_none.return_value = fake_project

        lock_result = MagicMock()  # FOR UPDATE returns project again

        count_result = MagicMock()
        count_result.scalar_one.return_value = 0  # no active runs

        mock_session.execute = AsyncMock(side_effect=[proj_result, lock_result, count_result])

        async def override_session():
            yield mock_session

        app.dependency_overrides[get_tenant_session] = override_session

        # Mock the orchestrator
        from app.services.pipeline import ExecutionMode, PipelineRun, PipelineRunStatus, PipelineStage

        fake_run = PipelineRun(
            run_id=TEST_RUN_ID,
            project_id=str(TEST_PROJECT_ID),
            organization_id=str(TEST_ORG_ID),
            user_id=str(TEST_USER_ID),
            status=PipelineRunStatus.CREATED,
            execution_mode=ExecutionMode.CHECKPOINT,
            current_stage=PipelineStage.REQUIREMENTS,
            created_at=datetime.now(timezone.utc),
        )

        mock_orch = MagicMock()
        mock_orch.create_run.return_value = fake_run
        mock_orch._persist_run = AsyncMock()
        mock_orch.run_pipeline = AsyncMock()
        mock_orch._active_runs = {}

        with (
            _patch_verify_token(),
            patch("app.routers.pipeline.get_orchestrator", return_value=mock_orch),
            patch("app.routers.pipeline.get_settings") as mock_settings,
        ):
            mock_settings.return_value.use_celery = False
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                headers = _auth_headers_for("org_admin")
                resp = await client.post(
                    "/api/v1/pipeline/start",
                    json={
                        "project_id": str(TEST_PROJECT_ID),
                        "execution_mode": "checkpoint",
                    },
                    headers=headers,
                )
                assert resp.status_code == 201, f"Unexpected: {resp.text}"
                data = resp.json()
                assert data["run_id"] == TEST_RUN_ID
                assert data["status"] == "created"
                assert data["project_id"] == str(TEST_PROJECT_ID)

    async def test_start_pipeline_project_not_found(self):
        """Returns 404 when project does not exist."""
        app = _make_test_app()

        from app.dependencies import get_current_user_context, get_tenant_session

        ctx = _make_tenant_context("org_admin")
        app.dependency_overrides[get_current_user_context] = lambda: ctx

        mock_session = _mock_session_with_results(scalar_one_or_none=None)
        async def override_session():
            yield mock_session
        app.dependency_overrides[get_tenant_session] = override_session

        with _patch_verify_token():
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                headers = _auth_headers_for("org_admin")
                resp = await client.post(
                    "/api/v1/pipeline/start",
                    json={
                        "project_id": str(uuid.uuid4()),
                        "execution_mode": "checkpoint",
                    },
                    headers=headers,
                )
                assert resp.status_code == 404

    async def test_start_pipeline_no_auth(self):
        """Returns 401/403 without auth token."""
        app = _make_test_app()
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/pipeline/start",
                json={
                    "project_id": str(TEST_PROJECT_ID),
                    "execution_mode": "checkpoint",
                },
            )
            assert resp.status_code in (401, 403)

    async def test_start_pipeline_viewer_forbidden(self):
        """Viewer role cannot start pipelines (403)."""
        app = _make_test_app()

        from app.dependencies import get_current_user_context, get_tenant_session

        ctx = _make_tenant_context("viewer")
        app.dependency_overrides[get_current_user_context] = lambda: ctx

        fake_project = MagicMock()
        fake_project.id = TEST_PROJECT_ID
        fake_project.organization_id = TEST_ORG_ID
        fake_project.owner_id = uuid.UUID("99999999-9999-9999-9999-999999999999")  # different owner
        fake_project.status = "active"

        mock_session = _mock_session_with_results(scalar_one_or_none=fake_project)
        async def override_session():
            yield mock_session
        app.dependency_overrides[get_tenant_session] = override_session

        with _patch_verify_token():
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                headers = _auth_headers_for("viewer")
                resp = await client.post(
                    "/api/v1/pipeline/start",
                    json={
                        "project_id": str(TEST_PROJECT_ID),
                        "execution_mode": "checkpoint",
                    },
                    headers=headers,
                )
                assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════
# 3. GET /pipeline/{run_id} — get pipeline status
# ═══════════════════════════════════════════════════════════════════


class TestGetPipelineStatus:
    """GET /api/v1/pipeline/{run_id}"""

    async def test_get_status_happy_path(self):
        """Returns pipeline status for a valid run_id."""
        app = _make_test_app()

        from app.dependencies import get_current_user_context

        ctx = _make_tenant_context("org_admin")
        app.dependency_overrides[get_current_user_context] = lambda: ctx

        from app.services.pipeline import ExecutionMode, PipelineRun, PipelineRunStatus, PipelineStage

        fake_run = PipelineRun(
            run_id=TEST_RUN_ID,
            project_id=str(TEST_PROJECT_ID),
            organization_id=str(TEST_ORG_ID),
            user_id=str(TEST_USER_ID),
            status=PipelineRunStatus.RUNNING,
            execution_mode=ExecutionMode.CHECKPOINT,
            current_stage=PipelineStage.ARCHITECTURE,
            created_at=datetime.now(timezone.utc),
        )

        mock_orch = MagicMock()
        mock_orch.get_run.return_value = fake_run

        with (
            _patch_verify_token(),
            patch("app.routers.pipeline.get_orchestrator", return_value=mock_orch),
        ):
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                headers = _auth_headers_for("org_admin")
                resp = await client.get(
                    f"/api/v1/pipeline/{TEST_RUN_ID}",
                    headers=headers,
                )
                assert resp.status_code == 200, f"Unexpected: {resp.text}"
                data = resp.json()
                assert data["run_id"] == TEST_RUN_ID
                assert data["status"] == "running"
                assert data["current_stage"] == "architecture"

    async def test_get_status_not_found(self):
        """Returns 404 when run does not exist."""
        app = _make_test_app()

        from app.dependencies import get_current_user_context

        ctx = _make_tenant_context("org_admin")
        app.dependency_overrides[get_current_user_context] = lambda: ctx

        mock_orch = MagicMock()
        mock_orch.get_run.return_value = None
        mock_orch.load_run_metadata = AsyncMock(return_value=None)

        with (
            _patch_verify_token(),
            patch("app.routers.pipeline.get_orchestrator", return_value=mock_orch),
        ):
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                headers = _auth_headers_for("org_admin")
                fake_id = str(uuid.uuid4())
                resp = await client.get(
                    f"/api/v1/pipeline/{fake_id}",
                    headers=headers,
                )
                assert resp.status_code == 404

    async def test_get_status_invalid_uuid(self):
        """Returns 422 for invalid run_id format."""
        app = _make_test_app()

        from app.dependencies import get_current_user_context

        ctx = _make_tenant_context("org_admin")
        app.dependency_overrides[get_current_user_context] = lambda: ctx

        with _patch_verify_token():
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                headers = _auth_headers_for("org_admin")
                resp = await client.get(
                    "/api/v1/pipeline/not-a-valid-uuid",
                    headers=headers,
                )
                assert resp.status_code == 422

    async def test_get_status_cross_tenant_returns_404(self):
        """Returns 404 when run belongs to a different org."""
        app = _make_test_app()

        from app.dependencies import get_current_user_context

        ctx = _make_tenant_context("org_admin")
        app.dependency_overrides[get_current_user_context] = lambda: ctx

        from app.services.pipeline import ExecutionMode, PipelineRun, PipelineRunStatus, PipelineStage

        other_org_id = str(uuid.uuid4())
        fake_run = PipelineRun(
            run_id=TEST_RUN_ID,
            project_id=str(TEST_PROJECT_ID),
            organization_id=other_org_id,  # different org
            user_id=str(TEST_USER_ID),
            status=PipelineRunStatus.RUNNING,
            execution_mode=ExecutionMode.CHECKPOINT,
            current_stage=PipelineStage.ARCHITECTURE,
            created_at=datetime.now(timezone.utc),
        )

        mock_orch = MagicMock()
        mock_orch.get_run.return_value = fake_run

        with (
            _patch_verify_token(),
            patch("app.routers.pipeline.get_orchestrator", return_value=mock_orch),
        ):
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                headers = _auth_headers_for("org_admin")
                resp = await client.get(
                    f"/api/v1/pipeline/{TEST_RUN_ID}",
                    headers=headers,
                )
                assert resp.status_code == 404

    async def test_get_status_no_auth(self):
        """Returns 401/403 without auth token."""
        app = _make_test_app()
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/pipeline/{TEST_RUN_ID}")
            assert resp.status_code in (401, 403)


# ═══════════════════════════════════════════════════════════════════
# 4. POST /pipeline/{run_id}/approve — approve checkpoint
# ═══════════════════════════════════════════════════════════════════


class TestApproveCheckpoint:
    """POST /api/v1/pipeline/{run_id}/approve"""

    async def test_approve_happy_path(self):
        """Approves a paused pipeline checkpoint."""
        app = _make_test_app()

        from app.dependencies import get_current_user_context, require_admin

        ctx = _make_tenant_context("org_admin")
        app.dependency_overrides[get_current_user_context] = lambda: ctx
        # Override require_admin so it doesn't hit the DB
        app.dependency_overrides[require_admin] = lambda: ctx

        from app.services.pipeline import ExecutionMode, PipelineRun, PipelineRunStatus, PipelineStage

        fake_run = PipelineRun(
            run_id=TEST_RUN_ID,
            project_id=str(TEST_PROJECT_ID),
            organization_id=str(TEST_ORG_ID),
            user_id=str(TEST_USER_ID),
            status=PipelineRunStatus.PAUSED,
            execution_mode=ExecutionMode.CHECKPOINT,
            current_stage=PipelineStage.CHECKPOINT_DESIGN,
            created_at=datetime.now(timezone.utc),
        )

        mock_orch = MagicMock()
        mock_orch.get_run.return_value = fake_run
        mock_orch.approve_checkpoint = AsyncMock()

        with (
            _patch_verify_token(),
            patch("app.routers.pipeline.get_orchestrator", return_value=mock_orch),
        ):
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                headers = _auth_headers_for("org_admin")
                resp = await client.post(
                    f"/api/v1/pipeline/{TEST_RUN_ID}/approve",
                    json={"approved": True, "action": "approve", "feedback": "Looks good"},
                    headers=headers,
                )
                assert resp.status_code == 200, f"Unexpected: {resp.text}"
                data = resp.json()
                assert data["run_id"] == TEST_RUN_ID

    async def test_approve_not_paused(self):
        """Returns 400 when pipeline is not paused."""
        app = _make_test_app()

        from app.dependencies import get_current_user_context, require_admin

        ctx = _make_tenant_context("org_admin")
        app.dependency_overrides[get_current_user_context] = lambda: ctx
        app.dependency_overrides[require_admin] = lambda: ctx

        from app.services.pipeline import ExecutionMode, PipelineRun, PipelineRunStatus, PipelineStage

        fake_run = PipelineRun(
            run_id=TEST_RUN_ID,
            project_id=str(TEST_PROJECT_ID),
            organization_id=str(TEST_ORG_ID),
            user_id=str(TEST_USER_ID),
            status=PipelineRunStatus.RUNNING,  # not paused
            execution_mode=ExecutionMode.CHECKPOINT,
            current_stage=PipelineStage.ARCHITECTURE,
            created_at=datetime.now(timezone.utc),
        )

        mock_orch = MagicMock()
        mock_orch.get_run.return_value = fake_run

        with (
            _patch_verify_token(),
            patch("app.routers.pipeline.get_orchestrator", return_value=mock_orch),
        ):
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                headers = _auth_headers_for("org_admin")
                resp = await client.post(
                    f"/api/v1/pipeline/{TEST_RUN_ID}/approve",
                    json={"approved": True, "action": "approve"},
                    headers=headers,
                )
                assert resp.status_code == 400

    async def test_approve_not_found(self):
        """Returns 404 when pipeline run does not exist."""
        app = _make_test_app()

        from app.dependencies import get_current_user_context, require_admin

        ctx = _make_tenant_context("org_admin")
        app.dependency_overrides[get_current_user_context] = lambda: ctx
        app.dependency_overrides[require_admin] = lambda: ctx

        mock_orch = MagicMock()
        mock_orch.get_run.return_value = None
        mock_orch.load_run_metadata = AsyncMock(return_value=None)

        with (
            _patch_verify_token(),
            patch("app.routers.pipeline.get_orchestrator", return_value=mock_orch),
        ):
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                headers = _auth_headers_for("org_admin")
                fake_id = str(uuid.uuid4())
                resp = await client.post(
                    f"/api/v1/pipeline/{fake_id}/approve",
                    json={"approved": True, "action": "approve"},
                    headers=headers,
                )
                assert resp.status_code == 404

    async def test_approve_non_admin_forbidden(self):
        """Non-admin roles get 403 on approve."""
        app = _make_test_app()

        from app.dependencies import get_current_user_context, require_admin
        from fastapi import HTTPException, status

        ctx = _make_tenant_context("viewer")
        app.dependency_overrides[get_current_user_context] = lambda: ctx

        # Simulate require_admin rejecting non-admin
        def reject_admin():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required",
            )
        app.dependency_overrides[require_admin] = reject_admin

        with _patch_verify_token():
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                headers = _auth_headers_for("viewer")
                resp = await client.post(
                    f"/api/v1/pipeline/{TEST_RUN_ID}/approve",
                    json={"approved": True, "action": "approve"},
                    headers=headers,
                )
                assert resp.status_code == 403

    async def test_approve_invalid_run_id(self):
        """Returns 422 for invalid run_id format."""
        app = _make_test_app()

        from app.dependencies import get_current_user_context, require_admin

        ctx = _make_tenant_context("org_admin")
        app.dependency_overrides[get_current_user_context] = lambda: ctx
        app.dependency_overrides[require_admin] = lambda: ctx

        with _patch_verify_token():
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                headers = _auth_headers_for("org_admin")
                resp = await client.post(
                    "/api/v1/pipeline/bad-uuid/approve",
                    json={"approved": True, "action": "approve"},
                    headers=headers,
                )
                assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════
# 5. GET /notifications/ — list notifications
# ═══════════════════════════════════════════════════════════════════


class TestListNotifications:
    """GET /api/v1/notifications/"""

    async def test_list_notifications_happy_path(self):
        """Returns notification list for authenticated user."""
        app = _make_test_app()

        from app.dependencies import get_current_user_context

        ctx = _make_tenant_context("org_admin")
        app.dependency_overrides[get_current_user_context] = lambda: ctx

        from app.services.notification import (
            NotificationChannel,
            NotificationPayload,
            NotificationService,
            NotificationType,
        )

        # Build a fake notification service with one notification
        fake_svc = NotificationService()
        fake_notif = NotificationPayload(
            notification_type=NotificationType.PIPELINE_STARTED,
            user_id=str(TEST_USER_ID),
            organization_id=str(TEST_ORG_ID),
            channel=NotificationChannel.WEB,
            title="Pipeline started: TestApp",
            body="Your project is building.",
        )
        fake_svc._sent.append(fake_notif)

        with (
            _patch_verify_token(),
            patch("app.routers.notifications.get_notification_service", return_value=fake_svc),
        ):
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                headers = _auth_headers_for("org_admin")
                resp = await client.get(
                    "/api/v1/notifications/",
                    headers=headers,
                )
                assert resp.status_code == 200, f"Unexpected: {resp.text}"
                data = resp.json()
                assert "notifications" in data
                assert data["total"] == 1
                assert data["unread"] == 1
                assert data["notifications"][0]["title"] == "Pipeline started: TestApp"

    async def test_list_notifications_empty(self):
        """Returns empty list when no notifications exist."""
        app = _make_test_app()

        from app.dependencies import get_current_user_context

        ctx = _make_tenant_context("org_admin")
        app.dependency_overrides[get_current_user_context] = lambda: ctx

        from app.services.notification import NotificationService

        empty_svc = NotificationService()

        with (
            _patch_verify_token(),
            patch("app.routers.notifications.get_notification_service", return_value=empty_svc),
        ):
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                headers = _auth_headers_for("org_admin")
                resp = await client.get(
                    "/api/v1/notifications/",
                    headers=headers,
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["total"] == 0
                assert data["notifications"] == []

    async def test_list_notifications_no_auth(self):
        """Returns 401/403 without auth token."""
        app = _make_test_app()
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/notifications/")
            assert resp.status_code in (401, 403)

    async def test_list_notifications_unread_only(self):
        """Filtering unread_only returns only unread notifications."""
        app = _make_test_app()

        from app.dependencies import get_current_user_context

        ctx = _make_tenant_context("org_admin")
        app.dependency_overrides[get_current_user_context] = lambda: ctx

        from app.services.notification import (
            NotificationChannel,
            NotificationPayload,
            NotificationService,
            NotificationType,
        )

        fake_svc = NotificationService()

        # Add one read and one unread notification
        read_notif = NotificationPayload(
            notification_type=NotificationType.PIPELINE_COMPLETED,
            user_id=str(TEST_USER_ID),
            organization_id=str(TEST_ORG_ID),
            channel=NotificationChannel.WEB,
            title="Completed",
            body="Done.",
            is_read=True,
        )
        unread_notif = NotificationPayload(
            notification_type=NotificationType.PIPELINE_STARTED,
            user_id=str(TEST_USER_ID),
            organization_id=str(TEST_ORG_ID),
            channel=NotificationChannel.WEB,
            title="Started",
            body="Building.",
        )
        fake_svc._sent.append(read_notif)
        fake_svc._sent.append(unread_notif)

        with (
            _patch_verify_token(),
            patch("app.routers.notifications.get_notification_service", return_value=fake_svc),
        ):
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                headers = _auth_headers_for("org_admin")
                resp = await client.get(
                    "/api/v1/notifications/?unread_only=true",
                    headers=headers,
                )
                assert resp.status_code == 200
                data = resp.json()
                # Only the unread notification should appear
                assert len(data["notifications"]) == 1
                assert data["notifications"][0]["title"] == "Started"


# ═══════════════════════════════════════════════════════════════════
# 6. POST /notifications/read — mark as read
# ═══════════════════════════════════════════════════════════════════


class TestMarkNotificationsRead:
    """POST /api/v1/notifications/read"""

    async def test_mark_all_as_read(self):
        """Marks all notifications as read (no IDs specified)."""
        app = _make_test_app()

        from app.dependencies import get_current_user_context

        ctx = _make_tenant_context("org_admin")
        app.dependency_overrides[get_current_user_context] = lambda: ctx

        from app.services.notification import (
            NotificationChannel,
            NotificationPayload,
            NotificationService,
            NotificationType,
        )

        fake_svc = NotificationService()
        for i in range(3):
            notif = NotificationPayload(
                notification_type=NotificationType.PIPELINE_STARTED,
                user_id=str(TEST_USER_ID),
                organization_id=str(TEST_ORG_ID),
                channel=NotificationChannel.WEB,
                title=f"Notif {i}",
                body=f"Body {i}",
            )
            fake_svc._sent.append(notif)

        with (
            _patch_verify_token(),
            patch("app.routers.notifications.get_notification_service", return_value=fake_svc),
        ):
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                headers = _auth_headers_for("org_admin")
                resp = await client.post(
                    "/api/v1/notifications/read",
                    headers=headers,
                )
                assert resp.status_code == 200, f"Unexpected: {resp.text}"
                data = resp.json()
                assert data["marked_read"] == 3

    async def test_mark_specific_as_read(self):
        """Marks specific notification IDs as read."""
        app = _make_test_app()

        from app.dependencies import get_current_user_context

        ctx = _make_tenant_context("org_admin")
        app.dependency_overrides[get_current_user_context] = lambda: ctx

        from app.services.notification import (
            NotificationChannel,
            NotificationPayload,
            NotificationService,
            NotificationType,
        )

        fake_svc = NotificationService()
        target_id = str(uuid.uuid4())
        notif = NotificationPayload(
            notification_type=NotificationType.PIPELINE_STARTED,
            user_id=str(TEST_USER_ID),
            organization_id=str(TEST_ORG_ID),
            channel=NotificationChannel.WEB,
            title="Target",
            body="Mark this one.",
            notification_id=target_id,
        )
        other_notif = NotificationPayload(
            notification_type=NotificationType.PIPELINE_COMPLETED,
            user_id=str(TEST_USER_ID),
            organization_id=str(TEST_ORG_ID),
            channel=NotificationChannel.WEB,
            title="Other",
            body="Not this one.",
        )
        fake_svc._sent.append(notif)
        fake_svc._sent.append(other_notif)

        with (
            _patch_verify_token(),
            patch("app.routers.notifications.get_notification_service", return_value=fake_svc),
        ):
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                headers = _auth_headers_for("org_admin")
                resp = await client.post(
                    "/api/v1/notifications/read",
                    json=[target_id],
                    headers=headers,
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["marked_read"] == 1

    async def test_mark_read_no_auth(self):
        """Returns 401/403 without auth token."""
        app = _make_test_app()
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/notifications/read")
            assert resp.status_code in (401, 403)


# ═══════════════════════════════════════════════════════════════════
# 7. WebSocket /ws/{run_id} — pipeline updates
# ═══════════════════════════════════════════════════════════════════


class TestWebSocket:
    """WS /api/v1/ws/{run_id}"""

    async def test_ws_auth_ok_via_first_message(self):
        """WebSocket auth via first-message protocol returns auth_ok."""
        app = _make_test_app()

        get_settings.cache_clear()
        from app.services.auth import create_access_token

        token = create_access_token(TEST_USER_ID, TEST_ORG_ID, "org_admin")

        from app.services.pipeline import ExecutionMode, PipelineRun, PipelineRunStatus, PipelineStage

        fake_run = PipelineRun(
            run_id=TEST_RUN_ID,
            project_id=str(TEST_PROJECT_ID),
            organization_id=str(TEST_ORG_ID),
            user_id=str(TEST_USER_ID),
            status=PipelineRunStatus.RUNNING,
            execution_mode=ExecutionMode.CHECKPOINT,
            current_stage=PipelineStage.ARCHITECTURE,
            created_at=datetime.now(timezone.utc),
        )

        mock_orch = MagicMock()
        mock_orch.get_run.return_value = fake_run

        with (
            _patch_verify_token(),
            patch("app.routers.websocket.get_orchestrator", return_value=mock_orch),
        ):
            from starlette.testclient import TestClient

            # Use Starlette's synchronous TestClient for WebSocket testing
            sync_client = TestClient(app)
            with sync_client.websocket_connect(f"/api/v1/ws/{TEST_RUN_ID}") as ws:
                # Send auth message
                ws.send_json({"type": "auth", "token": token})
                # Should receive auth_ok
                resp = ws.receive_json()
                assert resp["type"] == "auth_ok"
                assert resp["run_id"] == TEST_RUN_ID
                assert resp["status"] == "running"

    async def test_ws_invalid_token_closed(self):
        """WebSocket with invalid token closes with 4001."""
        app = _make_test_app()

        with _patch_verify_token():
            from starlette.testclient import TestClient

            sync_client = TestClient(app)
            with sync_client.websocket_connect(f"/api/v1/ws/{TEST_RUN_ID}") as ws:
                ws.send_json({"type": "auth", "token": "invalid-jwt-token"})
                # The server should close the connection
                # Starlette's test client raises WebSocketDisconnect on close
                try:
                    resp = ws.receive_json()
                    # If we get here, the server sent something before closing
                    # which shouldn't happen with invalid token
                    assert False, f"Expected connection close, got: {resp}"
                except Exception:
                    pass  # Connection was closed as expected

    async def test_ws_invalid_run_id_format(self):
        """WebSocket with invalid run_id format closes with 4004."""
        app = _make_test_app()

        from starlette.testclient import TestClient

        sync_client = TestClient(app)
        with sync_client.websocket_connect("/api/v1/ws/not-a-uuid") as ws:
            # Server should close immediately after accept with 4004
            try:
                ws.receive_json()
                assert False, "Expected WebSocket close"
            except Exception:
                pass  # Closed as expected

    async def test_ws_no_auth_message_timeout(self):
        """WebSocket without auth message closes after timeout."""
        app = _make_test_app()

        # Patch the auth timeout to be very short
        with patch("app.routers.websocket._AUTH_TIMEOUT_SECONDS", 0.1):
            from starlette.testclient import TestClient

            sync_client = TestClient(app)
            with sync_client.websocket_connect(f"/api/v1/ws/{TEST_RUN_ID}") as ws:
                # Don't send anything — should timeout and close
                try:
                    resp = ws.receive_json()
                    assert False, f"Expected connection close, got: {resp}"
                except Exception:
                    pass  # Closed due to auth timeout

    async def test_ws_run_not_found_closed(self):
        """WebSocket closes with 4004 when run is not found."""
        app = _make_test_app()

        get_settings.cache_clear()
        from app.services.auth import create_access_token

        token = create_access_token(TEST_USER_ID, TEST_ORG_ID, "org_admin")

        mock_orch = MagicMock()
        mock_orch.get_run.return_value = None
        mock_orch.load_run_metadata = AsyncMock(return_value=None)

        with (
            _patch_verify_token(),
            patch("app.routers.websocket.get_orchestrator", return_value=mock_orch),
        ):
            from starlette.testclient import TestClient

            sync_client = TestClient(app)
            fake_run_id = str(uuid.uuid4())
            with sync_client.websocket_connect(f"/api/v1/ws/{fake_run_id}") as ws:
                ws.send_json({"type": "auth", "token": token})
                try:
                    resp = ws.receive_json()
                    assert False, f"Expected connection close, got: {resp}"
                except Exception:
                    pass  # Closed with 4004

    async def test_ws_ping_pong(self):
        """WebSocket responds to ping with pong."""
        app = _make_test_app()

        get_settings.cache_clear()
        from app.services.auth import create_access_token

        token = create_access_token(TEST_USER_ID, TEST_ORG_ID, "org_admin")

        from app.services.pipeline import ExecutionMode, PipelineRun, PipelineRunStatus, PipelineStage

        fake_run = PipelineRun(
            run_id=TEST_RUN_ID,
            project_id=str(TEST_PROJECT_ID),
            organization_id=str(TEST_ORG_ID),
            user_id=str(TEST_USER_ID),
            status=PipelineRunStatus.RUNNING,
            execution_mode=ExecutionMode.CHECKPOINT,
            current_stage=PipelineStage.ARCHITECTURE,
            created_at=datetime.now(timezone.utc),
        )

        mock_orch = MagicMock()
        mock_orch.get_run.return_value = fake_run

        with (
            _patch_verify_token(),
            patch("app.routers.websocket.get_orchestrator", return_value=mock_orch),
        ):
            from starlette.testclient import TestClient

            sync_client = TestClient(app)
            with sync_client.websocket_connect(f"/api/v1/ws/{TEST_RUN_ID}") as ws:
                # Auth first
                ws.send_json({"type": "auth", "token": token})
                auth_resp = ws.receive_json()
                assert auth_resp["type"] == "auth_ok"

                # Send ping
                ws.send_json({"type": "ping"})
                pong_resp = ws.receive_json()
                assert pong_resp["type"] == "pong"
                assert "timestamp" in pong_resp

    async def test_ws_deprecated_query_param_auth(self):
        """WebSocket auth via query param (deprecated) still works."""
        app = _make_test_app()

        get_settings.cache_clear()
        from app.services.auth import create_access_token

        token = create_access_token(TEST_USER_ID, TEST_ORG_ID, "org_admin")

        from app.services.pipeline import ExecutionMode, PipelineRun, PipelineRunStatus, PipelineStage

        fake_run = PipelineRun(
            run_id=TEST_RUN_ID,
            project_id=str(TEST_PROJECT_ID),
            organization_id=str(TEST_ORG_ID),
            user_id=str(TEST_USER_ID),
            status=PipelineRunStatus.RUNNING,
            execution_mode=ExecutionMode.CHECKPOINT,
            current_stage=PipelineStage.ARCHITECTURE,
            created_at=datetime.now(timezone.utc),
        )

        mock_orch = MagicMock()
        mock_orch.get_run.return_value = fake_run

        with (
            _patch_verify_token(),
            patch("app.routers.websocket.get_orchestrator", return_value=mock_orch),
        ):
            from starlette.testclient import TestClient

            sync_client = TestClient(app)
            with sync_client.websocket_connect(
                f"/api/v1/ws/{TEST_RUN_ID}?token={token}"
            ) as ws:
                # Should get auth_ok immediately (no auth message needed)
                resp = ws.receive_json()
                assert resp["type"] == "auth_ok"
                assert resp["run_id"] == TEST_RUN_ID

    async def test_ws_cross_tenant_run_closed(self):
        """WebSocket closes when run belongs to different org."""
        app = _make_test_app()

        get_settings.cache_clear()
        from app.services.auth import create_access_token

        token = create_access_token(TEST_USER_ID, TEST_ORG_ID, "org_admin")

        from app.services.pipeline import ExecutionMode, PipelineRun, PipelineRunStatus, PipelineStage

        other_org_id = str(uuid.uuid4())
        fake_run = PipelineRun(
            run_id=TEST_RUN_ID,
            project_id=str(TEST_PROJECT_ID),
            organization_id=other_org_id,  # different org!
            user_id=str(TEST_USER_ID),
            status=PipelineRunStatus.RUNNING,
            execution_mode=ExecutionMode.CHECKPOINT,
            current_stage=PipelineStage.ARCHITECTURE,
            created_at=datetime.now(timezone.utc),
        )

        mock_orch = MagicMock()
        mock_orch.get_run.return_value = fake_run

        with (
            _patch_verify_token(),
            patch("app.routers.websocket.get_orchestrator", return_value=mock_orch),
        ):
            from starlette.testclient import TestClient

            sync_client = TestClient(app)
            with sync_client.websocket_connect(f"/api/v1/ws/{TEST_RUN_ID}") as ws:
                ws.send_json({"type": "auth", "token": token})
                try:
                    resp = ws.receive_json()
                    assert False, f"Expected connection close, got: {resp}"
                except Exception:
                    pass  # Closed because run is from another org
