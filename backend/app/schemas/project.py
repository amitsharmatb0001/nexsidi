"""Pydantic schemas for project, pipeline, chat, and notification APIs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Project Schemas ───────────────────────────────────────────────


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field("", max_length=2000)
    tech_stack: dict[str, str] = Field(default_factory=dict)


class ProjectUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)


class ProjectResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    organization_id: uuid.UUID
    created_at: datetime
    status: str = "active"

    model_config = {"from_attributes": True}


class ProjectListResponse(BaseModel):
    projects: list[ProjectResponse]
    total: int


# ── Pipeline Schemas ──────────────────────────────────────────────


class PipelineStartRequest(BaseModel):
    project_id: uuid.UUID
    execution_mode: str = Field("checkpoint", pattern="^(step_by_step|checkpoint|direct)$")
    requirements: str = Field("", max_length=10000)


class PipelineStatusResponse(BaseModel):
    run_id: str
    project_id: str
    status: str
    current_stage: str
    execution_mode: str
    created_at: str
    step_results: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class CheckpointApprovalRequest(BaseModel):
    approved: bool
    feedback: str = Field("", max_length=2000)


# ── Chat Schemas ──────────────────────────────────────────────────


class ChatMessageCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)
    attachments: dict[str, Any] | None = None


class ChatMessageResponse(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    role: str
    content: str
    attachments: dict[str, Any] | None = None
    token_count: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ChatSessionResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    agent_name: str | None = None
    channel: str
    status: str
    created_at: datetime
    messages: list[ChatMessageResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class ChatSessionListResponse(BaseModel):
    sessions: list[ChatSessionResponse]
    total: int


# ── Notification Schemas ──────────────────────────────────────────


class NotificationResponse(BaseModel):
    id: uuid.UUID | None = None
    title: str
    body: str | None = None
    link: str | None = None
    channel: str
    read: bool = False
    created_at: str

    model_config = {"from_attributes": True}


class NotificationListResponse(BaseModel):
    notifications: list[NotificationResponse]
    total: int
    unread: int
