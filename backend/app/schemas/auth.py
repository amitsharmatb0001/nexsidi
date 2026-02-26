"""Pydantic schemas for auth endpoints.

Separate request (input) and response (output) schemas.
Never expose password_hash or internal IDs in responses.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator


# --- Requests ---


class UserRegister(BaseModel):
    """Registration request. Creates user + organization."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=255)
    organization_name: str = Field(min_length=1, max_length=255)

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        has_upper = any(c.isupper() for c in v)
        has_lower = any(c.islower() for c in v)
        has_digit = any(c.isdigit() for c in v)
        if not (has_upper and has_lower and has_digit):
            raise ValueError("Password must contain uppercase, lowercase, and a digit")
        return v


class UserLogin(BaseModel):
    """Login request."""

    email: EmailStr
    password: str


class TokenRefresh(BaseModel):
    """Refresh token request."""

    refresh_token: str


# --- Responses ---


class UserResponse(BaseModel):
    """Public user info. Never includes password_hash."""

    id: uuid.UUID
    email: str
    name: str
    role: str
    organization_id: uuid.UUID
    auth_provider: str
    is_active: bool
    totp_enabled: bool
    avatar_url: str | None
    last_login_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    """JWT token pair."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class MessageResponse(BaseModel):
    """Generic message response."""

    message: str
