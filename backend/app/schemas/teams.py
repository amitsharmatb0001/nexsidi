"""Pydantic schemas for team management endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class TeamCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Team display name")


class TeamUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)


class TeamMemberResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    role: str
    joined_at: datetime

    model_config = {"from_attributes": True}


class TeamResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    created_at: datetime
    member_count: int = 0

    model_config = {"from_attributes": True}


class TeamDetailResponse(TeamResponse):
    """TeamResponse with the full member list included."""
    members: list[TeamMemberResponse] = Field(default_factory=list)


class TeamListResponse(BaseModel):
    teams: list[TeamResponse]
    total: int


class TeamMemberAdd(BaseModel):
    user_id: uuid.UUID
    # C-2-FIX: Literal allowlist prevents arbitrary role injection.
    # Previously a plain str — any string (including "super_admin" or SQL
    # injection payloads) would be stored without validation.
    role: Literal["member", "lead", "admin"] = Field(
        default="member",
        description="Role within the team: member, lead, or admin",
    )
