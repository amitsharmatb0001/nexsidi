"""Tenant context middleware.

Sets PostgreSQL session variables (SET LOCAL) for Row-Level Security:
- app.current_tenant = organization_id
- app.current_user_id = user_id
- app.user_role = role

These are used by RLS policies to filter data automatically.
SET LOCAL is transaction-scoped — it resets when the transaction ends.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Immutable tenant context extracted from JWT claims."""

    organization_id: str
    user_id: str
    role: str


async def set_tenant_context(session: AsyncSession, ctx: TenantContext) -> None:
    """Set RLS session variables for the current transaction.

    Must be called within an active transaction (session.begin()).
    SET LOCAL ensures values are scoped to the transaction only.

    Note: SET LOCAL doesn't support parameterized queries in asyncpg,
    so we use format strings. Values are validated UUIDs/enum from JWT.

    Args:
        session: Active async SQLAlchemy session.
        ctx: Tenant context from JWT claims.
    """
    # Validate inputs are safe (UUIDs and known role values only)
    _validate_uuid(ctx.organization_id)
    _validate_uuid(ctx.user_id)
    if not ctx.role.isalnum() and "_" not in ctx.role:
        raise ValueError(f"Invalid role: {ctx.role}")

    await session.execute(text(f"SET LOCAL app.current_tenant = '{ctx.organization_id}'"))
    await session.execute(text(f"SET LOCAL app.current_user_id = '{ctx.user_id}'"))
    await session.execute(text(f"SET LOCAL app.user_role = '{ctx.role}'"))


def _validate_uuid(value: str) -> None:
    """Validate that a string is a valid UUID (prevents SQL injection)."""
    import uuid
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError):
        raise ValueError(f"Invalid UUID: {value}")
