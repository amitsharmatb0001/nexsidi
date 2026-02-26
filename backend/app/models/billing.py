"""Billing schema models: records, token usage, API keys.

Tables: billing.records, billing.token_usage, billing.api_keys
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, UUIDPrimaryKeyMixin


class BillingRecord(Base, UUIDPrimaryKeyMixin, TenantMixin):
    __tablename__ = "records"
    __table_args__ = (
        Index("ix_billing_records_organization_id", "organization_id"),
        {"schema": "billing"},
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("core.organizations.id", ondelete="CASCADE"), nullable=False
    )
    razorpay_order_id: Mapped[str | None] = mapped_column(String(255), default=None)
    razorpay_payment_id: Mapped[str | None] = mapped_column(String(255), default=None)
    amount_inr: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    plan: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    invoice_url: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()", nullable=False)


class TokenUsage(Base, UUIDPrimaryKeyMixin, TenantMixin):
    __tablename__ = "token_usage"
    __table_args__ = (
        Index("ix_billing_token_usage_user_id", "user_id"),
        Index("ix_billing_token_usage_recorded_at", "recorded_at"),
        Index("ix_billing_token_usage_user_recorded", "user_id", "recorded_at"),
        {"schema": "billing"},
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("auth.users.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("core.projects.id", ondelete="SET NULL"), default=None
    )
    model: Mapped[str] = mapped_column(String(50), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=0, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()", nullable=False)


class ApiKey(Base, UUIDPrimaryKeyMixin, TenantMixin):
    __tablename__ = "api_keys"
    __table_args__ = (
        Index("ix_billing_api_keys_key_hash", "key_hash", unique=True),
        Index("ix_billing_api_keys_user_id", "user_id"),
        {"schema": "billing"},
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("auth.users.id", ondelete="CASCADE"), nullable=False
    )
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(12), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    scope: Mapped[dict] = mapped_column(JSONB, default=lambda: ["all"], nullable=False)
    rate_limit: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()", nullable=False)
