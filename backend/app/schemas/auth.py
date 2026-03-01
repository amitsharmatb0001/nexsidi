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
    # R8-FIX: max_length prevents 10MB+ password strings from consuming
    # memory. While bcrypt truncates to 72 bytes, the full string is still
    # parsed by Pydantic and held in memory — a lightweight DoS vector.
    password: str = Field(..., max_length=1024)


class TokenRefresh(BaseModel):
    """Refresh token request."""

    # R9-FIX: max_length prevents multi-MB payloads from being parsed
    # into memory. Valid JWTs are typically ~500 bytes.
    refresh_token: str = Field(..., max_length=4096)


class LogoutRequest(BaseModel):
    """Logout request. Optionally includes refresh token to revoke both tokens.

    R16-FIX: Without the refresh token, /logout only revokes the access token.
    An attacker with the refresh token can still mint new access tokens.
    """

    refresh_token: str | None = Field(None, max_length=4096)


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
