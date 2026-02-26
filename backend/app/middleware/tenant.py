"""Tenant context middleware.

Sets PostgreSQL session variables (SET LOCAL) for Row-Level Security:
- app.current_tenant = organization_id
- app.current_user = user_id
- app.current_role = role

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

    Args:
        session: Active async SQLAlchemy session.
        ctx: Tenant context from JWT claims.
    """
    await session.execute(text("SET LOCAL app.current_tenant = :tid"), {"tid": ctx.organization_id})
    await session.execute(text("SET LOCAL app.current_user = :uid"), {"uid": ctx.user_id})
    await session.execute(text("SET LOCAL app.current_role = :role"), {"role": ctx.role})
