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


class TenantContextError(ValueError):
    """V5-FIX (HIGH-5): Raised when tenant context has invalid UUID values.

    This replaces the unhandled ValueError from ``uuid.UUID()`` which would
    crash the request as a 500 ISE, permanently locking out users with
    corrupted JWTs.  Callers should catch this and return 401/400.
    """


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

    Raises:
        TenantContextError: If org_id or user_id is not a valid UUID.
    """
    # Validate inputs are safe (UUIDs and known role values only)
    # A-16-FIX: Use normalized UUID strings to prevent any format-based injection
    # V5-FIX (HIGH-5): Catch ValueError from _validate_uuid so a corrupted
    # JWT org_id doesn't crash the request with an unhandled exception.
    # Previously, ValueError propagated as a 500 ISE — locking the user out.
    try:
        org_id = _validate_uuid(ctx.organization_id)
        user_id = _validate_uuid(ctx.user_id)
    except (ValueError, AttributeError) as exc:
        raise TenantContextError(
            f"Invalid tenant context — malformed UUID in JWT: {exc}"
        ) from exc
    _validate_role(ctx.role)

    await session.execute(text(f"SET LOCAL app.current_tenant = '{org_id}'"))
    await session.execute(text(f"SET LOCAL app.current_user_id = '{user_id}'"))
    await session.execute(text(f"SET LOCAL app.user_role = '{ctx.role}'"))


_ALLOWED_ROLES = frozenset({
    "org_admin", "admin", "developer", "viewer", "member", "billing",
    "super_admin",  # H8-FIX: required for require_admin dependency
    "api_key",      # API-key authenticated requests (see services/api_key_auth.py)
})


def _validate_role(value: str) -> None:
    """Validate role against an explicit allowlist (prevents SQL injection).

    A regex or isalnum check is fragile — allowlist is the only safe approach
    when the value is interpolated into SQL via f-string.
    """
    if value not in _ALLOWED_ROLES:
        raise ValueError(f"Invalid role: {value!r}. Allowed: {sorted(_ALLOWED_ROLES)}")


def _validate_uuid(value: str) -> str:
    """Validate and normalize a UUID string (prevents SQL injection).

    A-16-FIX: Returns the canonical lowercase string form of the UUID.
    This ensures the value interpolated into ``SET LOCAL`` SQL is always
    a clean, normalized UUID — even if the input had mixed case, extra
    whitespace, or braces (all valid UUID forms but dangerous in SQL).
    """
    import uuid
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        raise ValueError(f"Invalid UUID: {value}")
