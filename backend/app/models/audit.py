"""Audit schema models: logs, security events, consent records.

Tables: audit.logs (IMMUTABLE, APPEND-ONLY), audit.security_events, audit.consent_records

audit.logs is the most critical table — it provides complete audit trail for
compliance (CERT-In, DPDP Act). The app database user has INSERT-only permission;
UPDATE and DELETE are revoked at the database level.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class AuditLog(Base, UUIDPrimaryKeyMixin):
    """Immutable audit trail. App user: INSERT only. No UPDATE, no DELETE.

    Partitioned by month on created_at (set up in migration, not here).
    Retention: security 1yr, activity 90d, compliance 3yr, financial 8yr.
    """

    __tablename__ = "logs"
    __table_args__ = (
        Index("ix_audit_logs_user_id", "user_id"),
        Index("ix_audit_logs_organization_id", "organization_id"),
        Index("ix_audit_logs_action", "action"),
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
        Index("ix_audit_logs_created_at", "created_at"),
        {"schema": "audit"},
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)
    before_state: Mapped[dict | None] = mapped_column(JSONB, default=None)
    after_state: Mapped[dict | None] = mapped_column(JSONB, default=None)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, default=None)
    ip_address: Mapped[str | None] = mapped_column(INET, default=None)
    user_agent: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()", nullable=False)


class SecurityEvent(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "security_events"
    __table_args__ = (
        Index("ix_audit_security_events_event_type", "event_type"),
        Index("ix_audit_security_events_severity", "severity"),
        Index(
            "ix_audit_security_events_unresolved",
            "resolved",
            postgresql_where="resolved = false",
        ),
        Index("ix_audit_security_events_created_at", "created_at"),
        {"schema": "audit"},
    )

    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    source_ip: Mapped[str | None] = mapped_column(INET, default=None)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)
    details: Mapped[dict | None] = mapped_column(JSONB, default=None)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()", nullable=False)


class ConsentRecord(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "consent_records"
    __table_args__ = (
        Index("ix_audit_consent_records_user_id", "user_id"),
        Index("ix_audit_consent_records_consent_type", "consent_type"),
        {"schema": "audit"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("auth.users.id", ondelete="CASCADE"), nullable=False
    )
    consent_type: Mapped[str] = mapped_column(String(50), nullable=False)
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(INET, default=None)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()", nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
