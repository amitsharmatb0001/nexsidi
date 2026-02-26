"""Tests for Pydantic schemas."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError


class TestAuthSchemas:
    """Test auth schema validation."""

    def test_register_valid(self):
        from app.schemas.auth import UserRegister
        req = UserRegister(
            email="test@example.com",
            password="SecurePass123",
            name="Test User",
            organization_name="Test Org",
        )
        assert req.email == "test@example.com"
        assert req.name == "Test User"

    def test_register_invalid_email(self):
        from app.schemas.auth import UserRegister
        with pytest.raises(ValidationError):
            UserRegister(
                email="not-an-email",
                password="SecurePass123",
                name="Test User",
                organization_name="Test Org",
            )

    def test_login_valid(self):
        from app.schemas.auth import UserLogin
        req = UserLogin(email="test@example.com", password="password123")
        assert req.email == "test@example.com"


class TestProjectSchemas:
    """Test project schema validation."""

    def test_project_create(self):
        from app.schemas.project import ProjectCreate
        proj = ProjectCreate(
            name="My App",
            description="An app",
            tech_stack={"backend": "FastAPI", "frontend": "Next.js"},
        )
        assert proj.name == "My App"

    def test_project_create_minimal(self):
        from app.schemas.project import ProjectCreate
        proj = ProjectCreate(name="Minimal")
        assert proj.name == "Minimal"

    def test_pipeline_start_request(self):
        from app.schemas.project import PipelineStartRequest
        req = PipelineStartRequest(
            project_id=uuid.uuid4(),
            execution_mode="checkpoint",
        )
        assert req.execution_mode == "checkpoint"

    def test_pipeline_start_invalid_mode(self):
        from app.schemas.project import PipelineStartRequest
        with pytest.raises(ValidationError):
            PipelineStartRequest(
                project_id=uuid.uuid4(),
                execution_mode="invalid_mode",
            )

    def test_checkpoint_approval(self):
        from app.schemas.project import CheckpointApprovalRequest
        req = CheckpointApprovalRequest(approved=True, feedback="Looks good")
        assert req.approved is True

    def test_chat_message_create(self):
        from app.schemas.project import ChatMessageCreate
        msg = ChatMessageCreate(content="Hello, build me an app")
        assert msg.content == "Hello, build me an app"
