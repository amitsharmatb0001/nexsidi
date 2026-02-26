"""Shared pytest fixtures for the NexSidi backend test suite.

Sets up:
- Environment variables for Settings (no real DB/Valkey needed)
- FastAPI test client via httpx
- JWT token helpers for authenticated requests
- Mock fixtures for external dependencies
"""

from __future__ import annotations

import os
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# Set environment BEFORE any app import so Settings picks them up
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/nexsidi_test")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-that-is-at-least-32-chars-long-for-testing")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-key")
os.environ.setdefault("GOOGLE_AI_API_KEY", "test-google-key")
os.environ.setdefault("VALKEY_URL", "redis://localhost:6379/15")
os.environ.setdefault("ENVIRONMENT", "development")

from app.config import get_settings  # noqa: E402
from app.services.auth import create_access_token  # noqa: E402


# ── Test IDs ───────────────────────────────────────────────────

TEST_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000010")
TEST_PROJECT_ID = uuid.UUID("00000000-0000-0000-0000-000000000100")


# ── Settings Fixture ───────────────────────────────────────────

@pytest.fixture(scope="session")
def settings():
    """Return test settings instance."""
    # Clear LRU cache to pick up test env vars
    get_settings.cache_clear()
    return get_settings()


# ── JWT Token Fixtures ─────────────────────────────────────────

@pytest.fixture
def auth_token() -> str:
    """Valid JWT access token for test user."""
    get_settings.cache_clear()
    return create_access_token(TEST_USER_ID, TEST_ORG_ID, "member")


@pytest.fixture
def admin_token() -> str:
    """Valid JWT access token with admin role."""
    get_settings.cache_clear()
    return create_access_token(TEST_USER_ID, TEST_ORG_ID, "org_admin")


@pytest.fixture
def auth_headers(auth_token: str) -> dict[str, str]:
    """Authorization headers for authenticated requests."""
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture
def admin_headers(admin_token: str) -> dict[str, str]:
    """Authorization headers for admin requests."""
    return {"Authorization": f"Bearer {admin_token}"}


# ── FastAPI Test Client ────────────────────────────────────────

@pytest.fixture
async def app():
    """Create a FastAPI app with mocked lifespan (no real DB/Valkey)."""
    from collections.abc import AsyncIterator
    from contextlib import asynccontextmanager

    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    @asynccontextmanager
    async def test_lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Test lifespan — skips DB and Valkey initialization."""
        yield

    settings = get_settings()

    test_app = FastAPI(
        title="NexSidi Test",
        lifespan=test_lifespan,
    )

    test_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Health check
    @test_app.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        return {"status": "healthy", "version": "2.0.0"}

    # Mount routers
    from app.routers.auth import router as auth_router
    from app.routers.projects import router as projects_router
    from app.routers.pipeline import router as pipeline_router
    from app.routers.chat import router as chat_router
    from app.routers.notifications import router as notifications_router
    from app.routers.websocket import router as ws_router

    test_app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
    test_app.include_router(projects_router, prefix="/api/v1/projects", tags=["projects"])
    test_app.include_router(pipeline_router, prefix="/api/v1/pipeline", tags=["pipeline"])
    test_app.include_router(chat_router, prefix="/api/v1/chat", tags=["chat"])
    test_app.include_router(notifications_router, prefix="/api/v1/notifications", tags=["notifications"])
    test_app.include_router(ws_router, prefix="/api/v1", tags=["websocket"])

    return test_app


@pytest.fixture
async def client(app) -> AsyncClient:
    """Async HTTP test client."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── Agent Fixtures ─────────────────────────────────────────────

@pytest.fixture
def pipeline_context() -> dict[str, Any]:
    """Sample pipeline context for agent testing."""
    return {
        "vikram": {
            "contract": {
                "project_name": "TestApp",
                "tech_stack": {"backend": "FastAPI", "frontend": "Next.js"},
                "database": {"primary": "PostgreSQL"},
            },
        },
        "shubham": {
            "file_contents": {
                "app/main.py": "from fastapi import FastAPI\napp = FastAPI()",
                "app/models.py": "from sqlalchemy import Column",
            },
        },
        "aanya": {
            "file_contents": {
                "src/App.tsx": "export default function App() { return <div>Hello</div> }",
                "src/index.tsx": "import App from './App'",
            },
        },
        "karan": {"total_findings": 2, "critical": 0, "high": 0},
        "navya": {"total_issues": 1},
        "deepika": {"total_issues": 0},
        "aarav": {"total_passed": 15, "total_failed": 0},
        "pranav": {
            "provider": "railway",
            "deployment_url": "https://testapp.railway.app",
        },
    }
