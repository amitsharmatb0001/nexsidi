"""Pydantic schemas for API key management endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Human-readable label for this key")
    scope: list[str] = Field(default_factory=lambda: ["pipeline:run"], description="Permission scopes")
    rate_limit: int = Field(default=60, ge=1, le=10000, description="Max requests per minute")
    expires_in_days: int | None = Field(
        default=None, ge=1, le=3650, description="Key TTL in days; None = never expires"
    )


class ApiKeyResponse(BaseModel):
    id: uuid.UUID
    name: str
    key_prefix: str
    scope: list[str]
    rate_limit: int
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ApiKeyCreateResponse(ApiKeyResponse):
    """Returned only on creation -- includes the full plaintext key once."""
    key: str = Field(..., description="Full API key -- store it now, it will never be shown again")


class ApiKeyUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    scope: list[str] | None = None
    rate_limit: int | None = Field(None, ge=1, le=10000)
    expires_in_days: int | None = Field(
        None, ge=1, le=3650, description="Resets TTL from now; None = clear expiry"
    )
