"""Pydantic schemas for project, pipeline, chat, and notification APIs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Project Schemas ───────────────────────────────────────────────


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field("", max_length=2000)
    tech_stack: dict[str, str] = Field(default_factory=dict)

    # R38-FIX: Tightened limits — 20 entries, 50/200 char key/value caps.
    @field_validator("tech_stack")
    @classmethod
    def validate_tech_stack(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > 20:
            raise ValueError("tech_stack cannot exceed 20 entries")
        for k, val in v.items():
            if len(k) > 50 or len(str(val)) > 200:
                raise ValueError("tech_stack key/value too long")
        return v


class ProjectUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)


class ProjectResponse(BaseModel):
    id: uuid.UUID
    name: str
    # R26-FIX-10: Allow None — the DB column is nullable. Previously crashed
    # with ValidationError for projects with description=NULL.
    description: str | None = ""
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
    git_config: dict[str, str] | None = Field(
        None,
        description="Optional git integration config: {repo_url, token, branch, auto_pr}",
        json_schema_extra={"example": {"repo_url": "https://github.com/user/repo", "branch": "main", "auto_pr": "true"}}
    )  # GIT-FIX


class PipelineStatusResponse(BaseModel):
    run_id: str
    project_id: str
    status: str
    current_stage: str
    execution_mode: str
    created_at: str
    step_results: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    # Simulation transparency — True if ANY real-world integration ran in
    # simulation mode (no Docker, no GitHub token, no cloud credentials).
    # Clients MUST surface this to the user so they are never misled into
    # thinking a "completed" pipeline actually deployed/tested anything.
    is_partially_simulated: bool = False
    simulation_summary: dict[str, Any] = Field(default_factory=dict)


class CheckpointApprovalRequest(BaseModel):
    approved: bool
    action: str = Field("approve", pattern="^(approve|reject|redo)$")
    feedback: str = Field("", max_length=5000)


# ── Chat Schemas ──────────────────────────────────────────────────


class ChatMessageCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)
    attachments: dict[str, Any] | None = None

    # ATTACH-FIX: Prevent DoS via massive attachment payloads.
    # Without this, an attacker can POST multi-MB JSON in attachments.
    # R38-FIX: Also cap the number of top-level keys to prevent abuse.
    @model_validator(mode="after")
    def validate_attachments_size(self) -> ChatMessageCreate:
        if self.attachments is not None:
            if len(self.attachments) > 20:
                raise ValueError("attachments cannot exceed 20 entries")
            import orjson
            serialized = orjson.dumps(self.attachments)
            if len(serialized) > 102_400:  # 100 KB
                raise ValueError("Attachments JSON exceeds 100 KB limit")
        return self


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


# R29-FIX-2: Separate schema for list endpoint — does NOT include messages.
# R28-FIX-7 removed selectinload from list_chat_sessions, but the response
# schema still declared `messages`, causing model_validate to trigger async
# lazy-loading → MissingGreenlet crash.
class ChatSessionListItemResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    agent_name: str | None = None
    channel: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ChatSessionListResponse(BaseModel):
    sessions: list[ChatSessionListItemResponse]
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
