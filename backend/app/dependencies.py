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
from app.services.auth import decode_token

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
        payload = decode_token(credentials.credentials)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    user_id = payload.get("sub")
    org_id = payload.get("org")
    role = payload.get("role")

    if not all([user_id, org_id, role]):
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
    """Dependency that requires org_admin or super_admin role."""
    if ctx.role not in ("org_admin", "super_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return ctx


# Type aliases for cleaner route signatures
CurrentContext = Annotated[TenantContext, Depends(get_current_user_context)]
TenantSession = Annotated[AsyncSession, Depends(get_tenant_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]
AdminContext = Annotated[TenantContext, Depends(require_admin)]
