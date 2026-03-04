"""User management routes.

Routes (all under /api/v1/users/):
    PATCH  /me                  — authenticated user updates own profile
    GET    /                    — admin: list users in the organisation (paginated)
    PATCH  /{user_id}           — admin: change role or activation status
    POST   /invite              — admin: invite a new user via email

Role constants (must match auth.users.role CHECK constraint):
    member, developer, org_admin

Admins cannot demote the last org_admin to prevent lock-out.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import func, select

from app.config import get_settings
from app.database import get_session_factory
from app.dependencies import AdminContext, CurrentContext, TenantSession
from app.middleware.tenant import TenantContext as _TC, set_tenant_context
from app.models.auth import EmailVerificationToken, User
from app.schemas.auth import UserResponse
from app.services.audit import log_action
from app.services.email import send_invite_email

logger = structlog.get_logger()

router = APIRouter()

# ── Valid roles that can be assigned via the API ──────────────────────────────
_ASSIGNABLE_ROLES = frozenset({"member", "developer", "org_admin"})

# ── Schemas ───────────────────────────────────────────────────────────────────


class UpdateMeRequest(BaseModel):
    """Self-service profile fields a user can change."""
    full_name: str | None = Field(None, min_length=1, max_length=255)
    avatar_url: str | None = Field(None, max_length=2048)


class UpdateUserRequest(BaseModel):
    """Admin: fields an admin can change on another user."""
    role: str | None = Field(None)
    is_active: bool | None = None

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str | None) -> str | None:
        if v is not None and v not in _ASSIGNABLE_ROLES:
            raise ValueError(f"role must be one of: {', '.join(sorted(_ASSIGNABLE_ROLES))}")
        return v


class InviteRequest(BaseModel):
    """Admin: invite a new member by email."""
    email: EmailStr
    role: str = Field(default="member")

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in _ASSIGNABLE_ROLES:
            raise ValueError(f"role must be one of: {', '.join(sorted(_ASSIGNABLE_ROLES))}")
        return v


class UserListResponse(BaseModel):
    """Paginated list of users."""
    users: list[UserResponse]
    total: int
    limit: int
    offset: int


class InviteResponse(BaseModel):
    """Invitation created response."""
    message: str
    email: str
    role: str
    invite_url: str | None = None  # None in prod (email sent), present in dev mode


# ── Frontend base URL helper ───────────────────────────────────────────────────

def _frontend_base() -> str:
    settings = get_settings()
    if settings.cors_origins:
        return settings.cors_origins[0].rstrip("/")
    return "https://app.nexsidi.com"


# ── Self-service ───────────────────────────────────────────────────────────────

@router.patch("/me", response_model=UserResponse)
async def update_me(
    body: UpdateMeRequest,
    ctx: CurrentContext,
    session: TenantSession,
) -> UserResponse:
    """Update the authenticated user's own profile (name and/or avatar URL).

    Only the fields provided in the request body are updated — omitted fields
    are left unchanged. Returns the full updated UserResponse.
    """
    result = await session.execute(
        select(User).where(User.id == uuid.UUID(ctx.user_id))
    )
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or deactivated",
        )

    before: dict = {}
    after: dict = {}

    if body.full_name is not None and body.full_name != user.name:
        before["name"] = user.name
        user.name = body.full_name
        after["name"] = user.name

    if body.avatar_url is not None and body.avatar_url != user.avatar_url:
        before["avatar_url"] = user.avatar_url
        user.avatar_url = body.avatar_url
        after["avatar_url"] = user.avatar_url

    if after:
        await session.flush()
        await log_action(
            session=session,
            action="user.profile_updated",
            entity_type="user",
            entity_id=user.id,
            user_id=user.id,
            organization_id=user.organization_id,
            before_state=before,
            after_state=after,
        )

    logger.info("user_profile_updated", user_id=ctx.user_id)
    return UserResponse.model_validate(user)


# ── Admin endpoints ────────────────────────────────────────────────────────────

@router.get("/", response_model=UserListResponse)
async def list_users(
    ctx: AdminContext,
    session: TenantSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> UserListResponse:
    """List all users in the admin's organisation (paginated).

    Requires org_admin or super_admin role. Returns users sorted by
    creation date (newest first).
    """
    org_id = uuid.UUID(ctx.organization_id)

    total_result = await session.execute(
        select(func.count()).select_from(User).where(User.organization_id == org_id)
    )
    total = total_result.scalar_one()

    users_result = await session.execute(
        select(User)
        .where(User.organization_id == org_id)
        .order_by(User.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    users = users_result.scalars().all()

    return UserListResponse(
        users=[UserResponse.model_validate(u) for u in users],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: uuid.UUID,
    body: UpdateUserRequest,
    ctx: AdminContext,
    session: TenantSession,
) -> UserResponse:
    """Admin: change a user's role or activation status.

    Safety checks:
    - Cannot modify users from other organisations.
    - Cannot demote the last org_admin (lock-out prevention).
    - Admins cannot modify super_admin accounts.
    """
    org_id = uuid.UUID(ctx.organization_id)

    result = await session.execute(
        select(User).where(User.id == user_id, User.organization_id == org_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Prevent modification of super_admin accounts by org admins
    if user.role == "super_admin" and ctx.role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot modify a super_admin account",
        )

    # Prevent self-demotion (admin demoting themselves is always bad UX)
    if str(user.id) == ctx.user_id and body.role is not None and body.role != user.role:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot change your own role",
        )

    before: dict = {}
    after: dict = {}

    # Role change: guard against removing last org_admin
    if body.role is not None and body.role != user.role:
        if user.role == "org_admin":
            admin_count_result = await session.execute(
                select(func.count()).select_from(User).where(
                    User.organization_id == org_id,
                    User.role == "org_admin",
                    User.is_active.is_(True),
                )
            )
            admin_count = admin_count_result.scalar_one()
            if admin_count <= 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot demote the last active org_admin — promote another user first",
                )
        before["role"] = user.role
        user.role = body.role
        after["role"] = user.role

    if body.is_active is not None and body.is_active != user.is_active:
        # Guard: cannot deactivate last admin
        if user.role == "org_admin" and not body.is_active:
            admin_count_result = await session.execute(
                select(func.count()).select_from(User).where(
                    User.organization_id == org_id,
                    User.role == "org_admin",
                    User.is_active.is_(True),
                )
            )
            admin_count = admin_count_result.scalar_one()
            if admin_count <= 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot deactivate the last active org_admin",
                )
        before["is_active"] = user.is_active
        user.is_active = body.is_active
        after["is_active"] = user.is_active

    if after:
        await session.flush()
        await log_action(
            session=session,
            action="user.admin_updated",
            entity_type="user",
            entity_id=user.id,
            user_id=uuid.UUID(ctx.user_id),
            organization_id=org_id,
            before_state=before,
            after_state=after,
        )

    logger.info(
        "admin_user_updated",
        target_user_id=str(user_id),
        admin_user_id=ctx.user_id,
    )
    return UserResponse.model_validate(user)


@router.post("/invite", response_model=InviteResponse)
async def invite_user(
    body: InviteRequest,
    ctx: AdminContext,
    session: TenantSession,
) -> InviteResponse:
    """Admin: invite a new user to the organisation by email.

    Flow:
    1. Check the email is not already a member of this org.
    2. Generate a signed invite token stored as a SHA-256 hash in
       email_verification_tokens (reused table — invite is just a special
       verification token with role metadata encoded in the URL).
    3. Send an invite email (or log URL in dev mode).
    4. Return 200 with invite details.

    The frontend registration page reads the `invite` query parameter and
    pre-fills the email + role. On successful registration, the token is
    redeemed and email_verified is set to True immediately.
    """
    org_id = uuid.UUID(ctx.organization_id)
    email_lower = body.email.lower()

    # Check if email already belongs to a user in this org
    existing = await session.execute(
        select(User).where(
            User.email == email_lower,
            User.organization_id == org_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already belongs to this organisation",
        )

    # Fetch org name for the email
    from app.models.core import Organization
    org_result = await session.execute(
        select(Organization).where(Organization.id == org_id)
    )
    org = org_result.scalar_one_or_none()
    org_name = org.name if org else "NexSidi"

    # Generate invite token (7-day expiry)
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)

    # Store as EmailVerificationToken; user_id is the inviting admin's id
    # (the invited user doesn't exist yet — token links via email in the URL)
    invite_token_row = EmailVerificationToken(
        user_id=uuid.UUID(ctx.user_id),  # admin who sent the invite
        token_hash=token_hash,
        expires_at=expires_at,
        used=False,
    )
    session.add(invite_token_row)
    await session.flush()

    await log_action(
        session=session,
        action="user.invite_sent",
        entity_type="user",
        entity_id=uuid.UUID(ctx.user_id),
        user_id=uuid.UUID(ctx.user_id),
        organization_id=org_id,
        after_state={"invited_email_hash": hashlib.sha256(email_lower.encode()).hexdigest(), "role": body.role},
    )

    # Build invite URL — frontend registration page handles pre-fill
    base = _frontend_base()
    invite_url = (
        f"{base}/auth/register"
        f"?invite={raw_token}"
        f"&email={email_lower}"
        f"&role={body.role}"
        f"&org={str(org_id)}"
    )

    settings = get_settings()
    sent = await send_invite_email(
        to_email=email_lower,
        invite_url=invite_url,
        org_name=org_name,
        role=body.role,
    )

    logger.info(
        "user_invited",
        org_id=str(org_id),
        invited_by=ctx.user_id,
        role=body.role,
    )

    return InviteResponse(
        message="Invitation sent",
        email=email_lower,
        role=body.role,
        # In dev mode (email not sent), expose the URL so devs can test
        invite_url=invite_url if not sent else None,
    )
