"""Audit logging service.

Writes to audit.logs table. Every state-changing operation must call log_action().
The audit.logs table is append-only — the app DB user has INSERT permission only.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


async def log_action(
    session: AsyncSession,
    *,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    organization_id: uuid.UUID | None = None,
    before_state: dict[str, Any] | None = None,
    after_state: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> AuditLog:
    """Insert an audit log entry.

    Args:
        session: Active database session (within a transaction).
        action: Action string in "entity.verb" format (e.g., "user.register", "project.create").
        entity_type: Type of entity (user, project, deployment, etc.).
        entity_id: UUID of the affected entity.
        user_id: UUID of the user performing the action (None for system actions).
        organization_id: UUID of the organization.
        before_state: Entity state before the change (None for creates).
        after_state: Entity state after the change (None for deletes).
        metadata: Additional context (reason, source, agent_name, etc.).
        ip_address: Client IP address.
        user_agent: Client user agent string.

    Returns:
        The created AuditLog instance.
    """
    entry = AuditLog(
        user_id=user_id,
        organization_id=organization_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_state=before_state,
        after_state=after_state,
        metadata_=metadata,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    session.add(entry)
    return entry
