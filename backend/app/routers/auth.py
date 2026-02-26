"""Auth API routes: register, login, token refresh, current user.

All routes are under /api/v1/auth/.
Registration creates both an organization and user (first user = org_admin).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.config import get_settings
from app.database import get_session_factory
from app.dependencies import CurrentUser
from app.models.auth import User
from app.models.core import Organization
from app.schemas.auth import (
    TokenRefresh,
    TokenResponse,
    UserLogin,
    UserRegister,
    UserResponse,
)
from app.services.audit import log_action
from app.services.auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)

logger = structlog.get_logger()

router = APIRouter()


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(body: UserRegister) -> TokenResponse:
    """Register a new user and organization.

    The first user of an organization becomes org_admin.
    Creates: organization, user, and returns JWT tokens.
    """
    settings = get_settings()
    factory = get_session_factory()

    async with factory() as session:
        async with session.begin():
            existing = await session.execute(
                select(User).where(User.email == body.email)
            )
            if existing.scalar_one_or_none() is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Email already registered",
                )

            # Generate unique slug (append counter if collision)
            base_slug = body.organization_name.lower().replace(" ", "-")[:100]
            slug = base_slug
            slug_check = await session.execute(
                select(func.count()).select_from(Organization).where(Organization.slug.like(f"{base_slug}%"))
            )
            count = slug_check.scalar_one()
            if count > 0:
                slug = f"{base_slug}-{count}"

            org = Organization(
                name=body.organization_name,
                slug=slug,
                plan="free",
            )
            session.add(org)
            await session.flush()

            user = User(
                organization_id=org.id,
                email=body.email,
                password_hash=hash_password(body.password),
                name=body.name,
                role="org_admin",
                auth_provider="email",
                is_active=True,
                last_login_at=datetime.now(timezone.utc),
            )
            session.add(user)
            await session.flush()

            await log_action(
                session=session,
                action="user.register",
                entity_type="user",
                entity_id=user.id,
                user_id=user.id,
                organization_id=org.id,
                after_state={"email": body.email, "role": "org_admin"},
            )

    logger.info("user_registered", email=body.email, org_id=str(org.id))

    return TokenResponse(
        access_token=create_access_token(user.id, org.id, user.role),
        refresh_token=create_refresh_token(user.id, org.id),
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: UserLogin) -> TokenResponse:
    """Authenticate with email + password, return JWT tokens."""
    settings = get_settings()
    factory = get_session_factory()

    async with factory() as session:
        async with session.begin():
            result = await session.execute(
                select(User).where(User.email == body.email)
            )
            user = result.scalar_one_or_none()

            if user is None or user.password_hash is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid email or password",
                )

            if not verify_password(body.password, user.password_hash):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid email or password",
                )

            if not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Account deactivated",
                )

            user.last_login_at = datetime.now(timezone.utc)

            await log_action(
                session=session,
                action="user.login",
                entity_type="user",
                entity_id=user.id,
                user_id=user.id,
                organization_id=user.organization_id,
            )

    logger.info("user_logged_in", email=body.email)

    return TokenResponse(
        access_token=create_access_token(user.id, user.organization_id, user.role),
        refresh_token=create_refresh_token(user.id, user.organization_id),
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(body: TokenRefresh) -> TokenResponse:
    """Exchange a refresh token for a new access + refresh token pair."""
    settings = get_settings()

    try:
        payload = decode_token(body.refresh_token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    user_id = uuid.UUID(payload["sub"])
    org_id = uuid.UUID(payload["org"])

    factory = get_session_factory()
    async with factory() as session:
        async with session.begin():
            result = await session.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()

            if user is None or not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="User not found or deactivated",
                )

    return TokenResponse(
        access_token=create_access_token(user_id, org_id, user.role),
        refresh_token=create_refresh_token(user_id, org_id),
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.get("/me", response_model=UserResponse)
async def get_me(user: CurrentUser) -> UserResponse:
    """Get current authenticated user's profile."""
    return UserResponse.model_validate(user)
