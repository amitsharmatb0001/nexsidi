"""Tests for FastAPI endpoints: health, auth headers, router mounting.

These tests use the httpx async client against the FastAPI test app.
Database-dependent routes return 500 (expected since no real DB),
but we verify the routing, auth, and response structure are correct.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


class TestHealthEndpoint:
    """Test the /health endpoint (no auth required)."""

    @pytest.mark.asyncio
    async def test_health_returns_200(self, client: AsyncClient):
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["version"] == "2.0.0"


class TestAuthRequired:
    """Test that protected endpoints reject unauthenticated requests."""

    @pytest.mark.asyncio
    async def test_projects_requires_auth(self, client: AsyncClient):
        resp = await client.get("/api/v1/projects/")
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_pipeline_requires_auth(self, client: AsyncClient):
        resp = await client.post("/api/v1/pipeline/start")
        assert resp.status_code in (401, 403, 422)

    @pytest.mark.asyncio
    async def test_chat_requires_auth(self, client: AsyncClient):
        resp = await client.get("/api/v1/chat/sessions/00000000-0000-0000-0000-000000000001")
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_notifications_requires_auth(self, client: AsyncClient):
        resp = await client.get("/api/v1/notifications/")
        assert resp.status_code in (401, 403)


class TestAuthHeaders:
    """Test that valid auth headers are accepted (routes may fail on DB but not on auth)."""

    @pytest.mark.asyncio
    async def test_projects_with_auth_not_401(self, client: AsyncClient, auth_headers):
        resp = await client.get("/api/v1/projects/", headers=auth_headers)
        # Should NOT be 401/403 -- it may be 500 (no DB) or 200
        assert resp.status_code not in (401, 403)

    @pytest.mark.asyncio
    async def test_notifications_with_auth_not_401(self, client: AsyncClient, auth_headers):
        resp = await client.get("/api/v1/notifications/", headers=auth_headers)
        assert resp.status_code not in (401, 403)


class TestRouterMounting:
    """Test that all routers are properly mounted."""

    @pytest.mark.asyncio
    async def test_auth_router_mounted(self, client: AsyncClient):
        # POST to /api/v1/auth/register should not 404
        resp = await client.post("/api/v1/auth/register", json={
            "email": "test@example.com",
            "password": "SecurePass123",
            "name": "Test User",
            "organization_name": "Test Org",
        })
        assert resp.status_code != 404

    @pytest.mark.asyncio
    async def test_projects_router_mounted(self, client: AsyncClient, auth_headers):
        resp = await client.get("/api/v1/projects/", headers=auth_headers)
        assert resp.status_code != 404

    @pytest.mark.asyncio
    async def test_pipeline_router_mounted(self, client: AsyncClient, auth_headers):
        resp = await client.post(
            "/api/v1/pipeline/start",
            headers=auth_headers,
            json={"project_id": "00000000-0000-0000-0000-000000000001", "execution_mode": "checkpoint"},
        )
        assert resp.status_code != 404

    @pytest.mark.asyncio
    async def test_chat_router_mounted(self, client: AsyncClient, auth_headers):
        resp = await client.post(
            "/api/v1/chat/sessions/00000000-0000-0000-0000-000000000001",
            headers=auth_headers,
        )
        assert resp.status_code != 404

    @pytest.mark.asyncio
    async def test_notifications_router_mounted(self, client: AsyncClient, auth_headers):
        resp = await client.get("/api/v1/notifications/", headers=auth_headers)
        assert resp.status_code != 404

    def test_websocket_router_mounted(self, app):
        # WebSocket routes only handle the WS protocol, not HTTP GET.
        # Verify the route exists by inspecting application routes.
        ws_found = any(
            "/ws/" in getattr(route, "path", "")
            and "WebSocket" in type(route).__name__
            for route in app.routes
        )
        assert ws_found, "WebSocket route /ws/{run_id} not found in app routes"
