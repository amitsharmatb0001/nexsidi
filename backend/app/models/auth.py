"""Auth schema models: users, whatsapp accounts, feature flags, reset tokens.

Tables: auth.users, auth.whatsapp_accounts, auth.feature_flags,
        auth.password_reset_tokens, auth.email_verification_tokens
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class User(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_organization_id", "organization_id"),
        Index("ix_users_email", "email", unique=True),
        Index("ix_users_phone", "phone", unique=True, postgresql_where="phone IS NOT NULL"),
        {"schema": "auth"},
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("core.organizations.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20), default=None)
    password_hash: Mapped[str | None] = mapped_column(String(255), default=None)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="member", nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(Text, default=None)
    auth_provider: Mapped[str] = mapped_column(String(20), default="email", nullable=False)
    google_id: Mapped[str | None] = mapped_column(String(255), unique=True, default=None)
    totp_secret_enc: Mapped[str | None] = mapped_column(Text, default=None)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    passkey_credentials: Mapped[dict | None] = mapped_column(JSONB, default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class WhatsAppAccount(Base, UUIDPrimaryKeyMixin, TenantMixin):
    """R26-FIX-5: Added TenantMixin for organization_id and RLS tenant isolation.

    Previously had no organization_id column, so no tenant-scoped RLS policy
    could be applied — any authenticated user from any tenant could potentially
    access another tenant's WhatsApp accounts.
    """

    __tablename__ = "whatsapp_accounts"
    __table_args__ = (
        Index("ix_whatsapp_accounts_organization_id", "organization_id"),
        # R-WA-FIX: Composite unique on (user_id, organization_id) instead of
        # user_id alone.  The old single-column unique prevented the same user
        # from linking a WhatsApp account in a second organization (legit use
        # case: consultant working across tenants).
        UniqueConstraint("user_id", "organization_id", name="uq_whatsapp_user_org"),
        {"schema": "auth"},
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("auth.users.id", ondelete="CASCADE"), nullable=False
    )
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pin_hash: Mapped[str | None] = mapped_column(String(255), default=None)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()", nullable=False)


class FeatureFlag(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "feature_flags"
    __table_args__ = {"schema": "auth"}

    flag_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("auth.users.id", ondelete="SET NULL"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()", nullable=False)


class PasswordResetToken(Base, UUIDPrimaryKeyMixin):
    """Single-use password reset tokens stored as SHA-256 hashes.

    The raw token is sent to the user's email; only the hash is stored.
    Tokens expire after 1 hour and are single-use (used=True after redemption).
    """

    __tablename__ = "password_reset_tokens"
    __table_args__ = (
        Index("ix_password_reset_tokens_token_hash", "token_hash", unique=True),
        {"schema": "auth"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("auth.users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()", nullable=False)


class EmailVerificationToken(Base, UUIDPrimaryKeyMixin):
    """Single-use email verification tokens stored as SHA-256 hashes.

    The raw token is sent to the user's email; only the hash is stored.
    Tokens expire after 24 hours and are single-use (used=True after redemption).
    """

    __tablename__ = "email_verification_tokens"
    __table_args__ = (
        Index("ix_email_verification_tokens_token_hash", "token_hash", unique=True),
        {"schema": "auth"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("auth.users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()", nullable=False)
