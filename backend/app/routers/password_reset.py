"""Password reset and email verification routes.

Routes (all under /api/v1/auth/):
    POST /forgot-password       — request a password reset link
    POST /reset-password        — redeem token and set new password
    POST /verify-email          — mark email as verified
    POST /resend-verification   — resend verification email (authenticated)

Security notes:
- Raw tokens are NEVER stored; only SHA-256 hashes are persisted.
- forgot-password always returns 200 to prevent email enumeration.
- Tokens are single-use (used=True) and time-limited.
- bcrypt runs in a thread pool to avoid blocking the event loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select, text as _text

from app.config import get_settings
from app.database import get_session_factory
from app.dependencies import CurrentContext
from app.middleware.tenant import TenantContext as _TC, set_tenant_context
from app.models.auth import EmailVerificationToken, PasswordResetToken, User
from app.services.audit import log_action
from app.services.auth import hash_password
from app.services.email import send_password_reset_email, send_verification_email

logger = structlog.get_logger()

router = APIRouter()

# ── Token helpers ─────────────────────────────────────────────────────────────

_RESET_TOKEN_EXPIRY_HOURS = 1
_VERIFY_TOKEN_EXPIRY_HOURS = 24

# Frontend base URL for generating reset/verify links.
# Falls back to a placeholder if CORS_ORIGINS is not set.
def _frontend_base() -> str:
    settings = get_settings()
    if settings.cors_origins:
        return settings.cors_origins[0].rstrip("/")
    return "https://app.nexsidi.com"


def _make_token() -> tuple[str, str]:
    """Generate a secure random token and its SHA-256 hash.

    Returns:
        (raw_token, token_hash) — raw goes to user, hash goes to DB.
    """
    raw = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return raw, digest


# ── Schemas ───────────────────────────────────────────────────────────────────

class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=512)
    new_password: str = Field(..., min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        has_upper = any(c.isupper() for c in v)
        has_lower = any(c.islower() for c in v)
        has_digit = any(c.isdigit() for c in v)
        if not (has_upper and has_lower and has_digit):
            raise ValueError("Password must contain uppercase, lowercase, and a digit")
        return v


class VerifyEmailRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=512)


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/forgot-password", status_code=status.HTTP_200_OK)
async def forgot_password(body: ForgotPasswordRequest) -> dict:
    """Request a password reset link.

    Always returns 200 — never reveals whether the email exists.
    Sends a one-time reset link valid for 1 hour via ZeptoMail SMTP.
    In dev mode (no SMTP configured), logs the reset URL at WARNING level.
    """
    settings = get_settings()
    factory = get_session_factory()

    # Normalise email so lookups are case-insensitive
    email_lower = body.email.lower()
    raw_token, token_hash = _make_token()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=_RESET_TOKEN_EXPIRY_HOURS)

    try:
        async with factory() as session:
            async with session.begin():
                # auth_mode bypasses RLS for pre-auth SELECT
                await session.execute(_text("SET LOCAL app.auth_mode = 'true'"))

                result = await session.execute(
                    select(User).where(User.email == email_lower)
                )
                user = result.scalar_one_or_none()

                if user is None or not user.is_active:
                    # Return 200 regardless — don't leak account existence
                    logger.info("password_reset_email_not_found")
                    return {"message": "If an account with that email exists, a reset link has been sent."}

                # Invalidate any previous unused tokens for this user
                # (harmless if none exist — just a best-effort cleanup)
                from sqlalchemy import update as _update
                await session.execute(
                    _update(PasswordResetToken)
                    .where(
                        PasswordResetToken.user_id == user.id,
                        PasswordResetToken.used.is_(False),
                    )
                    .values(used=True)
                )

                reset_token = PasswordResetToken(
                    user_id=user.id,
                    token_hash=token_hash,
                    expires_at=expires_at,
                    used=False,
                )
                session.add(reset_token)
                await session.flush()

                _user_id = user.id
                _org_id = user.organization_id
                _role = user.role
    except Exception as exc:
        logger.error("password_reset_db_error", error=str(exc)[:200])
        # Still return 200 to prevent timing oracle
        return {"message": "If an account with that email exists, a reset link has been sent."}

    # Build reset URL and send email (outside DB transaction)
    reset_url = f"{_frontend_base()}/auth/reset-password?token={raw_token}"
    await send_password_reset_email(to_email=email_lower, reset_url=reset_url)

    # Audit log in a separate session so email failure doesn't roll it back
    try:
        async with factory() as session:
            async with session.begin():
                await set_tenant_context(session, _TC(
                    organization_id=str(_org_id),
                    user_id=str(_user_id),
                    role=_role,
                ))
                await log_action(
                    session=session,
                    action="user.password_reset_requested",
                    entity_type="user",
                    entity_id=_user_id,
                    user_id=_user_id,
                    organization_id=_org_id,
                )
    except Exception as audit_exc:
        logger.error("password_reset_audit_failed", error=str(audit_exc)[:200])

    return {"message": "If an account with that email exists, a reset link has been sent."}


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(body: ResetPasswordRequest) -> Response:
    """Redeem a password reset token and set a new password.

    - Hashes the supplied token and looks it up in the DB.
    - Validates token is not expired and not already used.
    - Updates password_hash and marks token as used.
    - Returns 204 on success; 400 for invalid/expired tokens.
    """
    token_hash = hashlib.sha256(body.token.encode()).hexdigest()
    factory = get_session_factory()

    async with factory() as session:
        async with session.begin():
            await session.execute(_text("SET LOCAL app.auth_mode = 'true'"))

            result = await session.execute(
                select(PasswordResetToken).where(
                    PasswordResetToken.token_hash == token_hash
                )
            )
            reset_token = result.scalar_one_or_none()

            if reset_token is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid or expired reset token",
                )

            if reset_token.used:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="This reset link has already been used",
                )

            now = datetime.now(timezone.utc)
            # expires_at may be naive if stored without timezone — normalise
            expires_at = reset_token.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if now > expires_at:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Reset link has expired — please request a new one",
                )

            # Fetch the user
            user_result = await session.execute(
                select(User).where(User.id == reset_token.user_id)
            )
            user = user_result.scalar_one_or_none()
            if user is None or not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid or expired reset token",
                )

            # Update password (bcrypt in thread pool to avoid blocking event loop)
            user.password_hash = await asyncio.to_thread(hash_password, body.new_password)

            # Mark token as used (single-use enforcement)
            reset_token.used = True

            await session.flush()

            # Set RLS context for audit log
            await set_tenant_context(session, _TC(
                organization_id=str(user.organization_id),
                user_id=str(user.id),
                role=user.role,
            ))

            await log_action(
                session=session,
                action="user.password_reset",
                entity_type="user",
                entity_id=user.id,
                user_id=user.id,
                organization_id=user.organization_id,
            )

            _user_id = user.id

    logger.info("password_reset_complete", user_id=str(_user_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/verify-email", status_code=status.HTTP_204_NO_CONTENT)
async def verify_email(body: VerifyEmailRequest) -> Response:
    """Verify a user's email address using a token from the verification email.

    - Hashes the supplied token, looks it up in DB.
    - Sets user.email_verified = True.
    - Marks token as used.
    - Returns 204 on success; 400 for invalid/expired tokens.
    """
    token_hash = hashlib.sha256(body.token.encode()).hexdigest()
    factory = get_session_factory()

    async with factory() as session:
        async with session.begin():
            await session.execute(_text("SET LOCAL app.auth_mode = 'true'"))

            result = await session.execute(
                select(EmailVerificationToken).where(
                    EmailVerificationToken.token_hash == token_hash
                )
            )
            verify_token = result.scalar_one_or_none()

            if verify_token is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid verification token",
                )

            if verify_token.used:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="This verification link has already been used",
                )

            now = datetime.now(timezone.utc)
            expires_at = verify_token.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if now > expires_at:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Verification link has expired — please request a new one",
                )

            user_result = await session.execute(
                select(User).where(User.id == verify_token.user_id)
            )
            user = user_result.scalar_one_or_none()
            if user is None or not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid verification token",
                )

            user.email_verified = True
            verify_token.used = True
            await session.flush()

            await set_tenant_context(session, _TC(
                organization_id=str(user.organization_id),
                user_id=str(user.id),
                role=user.role,
            ))

            await log_action(
                session=session,
                action="user.email_verified",
                entity_type="user",
                entity_id=user.id,
                user_id=user.id,
                organization_id=user.organization_id,
            )

            _user_id = user.id

    logger.info("email_verified", user_id=str(_user_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/resend-verification", status_code=status.HTTP_204_NO_CONTENT)
async def resend_verification(ctx: CurrentContext) -> Response:
    """Resend email verification link to the authenticated user.

    Requires authentication (but not a verified email). Generates a new
    verification token and sends it via email (or logs in dev mode).
    Returns 204 whether or not the email was actually sent to prevent
    information leakage.
    """
    factory = get_session_factory()

    async with factory() as session:
        async with session.begin():
            await set_tenant_context(session, _TC(
                organization_id=ctx.organization_id,
                user_id=ctx.user_id,
                role=ctx.role,
            ))

            result = await session.execute(
                select(User).where(User.id == uuid.UUID(ctx.user_id))
            )
            user = result.scalar_one_or_none()

            if user is None or not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="User not found or deactivated",
                )

            if user.email_verified:
                # Already verified — return 204 silently (no need to expose state)
                return Response(status_code=status.HTTP_204_NO_CONTENT)

            # Invalidate any previous unused verification tokens
            from sqlalchemy import update as _update
            await session.execute(
                _update(EmailVerificationToken)
                .where(
                    EmailVerificationToken.user_id == user.id,
                    EmailVerificationToken.used.is_(False),
                )
                .values(used=True)
            )

            raw_token, token_hash = _make_token()
            expires_at = datetime.now(timezone.utc) + timedelta(hours=_VERIFY_TOKEN_EXPIRY_HOURS)

            verify_token_row = EmailVerificationToken(
                user_id=user.id,
                token_hash=token_hash,
                expires_at=expires_at,
                used=False,
            )
            session.add(verify_token_row)
            await session.flush()

            _email = user.email
            _org_id = user.organization_id
            _user_id = user.id
            _role = user.role

    # Send email outside the transaction
    verify_url = f"{_frontend_base()}/auth/verify-email?token={raw_token}"
    await send_verification_email(to_email=_email, verify_url=verify_url)

    try:
        async with factory() as session:
            async with session.begin():
                await set_tenant_context(session, _TC(
                    organization_id=str(_org_id),
                    user_id=str(_user_id),
                    role=_role,
                ))
                await log_action(
                    session=session,
                    action="user.verification_resent",
                    entity_type="user",
                    entity_id=_user_id,
                    user_id=_user_id,
                    organization_id=_org_id,
                )
    except Exception as audit_exc:
        logger.error("resend_verification_audit_failed", error=str(audit_exc)[:200])

    logger.info("verification_resent", user_id=str(_user_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Utility: create and store a fresh email verification token ─────────────────
# Called from registration to issue initial verification token.

async def _create_verification_token_for_user(
    session,
    user_id: uuid.UUID,
    email: str,
) -> None:
    """Create an EmailVerificationToken row and dispatch the verification email.

    Intended to be called from the registration endpoint while still inside
    an active transaction (so the user row is flushed but not yet committed).
    The email is sent asynchronously outside the caller's transaction.

    NOTE: This function only SCHEDULES the email send via asyncio.create_task()
    so it does not block registration. The token row is added to the session.
    """
    raw_token, token_hash = _make_token()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=_VERIFY_TOKEN_EXPIRY_HOURS)

    token_row = EmailVerificationToken(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at,
        used=False,
    )
    session.add(token_row)

    verify_url = f"{_frontend_base()}/auth/verify-email?token={raw_token}"

    # Schedule email send as a fire-and-forget task — registration should not
    # fail if the email service is unavailable.
    asyncio.create_task(send_verification_email(to_email=email, verify_url=verify_url))
