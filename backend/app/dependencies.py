"""FastAPI dependencies: auth extraction, tenant-scoped sessions.

These are injected into route handlers via Depends().
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session_factory
from app.middleware.tenant import TenantContext, set_tenant_context
from app.models.auth import User
from app.services.auth import decode_token, verify_token

security_scheme = HTTPBearer()


async def get_current_user_context(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security_scheme)],
) -> TenantContext:
    """Extract and validate JWT, return TenantContext.

    Used by all authenticated endpoints. Validates:
    - Token is present and well-formed
    - Token is not expired
    - Token type is 'access' (not refresh)
    - All required claims are present
    """
    try:
        # S-20-FIX: Validate token type at decode layer (defense-in-depth)
        # PHASE-1: Use verify_token (async) which also checks Valkey revocation.
        payload = await verify_token(credentials.credentials, expected_type="access")
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception:
        # R28-FIX-14: Catch infrastructure errors (ConnectionError from Valkey,
        # RuntimeError from uninitialized store). Without this, a Valkey blip
        # returns a raw 500 with traceback instead of a clean 503.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service temporarily unavailable",
        )

    # R28-FIX-3: Use bracket access for mandatory claims (guaranteed by
    # decode_token's require list) and explicit None checks instead of
    # truthiness which masks falsy-value bugs (e.g., role="" or 0).
    user_id = payload["sub"]
    org_id = payload["org"]
    role = payload["role"]

    if user_id is None or org_id is None or role is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed token claims",
        )

    # R26-FIX-27: Validate UUID format at the gate. Non-UUID claims cause
    # ValueError/500 downstream (e.g., uuid.UUID(ctx.user_id) in logout).
    try:
        uuid.UUID(user_id)
        uuid.UUID(org_id)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed token claims",
        )

    return TenantContext(
        organization_id=org_id,
        user_id=user_id,
        role=role,
    )


async def get_tenant_session(
    ctx: Annotated[TenantContext, Depends(get_current_user_context)],
) -> AsyncGenerator[AsyncSession]:
    """Yield a database session with RLS context variables set.

    Every query in this session is automatically filtered by PostgreSQL RLS
    policies. The caller doesn't need to add WHERE organization_id = X.
    """
    factory = get_session_factory()
    async with factory() as session:
        async with session.begin():
            await set_tenant_context(session, ctx)
            yield session


async def get_current_user(
    ctx: Annotated[TenantContext, Depends(get_current_user_context)],
    session: Annotated[AsyncSession, Depends(get_tenant_session)],
) -> User:
    """Fetch the full User object from DB. Use when you need user details."""
    result = await session.execute(
        select(User).where(User.id == uuid.UUID(ctx.user_id))
    )
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or deactivated",
        )
    return user


async def require_admin(
    ctx: Annotated[TenantContext, Depends(get_current_user_context)],
) -> TenantContext:
    """Dependency that requires org_admin or super_admin role.

    R17-FIX: Re-checks the DB to ensure the role hasn't been revoked since
    the JWT was issued. Previously trusted the JWT role claim directly,
    allowing a demoted admin to retain access for up to 15 minutes (access
    token lifetime) or 7 days (via refresh token minting new access tokens
    with stale role claim — though /refresh reads role from DB for new tokens).

    R25-FIX-13: Uses an internal short-lived session for the DB role check.
    This session opens ONLY after the JWT fast-reject, so non-admin users
    never touch the DB. The session is closed immediately after the check.
    Note: We intentionally do NOT depend on get_tenant_session() here because
    FastAPI resolves ALL dependencies before calling the function body. If
    get_tenant_session() fails (DB down), the JWT fast-reject path would
    never execute, returning 500 instead of 403 for non-admin users.
    The brief overlap with the route's TenantSession is acceptable: the
    admin-check session is short-lived (one SELECT) and the overlap window
    is measured in milliseconds.
    """
    # Fast reject: if JWT doesn't even claim admin, don't bother with DB
    if ctx.role not in ("org_admin", "super_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    # Verify role is still valid in the DB (defense against stale JWT claims)
    factory = get_session_factory()
    async with factory() as session:
        async with session.begin():
            await set_tenant_context(session, ctx)
            result = await session.execute(
                select(User).where(User.id == uuid.UUID(ctx.user_id))
            )
            user = result.scalar_one_or_none()
            if user is None or not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="User not found or deactivated",
                )
            if user.role not in ("org_admin", "super_admin"):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin access required",
                )
            # Return context with the authoritative DB role
            return TenantContext(
                organization_id=ctx.organization_id,
                user_id=ctx.user_id,
                role=user.role,
            )


# Type aliases for cleaner route signatures
CurrentContext = Annotated[TenantContext, Depends(get_current_user_context)]
TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]
AdminContext = Annotated[TenantContext, Depends(require_admin)]
