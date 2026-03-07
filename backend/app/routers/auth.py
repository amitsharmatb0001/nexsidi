"""Auth API routes: register, login, token refresh, logout, current user.

All routes are under /api/v1/auth/.
Registration creates both an organization and user (first user = org_admin).
"""

from __future__ import annotations

import asyncio
import secrets
import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, HTTPException, Request, Response, status
from jwt.exceptions import PyJWTError as JWTError
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.config import get_settings
from app.database import get_session_factory
from app.dependencies import CurrentContext, CurrentUser, TenantSession
from app.middleware.tenant import TenantContext as _TC, set_tenant_context
from app.models.auth import User
from app.models.core import Organization
from app.schemas.auth import (
    LogoutRequest,
    TokenRefresh,
    TokenResponse,
    UserLogin,
    UserRegister,
    UserResponse,
)
from app.services.audit import log_action
from app.services.rate_limiter import get_rate_limiter
from app.services.auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
    verify_token,
)

logger = structlog.get_logger()

router = APIRouter()

# R8-FIX: Pre-computed dummy hash for login timing oracle defense.
# When user is not found, we still run bcrypt to equalize response time,
# preventing attackers from learning whether an email exists by measuring
# response latency (non-existent user = fast 401, existing user = slow bcrypt).
_DUMMY_HASH = hash_password("__timing_oracle_dummy_pw__")


# ── Distributed rate limiting for auth endpoints ──────────────────────────────
# Previously used an in-memory _AuthRateLimiter that was bypassed in
# multi-worker / multi-replica deployments (each process had its own counter).
# Now uses ValKeyRateLimiter — a Valkey-backed sliding-window limiter with an
# atomic Lua script, so limits are enforced across ALL workers and replicas.
# See app/services/rate_limiter.py for implementation details.


def _client_ip(request: Request) -> str:
    """Extract client IP from request.

    REFIX: X-Forwarded-For is only trusted when running behind a reverse
    proxy (production).  In development / direct access, using XFF would let
    any client spoof their IP and bypass rate limiting.  We only trust XFF
    when the setting ``trust_proxy_headers`` is True.
    """
    settings = get_settings()
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # R37-FIX: Take RIGHTMOST IP, not leftmost. When nginx (the
            # reverse proxy) appends client IP, it's the last entry. The
            # leftmost is attacker-controlled via X-Forwarded-For header.
            return forwarded.split(",")[-1].strip()
    if request.client:
        return request.client.host
    # R11-FIX: Log warning when request.client is None — all such requests
    # share one rate-limit bucket ("unknown"), enabling DoS if a misconfigured
    # proxy sends many requests without client info.
    logger.warning("client_ip_missing", path=request.url.path)
    return "unknown"


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(body: UserRegister, request: Request) -> TokenResponse:
    """Register a new user and organization.

    The first user of an organization becomes org_admin.
    Creates: organization, user, and returns JWT tokens.
    """
    await get_rate_limiter().check("register_ip", _client_ip(request), max_attempts=5)
    settings = get_settings()
    factory = get_session_factory()

    # ENUM-FIX: Use generic error message to prevent email enumeration.
    # Previously returned 409 "Email already registered" which reveals
    # whether an email is in the system.  Now returns the same error
    # for both duplicate emails and IntegrityError race conditions.
    #
    # RACE-FIX: Wrap the INSERT in try/except IntegrityError to handle
    # the race between SELECT (email check) and INSERT (user creation).
    # Two concurrent registrations with the same email could both pass
    # the SELECT check, then one INSERT fails with a unique constraint
    # violation.
    from sqlalchemy.exc import IntegrityError as SAIntegrityError

    try:
        async with factory() as session:
            async with session.begin():
                # R36-FIX: Set auth_mode for pre-authentication RLS bypass.
                # When DB_USER=nexsidi_app, FORCE ROW LEVEL SECURITY blocks
                # all operations unless app.current_tenant is set. But register
                # runs BEFORE authentication — there's no tenant yet. The
                # app.auth_mode policy (migration 005) allows SELECT/INSERT on
                # auth.users and core.organizations when this flag is set.
                # SET LOCAL is transaction-scoped — cannot leak across requests.
                from sqlalchemy import text as _text
                await session.execute(_text("SET LOCAL app.auth_mode = 'true'"))

                existing = await session.execute(
                    select(User).where(User.email == body.email)
                )
                if existing.scalar_one_or_none() is not None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Unable to create account with this email",
                    )

                # Generate unique slug (append counter if collision)
                # M9-FIX: Sanitize slug (remove non-alphanum/dash), use exact match
                # to avoid LIKE injection, and use a retry loop for race conditions
                import re
                base_slug = re.sub(r"[^a-z0-9-]", "", body.organization_name.lower().replace(" ", "-"))[:100]
                if not base_slug:
                    base_slug = "org"
                slug = base_slug
                # Try the base slug first, then append incrementing numbers
                for suffix in range(0, 100):
                    candidate = slug if suffix == 0 else f"{base_slug}-{suffix}"
                    slug_check = await session.execute(
                        select(func.count()).select_from(Organization).where(Organization.slug == candidate)
                    )
                    if slug_check.scalar_one() == 0:
                        slug = candidate
                        break
                else:
                    # Extremely unlikely: 100 collisions
                    slug = f"{base_slug}-{uuid.uuid4().hex[:8]}"

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
                    # R22-FIX: Run bcrypt in thread pool to avoid blocking the
                    # event loop for ~300ms. 10 concurrent registrations would
                    # stall ALL other requests for 3+ seconds without this.
                    password_hash=await asyncio.to_thread(hash_password, body.password),
                    name=body.name,
                    role="org_admin",
                    auth_provider="email",
                    is_active=True,
                    last_login_at=datetime.now(timezone.utc),
                )
                session.add(user)
                await session.flush()

                # DEFERRED-FIX-18: Set RLS context before writing audit log.
                # The session connects as nexsidi_app with FORCE ROW LEVEL
                # SECURITY. Without set_tenant_context(), the INSERT into
                # audit.logs has no app.current_tenant set, which can cause
                # the RLS policy to silently reject the row on strict policies.
                from app.middleware.tenant import TenantContext as _TC, set_tenant_context
                await set_tenant_context(session, _TC(
                    organization_id=str(org.id),
                    user_id=str(user.id),
                    role="org_admin",
                ))

                await log_action(
                    session=session,
                    action="user.register",
                    entity_type="user",
                    entity_id=user.id,
                    user_id=user.id,
                    organization_id=org.id,
                    # R8-FIX: Don't store PII (email) in audit log.
                    # Audit logs may be exported, queried by admins, or sent to
                    # external logging systems — storing emails violates GDPR.
                    after_state={"role": "org_admin"},
                    # DEFERRED-FIX-16: Pass ip_address and user_agent for
                    # forensic audit trail (who registered from where).
                    ip_address=_client_ip(request),
                    user_agent=request.headers.get("user-agent", "")[:256],
                )

                # R10-FIX: Capture ORM values inside session scope.
                # If expire_on_commit changes to True (SQLAlchemy default),
                # accessing these after session close raises DetachedInstanceError.
                _user_id = user.id
                _org_id = org.id
                _role = user.role
    except SAIntegrityError:
        # RACE-FIX: Concurrent registration with same email or slug
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Unable to create account with this email",
        )

    # LOW-FIX: Don't log PII (email) — log anonymized identifiers only
    logger.info("user_registered", user_id=str(_user_id), org_id=str(_org_id))

    return TokenResponse(
        access_token=create_access_token(_user_id, _org_id, _role),
        refresh_token=create_refresh_token(_user_id, _org_id),
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: UserLogin, request: Request) -> TokenResponse:
    """Authenticate with email + password, return JWT tokens."""
    await get_rate_limiter().check("login_email", body.email.lower(), max_attempts=10)       # per-email (primary)
    await get_rate_limiter().check("login_ip", _client_ip(request), max_attempts=30)        # per-IP (secondary)
    settings = get_settings()
    factory = get_session_factory()

    async with factory() as session:
        async with session.begin():
            # R36-FIX: Set auth_mode for pre-authentication RLS bypass.
            # Same as register — login needs to SELECT auth.users by email
            # before any tenant context exists. SET LOCAL is transaction-scoped.
            from sqlalchemy import text as _text
            await session.execute(_text("SET LOCAL app.auth_mode = 'true'"))

            result = await session.execute(
                select(User).where(User.email == body.email)
            )
            user = result.scalar_one_or_none()

            # R8-FIX: Timing oracle defense. When user doesn't exist,
            # we still run verify_password against a dummy hash so the
            # response time is ~identical to the valid-user path. This
            # prevents attackers from learning email existence via timing.
            if user is None or user.password_hash is None:
                # R22-FIX: Run bcrypt in thread pool (same as register path).
                await asyncio.to_thread(verify_password, body.password, _DUMMY_HASH)
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid email or password",
                )

            # R22-FIX: Run bcrypt in thread pool to avoid blocking event loop.
            if not await asyncio.to_thread(verify_password, body.password, user.password_hash):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid email or password",
                )

            # R10-FIX: Use the same 401 + generic message as invalid credentials.
            # Previously, deactivated accounts got a distinct 403 "Account deactivated"
            # which let attackers who know the password discover that an account
            # exists and was deactivated — a form of account enumeration.
            if not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid email or password",
                )

            # F-2-FIX: TOTP gate — enforce 2FA at login.
            # Previously totp_enabled was NEVER checked here: any user who enrolled
            # 2FA still received tokens on password-alone — a complete 2FA bypass.
            if user.totp_enabled and user.totp_secret_enc:
                if not body.totp_code:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="totp_required",
                        headers={"X-TOTP-Required": "true"},
                    )
                try:
                    import pyotp
                    from app.services.encryption import decrypt_totp_secret
                    _totp_secret = decrypt_totp_secret(user.totp_secret_enc)
                    if not pyotp.TOTP(_totp_secret).verify(body.totp_code, valid_window=1):
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid TOTP code",
                        )
                except HTTPException:
                    raise
                except Exception:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="TOTP verification unavailable",
                    )

            user.last_login_at = datetime.now(timezone.utc)

            # DEFERRED-FIX-18: Set RLS context before writing audit log.
            from app.middleware.tenant import TenantContext as _TC, set_tenant_context
            await set_tenant_context(session, _TC(
                organization_id=str(user.organization_id),
                user_id=str(user.id),
                role=user.role,
            ))

            await log_action(
                session=session,
                action="user.login",
                entity_type="user",
                entity_id=user.id,
                user_id=user.id,
                organization_id=user.organization_id,
                # DEFERRED-FIX-16: Pass ip_address and user_agent for
                # forensic audit trail (who logged in from where).
                ip_address=_client_ip(request),
                user_agent=request.headers.get("user-agent", "")[:256],
            )

            # R10-FIX: Capture ORM values inside session scope
            _user_id = user.id
            _org_id = user.organization_id
            _role = user.role

    logger.info("user_logged_in", user_id=str(_user_id))

    return TokenResponse(
        access_token=create_access_token(_user_id, _org_id, _role),
        refresh_token=create_refresh_token(_user_id, _org_id),
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(body: TokenRefresh, request: Request) -> TokenResponse:
    """Exchange a refresh token for a new access + refresh token pair."""
    # R-05-FIX: Rate limit refresh endpoint to prevent token-cycling attacks.
    # An attacker with a leaked refresh token could mint unlimited access tokens
    # without this limit.
    await get_rate_limiter().check("refresh_ip", _client_ip(request), max_attempts=30, window_seconds=900)
    settings = get_settings()

    try:
        # S-20-FIX: Validate token type at decode layer (defense-in-depth)
        # REVIEW-FIX: Use verify_token() (async, checks revocation) instead
        # of decode_token() (sync, no revocation check). Without this, a
        # revoked refresh token (from /logout or /logout-all) could still
        # mint new access tokens, completely defeating token revocation.
        payload = await verify_token(body.refresh_token, expected_type="refresh")
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    # R20-FIX: Guard against non-UUID sub/org claims. A token from a leaked
    # secret or different service could have valid JWT structure but non-UUID
    # claim values. Without this, uuid.UUID() raises ValueError → unhandled 500.
    try:
        user_id = uuid.UUID(payload["sub"])
        org_id = uuid.UUID(payload["org"])
    except (KeyError, ValueError, AttributeError):
        # R22-FIX: Added KeyError. If a token is missing the `org` claim
        # (e.g., from a leaked secret or incompatible issuer), payload["org"]
        # raises KeyError, not ValueError. Without catching KeyError, this
        # returns an unhandled 500 instead of 401.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token claims",
        )

    # REVIEW-FIX R2: Validate user BEFORE revoking the old refresh token.
    # Previously, we revoked first, then checked user — if the user was
    # deactivated, the old refresh token was already burned and the 401
    # left the client with NO valid tokens and NO way to re-auth.
    factory = get_session_factory()
    async with factory() as session:
        async with session.begin():
            # R37-FIX: Set auth_mode for RLS bypass — same as login/register.
            # Without this, SELECT auth.users returns 0 rows under FORCE RLS.
            from sqlalchemy import text as _text
            await session.execute(_text("SET LOCAL app.auth_mode = 'true'"))

            result = await session.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()

            if user is None or not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="User not found or deactivated",
                )

            # C2-FIX: Verify user still belongs to the org claimed in the
            # refresh token.  Without this check a user removed from org-A
            # could keep minting access tokens scoped to org-A.
            if str(user.organization_id) != str(org_id):
                logger.warning(
                    "refresh_org_mismatch",
                    user_id=str(user_id),
                    token_org=str(org_id),
                    actual_org=str(user.organization_id),
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Organization membership changed — please login again",
                )

            # R10-FIX: Capture ORM values inside session scope
            _user_id = user.id
            _org_id = user.organization_id
            _role = user.role

    # REVIEW-FIX: Revoke the old refresh token AFTER validating user.
    # Uses atomic SET NX so concurrent /refresh requests with the same
    # token are rejected: the second caller gets was_set=False → 401.
    # R19-FIX: jti and exp are now mandatory claims (enforced by decode_token),
    # so the previous `if old_jti and old_exp:` conditional is removed.
    # Without mandatory claims, a token missing jti would skip ALL revocation
    # and single-use enforcement, making refresh tokens infinitely replayable.
    old_jti = payload["jti"]
    old_exp = payload["exp"]
    try:
        from app.services.token_revocation import get_revocation_store
        store = get_revocation_store()
        # R21-FIX: Guard fromtimestamp() against OverflowError/OSError.
        # A maliciously crafted exp claim (e.g., 9999999999999) causes
        # fromtimestamp() to raise OverflowError, returning 500 to the client.
        try:
            old_expires_at = datetime.fromtimestamp(old_exp, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token expiry",
            )
        was_set = await store.revoke(old_jti, old_expires_at)
        if not was_set:
            # REVIEW-FIX: This refresh token was already used (race condition
            # or replay attack). Reject to enforce single-use semantics.
            logger.warning("refresh_token_replay", jti=old_jti, user_id=str(user_id))
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token already used",
            )
    except HTTPException:
        raise  # Re-raise our own 401
    except RuntimeError:
        # Revocation store not initialized — fail-closed in production
        # R31-FIX: Removed `from app.config import get_settings` local import.
        # Python treats any name imported/assigned in a function body as a
        # local variable throughout the entire function. This caused
        # `settings = get_settings()` (line 360) to raise UnboundLocalError
        # because the compiler saw the later local import and expected a local
        # variable, but the import only executes inside this except block.
        if get_settings().is_production:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Token revocation service unavailable",
            )
    except Exception as exc:
        # R18-FIX: Redis ConnectionError/TimeoutError — fail-closed in
        # production. Without this, single-use enforcement on refresh
        # tokens is silently bypassed during Valkey outages, allowing a
        # stolen refresh token to mint unlimited access tokens.
        logger.error("refresh_revocation_failed", jti=old_jti, error=str(exc)[:200])
        # R31-FIX: Same local-import scoping fix as above.
        if get_settings().is_production:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Token revocation service unavailable",
            )

    # R37-FIX: Audit log for token refresh (security-relevant event).
    try:
        async with factory() as session:
            async with session.begin():
                from app.middleware.tenant import TenantContext as _TC, set_tenant_context
                await set_tenant_context(session, _TC(
                    organization_id=str(_org_id),
                    user_id=str(_user_id),
                    role=_role,
                ))
                await log_action(
                    session=session,
                    action="user.token_refresh",
                    entity_type="user",
                    entity_id=_user_id,
                    user_id=_user_id,
                    organization_id=_org_id,
                    ip_address=_client_ip(request),
                    user_agent=request.headers.get("user-agent", "")[:256],
                )
    except Exception as audit_exc:
        logger.error("refresh_audit_failed", error=str(audit_exc)[:200])

    # Always use the user's *current* org, never the stale token claim
    return TokenResponse(
        access_token=create_access_token(_user_id, _org_id, _role),
        refresh_token=create_refresh_token(_user_id, _org_id),
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.get("/me", response_model=UserResponse)
async def get_me(user: CurrentUser) -> UserResponse:
    """Get current authenticated user's profile."""
    return UserResponse.model_validate(user)


@router.post("/logout")
async def logout(
    ctx: CurrentContext,
    request: Request,
    body: LogoutRequest | None = None,
) -> Response:
    """Logout: revoke the current access token and optionally the refresh token.

    R16-FIX: Previously only revoked the access token. An attacker with
    the refresh token could still mint new access tokens after logout.
    Now accepts optional refresh_token in body to revoke both.
    """
    # Re-extract the raw token from the Authorization header
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Bearer token",
        )
    raw_token = auth_header[7:]

    from app.services.token_revocation import get_revocation_store

    # R17-FIX: Enforce expected_type="access" for defense-in-depth.
    # R20-FIX: Use payload["jti"] directly — claims are mandatory (R19-FIX).
    # R34-FIX: Use verify_token() (async, checks revocation) instead of
    # decode_token() (sync, no revocation check). Using the non-revocation
    # path here creates an inconsistency: if a user calls /logout-all from
    # another session, then /logout from this session, decode_token() would
    # succeed on the already-revoked token. verify_token() correctly rejects it.
    try:
        payload = await verify_token(raw_token, expected_type="access")
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked access token",
        )
    jti = payload["jti"]
    exp_ts = payload["exp"]

    # R21-FIX: Guard fromtimestamp() against OverflowError/OSError.
    try:
        expires_at = datetime.fromtimestamp(exp_ts, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token expiry",
        )
    # R18-FIX: Wrap in try/except to handle Valkey unavailability.
    # Previously, get_revocation_store() or store.revoke() raising RuntimeError
    # or redis.ConnectionError caused unhandled 500. Return 503 instead.
    try:
        store = get_revocation_store()
        await store.revoke(jti, expires_at)

        # R16-FIX: Also revoke refresh token if provided
        if body and body.refresh_token:
            try:
                refresh_payload = decode_token(body.refresh_token, expected_type="refresh")
                # R18-FIX: Verify refresh token belongs to the same user.
                # Without this, any authenticated user can revoke another user's
                # refresh token by providing it in the logout request body.
                # R27-FIX-4: Use payload["key"] (claims are mandatory per R26-FIX-24),
                # fix wrong else branch (was paired with `if r_jti and r_exp` instead
                # of `if sub == user_id`), and eliminate falsy-value .get() bug.
                # R30-FIX-6: Use bracket access — "sub" is a mandatory claim
                # enforced by decode_token(). .get() silently returns None for
                # malformed tokens, making the ownership check always fail
                # instead of raising a clear KeyError.
                if refresh_payload["sub"] == ctx.user_id:
                    r_jti = refresh_payload["jti"]
                    r_exp = refresh_payload["exp"]
                    try:
                        r_expires_at = datetime.fromtimestamp(r_exp, tz=timezone.utc)
                    except (OverflowError, OSError, ValueError):
                        r_expires_at = None
                    if r_expires_at is not None:
                        await store.revoke(r_jti, r_expires_at)
                else:
                    logger.warning(
                        "logout_refresh_token_mismatch",
                        token_sub=refresh_payload.get("sub"),
                        user_id=ctx.user_id,
                    )
            except JWTError:
                pass  # Invalid/expired refresh token — ignore, we're logging out
            except Exception as exc:
                # R32-FIX: Don't silently swallow Valkey/infra failures.
                # If refresh token decode succeeded but revocation failed
                # (Valkey timeout), the refresh token remains valid. Log it.
                logger.error(
                    "logout_refresh_revocation_failed",
                    error=str(exc)[:200],
                )
    except Exception as exc:
        logger.error("logout_revocation_failed", jti=jti, error=str(exc)[:200])
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Token revocation service unavailable",
        )

    # REVIEW-FIX: log_action() is async and requires a DB session + entity_type.
    # R19-FIX: Set tenant context before writing audit logs.
    # R27-FIX-17: Wrap audit log in try/except so a DB failure after
    # successful token revocation does not return 500 to the client.
    # The critical operation (revocation) already succeeded above.
    try:
        factory = get_session_factory()
        async with factory() as session:
            async with session.begin():
                from app.middleware.tenant import TenantContext as _TC, set_tenant_context
                await set_tenant_context(session, _TC(
                    organization_id=ctx.organization_id,
                    user_id=ctx.user_id,
                    role=ctx.role,
                ))
                await log_action(
                    session=session,
                    action="user.logout",
                    entity_type="user",
                    entity_id=uuid.UUID(ctx.user_id),
                    user_id=uuid.UUID(ctx.user_id),
                    organization_id=uuid.UUID(ctx.organization_id),
                    metadata={"jti": jti},
                )
    except Exception as audit_exc:
        logger.error("logout_audit_failed", jti=jti, error=str(audit_exc)[:200])

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/logout-all")
async def logout_all(ctx: CurrentContext) -> Response:
    """Revoke ALL tokens for the current user (family revocation).

    All access and refresh tokens issued before this moment are invalidated.
    The user must login again to get new tokens.

    Use cases: password change, suspected account compromise, security lockout.
    """
    from app.services.token_revocation import get_revocation_store

    # R18-FIX: Wrap in try/except to handle Valkey unavailability gracefully.
    try:
        store = get_revocation_store()
        await store.revoke_all_user_tokens(ctx.user_id)
    except Exception as exc:
        logger.error("logout_all_revocation_failed", user_id=ctx.user_id, error=str(exc)[:200])
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Token revocation service unavailable",
        )

    # REVIEW-FIX: log_action() is async and requires a DB session + entity_type.
    # R19-FIX: Set tenant context before writing audit logs (same as /logout).
    # R27-FIX-17: Wrap in try/except — revocation already succeeded above.
    try:
        factory = get_session_factory()
        async with factory() as session:
            async with session.begin():
                from app.middleware.tenant import TenantContext as _TC, set_tenant_context
                await set_tenant_context(session, _TC(
                    organization_id=ctx.organization_id,
                    user_id=ctx.user_id,
                    role=ctx.role,
                ))
                await log_action(
                    session=session,
                    action="user.logout_all",
                    entity_type="user",
                    entity_id=uuid.UUID(ctx.user_id),
                    user_id=uuid.UUID(ctx.user_id),
                    organization_id=uuid.UUID(ctx.organization_id),
                )
    except Exception as audit_exc:
        logger.error("logout_all_audit_failed", user_id=ctx.user_id, error=str(audit_exc)[:200])

    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── TOTP / 2FA Endpoints ──────────────────────────────────────────────────────


class TOTPEnableResponse(BaseModel):
    """Returned when TOTP setup is initiated — contains the QR code URI.

    L-1-FIX: secret_preview removed. Returning ANY portion of the TOTP secret
    reduces the brute-force search space. The otpauth:// URI contains all
    information needed by authenticator apps; manual entry uses the full secret
    from the URI. Never expose partial secrets in API responses.
    """
    otpauth_uri: str


class TOTPVerifyRequest(BaseModel):
    code: str = Field(..., pattern=r"^\d{6}$", description="6-digit TOTP code")


class TOTPDisableRequest(BaseModel):
    code: str = Field(..., pattern=r"^\d{6}$", description="Current TOTP code to confirm disable")


@router.post("/totp/enable", response_model=TOTPEnableResponse)
async def totp_enable(
    ctx: CurrentContext,
    session: TenantSession,
) -> TOTPEnableResponse:
    """Step 1 of TOTP setup: generate a secret and return the otpauth:// URI.

    Does NOT activate TOTP yet — user must call /totp/verify with a valid
    code to confirm they have the QR code scanned correctly.
    TenantSession already sets RLS context; no need to call set_tenant_context again.
    """
    settings = get_settings()
    if not settings.enable_totp:
        raise HTTPException(status_code=404, detail="TOTP authentication is not enabled")

    try:
        import pyotp
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="pyotp package not installed") from exc

    from app.services.encryption import encrypt_totp_secret

    # Generate new TOTP secret and encrypt for storage
    secret = pyotp.random_base32()
    encrypted = encrypt_totp_secret(secret)

    # Persist encrypted secret (totp_enabled stays False until /totp/verify confirms it)
    result = await session.execute(
        select(User).where(User.id == uuid.UUID(ctx.user_id))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.totp_secret_enc = encrypted
    await session.flush()

    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=user.email, issuer_name="Nexsidi")
    logger.info("totp_setup_initiated", user_id=ctx.user_id)
    return TOTPEnableResponse(otpauth_uri=uri)


@router.post("/totp/verify", status_code=status.HTTP_204_NO_CONTENT)
async def totp_verify(
    body: TOTPVerifyRequest,
    ctx: CurrentContext,
    session: TenantSession,
) -> Response:
    """Step 2 of TOTP setup: verify first code and activate 2FA."""
    # Rate limit: 5 attempts per 5 minutes per user to prevent brute-force.
    await get_rate_limiter().check(
        "totp_verify_user", ctx.user_id, max_attempts=5, window_seconds=300
    )
    settings = get_settings()
    if not settings.enable_totp:
        raise HTTPException(status_code=404, detail="TOTP authentication is not enabled")

    try:
        import pyotp
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="pyotp package not installed") from exc

    from app.services.encryption import decrypt_totp_secret

    result = await session.execute(
        select(User).where(User.id == uuid.UUID(ctx.user_id))
    )
    user = result.scalar_one_or_none()
    if not user or not user.totp_secret_enc:
        raise HTTPException(status_code=400, detail="Call /totp/enable first")

    try:
        secret = decrypt_totp_secret(user.totp_secret_enc)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to decrypt TOTP secret")

    if not pyotp.TOTP(secret).verify(body.code, valid_window=1):
        logger.warning("totp_verify_invalid_code", user_id=ctx.user_id)
        raise HTTPException(status_code=400, detail="Invalid TOTP code")

    user.totp_enabled = True
    await session.flush()

    logger.info("totp_enabled", user_id=ctx.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/totp/disable", status_code=status.HTTP_204_NO_CONTENT)
async def totp_disable(
    body: TOTPDisableRequest,
    ctx: CurrentContext,
    session: TenantSession,
) -> Response:
    """Disable TOTP 2FA — requires current valid TOTP code to prevent session hijack."""
    # Rate limit: 5 attempts per 5 minutes per user to prevent brute-force.
    await get_rate_limiter().check(
        "totp_disable_user", ctx.user_id, max_attempts=5, window_seconds=300
    )
    settings = get_settings()
    if not settings.enable_totp:
        raise HTTPException(status_code=404, detail="TOTP authentication is not enabled")

    try:
        import pyotp
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="pyotp package not installed") from exc

    from app.services.encryption import decrypt_totp_secret

    result = await session.execute(
        select(User).where(User.id == uuid.UUID(ctx.user_id))
    )
    user = result.scalar_one_or_none()
    if not user or not user.totp_enabled or not user.totp_secret_enc:
        raise HTTPException(status_code=400, detail="TOTP is not enabled on this account")

    try:
        secret = decrypt_totp_secret(user.totp_secret_enc)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to decrypt TOTP secret")

    if not pyotp.TOTP(secret).verify(body.code, valid_window=1):
        logger.warning("totp_disable_invalid_code", user_id=ctx.user_id)
        raise HTTPException(status_code=400, detail="Invalid TOTP code")

    user.totp_enabled = False
    user.totp_secret_enc = None
    await session.flush()

    logger.info("totp_disabled", user_id=ctx.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Google OAuth 2.0 Endpoints ────────────────────────────────────────────────
#
# Flow:
#   1. GET /google/authorize  → redirect URL to Google consent screen
#   2. Google redirects to GET /google/callback?code=...&state=...
#   3. Exchange code for tokens, fetch user info, upsert user, return JWTs.
#
# CSRF: state param is a cryptographically random nonce. In production, store
# it in a short-lived server-side session or Valkey key; here it is embedded
# in the redirect URL and the frontend must pass it back for validation.
#
# httpx is already a dependency (see pyproject.toml) — no extra installs needed.


class GoogleAuthorizeResponse(BaseModel):
    """Authorization URL to redirect the browser to."""
    authorization_url: str
    state: str


_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
_GOOGLE_SCOPES = "openid email profile"


@router.get("/google/authorize", response_model=GoogleAuthorizeResponse)
async def google_authorize() -> GoogleAuthorizeResponse:
    """Return the Google OAuth consent screen URL.

    The frontend should redirect the browser to ``authorization_url``.
    It must store ``state`` and verify it matches the value returned
    in the callback to prevent CSRF attacks.

    Requires GOOGLE_CLIENT_ID to be configured.
    """
    settings = get_settings()
    if not settings.google_client_id:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Google OAuth is not configured — set GOOGLE_CLIENT_ID",
        )

    import urllib.parse

    state = secrets.token_urlsafe(16)
    # The redirect_uri must be registered in the Google Cloud Console.
    # We derive it from the first CORS origin so it works in all environments.
    redirect_uri = _google_redirect_uri(settings)

    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": _GOOGLE_SCOPES,
        "access_type": "offline",
        "prompt": "select_account",
        "state": state,
    }
    auth_url = f"{_GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"
    logger.info("google_oauth_authorize_requested")
    return GoogleAuthorizeResponse(authorization_url=auth_url, state=state)


@router.get("/google/callback", response_model=TokenResponse)
async def google_callback(
    code: str,
    state: str,
    request: Request,
) -> TokenResponse:
    """Handle Google's OAuth callback.

    Exchanges the authorization code for user info, then:
    - If google_id matches an existing user → log in.
    - If email matches an existing user → link Google account and log in.
    - Otherwise → create a new user (single-user org named after the Google name).

    Returns the same JWT pair as /login.
    """
    await get_rate_limiter().check("google_callback_ip", _client_ip(request), max_attempts=20)

    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Google OAuth is not configured",
        )

    redirect_uri = _google_redirect_uri(settings)

    # ── Step 1: exchange code for tokens ─────────────────────────────────────
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15.0) as client:
            token_resp = await client.post(
                _GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
    except Exception as exc:
        logger.error("google_token_exchange_failed", error=str(exc)[:200])
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to contact Google OAuth server",
        )

    if token_resp.status_code != 200:
        logger.warning("google_token_exchange_rejected", status=token_resp.status_code)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid authorization code or state",
        )

    token_data = token_resp.json()
    access_token_google = token_data.get("access_token")
    if not access_token_google:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No access token in Google response",
        )

    # ── Step 2: fetch user info ───────────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            userinfo_resp = await client.get(
                _GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token_google}"},
            )
    except Exception as exc:
        logger.error("google_userinfo_failed", error=str(exc)[:200])
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch user info from Google",
        )

    if userinfo_resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to retrieve user info from Google",
        )

    userinfo = userinfo_resp.json()
    google_id: str = userinfo.get("sub", "")
    email: str = userinfo.get("email", "").lower()
    name: str = userinfo.get("name") or email.split("@")[0]
    avatar_url: str | None = userinfo.get("picture")
    email_verified_google: bool = userinfo.get("email_verified", False)

    if not google_id or not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google did not return required user information",
        )

    # ── Step 3: upsert user ───────────────────────────────────────────────────
    from sqlalchemy.exc import IntegrityError as SAIntegrityError
    import re as _re

    factory = get_session_factory()
    try:
        async with factory() as session:
            async with session.begin():
                await session.execute(_text("SET LOCAL app.auth_mode = 'true'"))

                # Try google_id first (returning user with linked account)
                result = await session.execute(
                    select(User).where(User.google_id == google_id)
                )
                user = result.scalar_one_or_none()

                if user is None:
                    # Try email match (existing email/password account → link)
                    result = await session.execute(
                        select(User).where(User.email == email)
                    )
                    user = result.scalar_one_or_none()
                    if user is not None:
                        # Link Google account to existing user
                        user.google_id = google_id
                        if avatar_url and not user.avatar_url:
                            user.avatar_url = avatar_url
                        if email_verified_google:
                            user.email_verified = True
                        await session.flush()
                        logger.info("google_oauth_account_linked", user_id=str(user.id))

                if user is None:
                    # Brand-new user: create org + user
                    from app.models.core import Organization

                    base_slug = _re.sub(r"[^a-z0-9-]", "", name.lower().replace(" ", "-"))[:60]
                    if not base_slug:
                        base_slug = "org"
                    slug = base_slug
                    for suffix in range(0, 100):
                        candidate = slug if suffix == 0 else f"{base_slug}-{suffix}"
                        slug_check = await session.execute(
                            select(func.count()).select_from(Organization).where(Organization.slug == candidate)
                        )
                        if slug_check.scalar_one() == 0:
                            slug = candidate
                            break
                    else:
                        slug = f"{base_slug}-{uuid.uuid4().hex[:8]}"

                    org = Organization(name=f"{name}'s Organization", slug=slug, plan="free")
                    session.add(org)
                    await session.flush()

                    user = User(
                        organization_id=org.id,
                        email=email,
                        name=name,
                        role="org_admin",
                        auth_provider="google",
                        google_id=google_id,
                        avatar_url=avatar_url,
                        is_active=True,
                        email_verified=email_verified_google,
                        last_login_at=datetime.now(timezone.utc),
                    )
                    session.add(user)
                    await session.flush()
                    logger.info("google_oauth_new_user", user_id=str(user.id))
                else:
                    # Existing user — update last_login_at
                    if not user.is_active:
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Account is deactivated",
                        )
                    user.last_login_at = datetime.now(timezone.utc)

                await set_tenant_context(session, _TC(
                    organization_id=str(user.organization_id),
                    user_id=str(user.id),
                    role=user.role,
                ))

                await log_action(
                    session=session,
                    action="user.google_login",
                    entity_type="user",
                    entity_id=user.id,
                    user_id=user.id,
                    organization_id=user.organization_id,
                    ip_address=_client_ip(request),
                    user_agent=request.headers.get("user-agent", "")[:256],
                )

                _user_id = user.id
                _org_id = user.organization_id
                _role = user.role

    except HTTPException:
        raise
    except SAIntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Unable to create account — email may already be in use",
        )
    except Exception as exc:
        logger.error("google_oauth_upsert_failed", error=str(exc)[:200])
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to complete Google sign-in",
        )

    logger.info("google_oauth_login_complete", user_id=str(_user_id))

    return TokenResponse(
        access_token=create_access_token(_user_id, _org_id, _role),
        refresh_token=create_refresh_token(_user_id, _org_id),
        expires_in=settings.access_token_expire_minutes * 60,
    )


def _google_redirect_uri(settings) -> str:
    """Build the OAuth callback URI from the API's own URL.

    In production, GOOGLE_REDIRECT_URI should be set explicitly.
    Fallback: derive from first CORS origin (typically the frontend URL).
    """
    import os
    explicit = os.getenv("GOOGLE_REDIRECT_URI", "")
    if explicit:
        return explicit
    if settings.cors_origins:
        # API lives on the same origin but at /api/v1/auth/google/callback
        # Adjust if your API and frontend are on different origins.
        return f"{settings.cors_origins[0].rstrip('/')}/api/v1/auth/google/callback"
    return "http://localhost:8000/api/v1/auth/google/callback"
