"""Authentication service: password hashing, JWT creation/verification.

Security decisions:
- bcrypt for password hashing (uses direct bcrypt library, not passlib).
  passlib is unmaintained and incompatible with bcrypt 4.x / Python 3.13.
- JWT with HS256 (symmetric) for v1. Switch to RS256 (asymmetric) before
  multi-service deployment.
- Access token: 15 min. Refresh token: 7 days.
- Token includes: sub (user_id), org (organization_id), role, exp, iat, jti.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from jwt.exceptions import PyJWTError as JWTError

from app.config import get_settings


import structlog

logger = structlog.get_logger(__name__)
# bcrypt max input is 72 bytes — we truncate to prevent ValueError
_BCRYPT_MAX_BYTES = 72


def _prep_password(password: str) -> bytes:
    """Encode password to bytes and truncate to bcrypt's 72-byte limit."""
    pw_bytes = password.encode("utf-8")
    return pw_bytes[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt with auto-generated salt."""
    pw_bytes = _prep_password(password)
    hashed = bcrypt.hashpw(pw_bytes, bcrypt.gensalt(rounds=12))
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    pw_bytes = _prep_password(plain_password)
    hash_bytes = hashed_password.encode("utf-8")
    return bcrypt.checkpw(pw_bytes, hash_bytes)


_ALLOWED_ROLES = frozenset({
    "org_admin", "admin", "developer", "viewer", "member", "billing",
    "super_admin",
})


def create_access_token(
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    role: str,
) -> str:
    """Create a short-lived JWT access token (15 min default).

    Claims:
        sub: user_id (str)
        org: organization_id (str)
        role: user role
        exp: expiration timestamp
        iat: issued at
        jti: unique token ID (for revocation if needed)
    """
    # R19-FIX: Validate role at token creation time. Without this, a corrupted
    # DB row (or a future code path with attacker-influenced role) produces a
    # JWT with an invalid role string. The downstream tenant middleware's
    # _validate_role() catches SQL injection, but the JWT is already issued.
    # Users with invalid-role tokens get 500 errors on every authenticated
    # request — permanently locked out until the token expires.
    if role not in _ALLOWED_ROLES:
        raise ValueError(f"Invalid role for JWT: {role!r}")
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.access_token_expire_minutes)

    payload = {
        "sub": str(user_id),
        "org": str(organization_id),
        "role": role,
        "exp": expire,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "type": "access",
        # R11-FIX: Add iss/aud claims to prevent cross-environment token reuse.
        # Without these, a token from staging (same HS256 secret) would be accepted
        # by production, or a token from another service sharing the secret could
        # grant access.
        "iss": "nexsidi",
        "aud": "nexsidi-api",
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> str:
    """Create a long-lived refresh token (7 days default)."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=settings.refresh_token_expire_days)

    payload = {
        "sub": str(user_id),
        "org": str(organization_id),
        "exp": expire,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "type": "refresh",
        # R11-FIX: Add iss/aud claims (same as access token)
        "iss": "nexsidi",
        "aud": "nexsidi-api",
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str, *, expected_type: str | None = None) -> dict:
    """Decode and validate a JWT token.

    Args:
        token: The JWT token string.
        expected_type: If set, validates the ``type`` claim matches
            (e.g. ``"access"`` or ``"refresh"``).  Raises JWTError if
            the claim is missing or doesn't match.  This is defense-in-depth;
            callers should still validate at the business layer.

    Returns the payload dict on success.
    Raises JWTError on invalid/expired tokens or type mismatch.
    """
    settings = get_settings()
    try:
        # R11-FIX: Validate iss/aud claims at decode layer.
        # Rejects tokens from other environments or services.
        # R19-FIX: Require jti, sub, iat, exp as mandatory claims. Without this,
        # a token missing jti silently bypasses ALL revocation (both /logout and
        # /logout-all), and a token missing sub/iat bypasses family revocation.
        # This closes the refresh-token-replay gap where `if old_jti and old_exp:`
        # in /refresh would skip single-use enforcement for tokens without jti.
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            audience="nexsidi-api",
            issuer="nexsidi",
            # R26-FIX-24: Require "org" claim for defense-in-depth.
            # R28-FIX-3: Also require "role" for access tokens — tokens without
            # role bypass RBAC. R29-FIX-1: Refresh tokens intentionally omit
            # "role" (the refresh endpoint reads current role from DB). Making
            # "role" mandatory for ALL tokens broke the entire refresh flow.
            options={"require": (
                ["jti", "sub", "iat", "exp", "org", "role"]
                if expected_type != "refresh"
                else ["jti", "sub", "iat", "exp", "org"]
            )},
        )
    except JWTError:
        raise

    # S-20-FIX: Defense-in-depth type validation at the decode layer.
    # Prevents a refresh token (7-day expiry) from being used as an
    # access token (15-min expiry), which would bypass expiry controls.
    if expected_type is not None:
        token_type = payload.get("type")
        if token_type != expected_type:
            raise JWTError(
                f"Token type mismatch: expected {expected_type!r}, got {token_type!r}"
            )

    return payload


async def verify_token(token: str, *, expected_type: str | None = None) -> dict:
    """Decode + check revocation. Use this for all authenticated endpoints.

    This is the async counterpart to decode_token(). It first performs all
    the same validations (signature, exp, iss, aud, type), then additionally
    checks the Valkey-backed revocation store for:
    1. Single-token revocation (jti blocklist — from /logout)
    2. Family revocation (user cutoff — from /logout-all or password change)

    Args:
        token: The JWT token string.
        expected_type: If set, validates the ``type`` claim matches.

    Returns the payload dict on success.
    Raises JWTError on invalid/expired/revoked tokens.
    """
    payload = decode_token(token, expected_type=expected_type)

    # Check revocation store.
    # R16-FIX: FAIL-CLOSED in production. If the revocation store is unavailable,
    # all tokens pass validation — a stolen/revoked token remains usable. This is
    # a critical security gap: /logout and /logout-all APPEAR to work (HTTP 204)
    # but have zero actual effect. In production, deny access instead.
    try:
        from app.services.token_revocation import get_revocation_store
        store = get_revocation_store()

        # R19-FIX: jti, sub, iat are now mandatory claims (enforced by
        # decode_token). Previously these used .get() with conditional checks,
        # meaning tokens missing these claims silently bypassed revocation.
        # 1. Single-token revocation (jti blocklist)
        jti = payload["jti"]
        if await store.is_revoked(jti):
            raise JWTError("Token has been revoked")

        # 2. Family revocation (all user tokens before cutoff)
        user_id = payload["sub"]
        iat_raw = payload["iat"]
        # R20-FIX: Guard against extreme iat values. A crafted JWT with
        # "iat": 999999999999999 passes jose's decode but causes
        # OverflowError/OSError in datetime.fromtimestamp() on Windows/32-bit.
        # This would propagate as unhandled 500 (not JWTError).
        try:
            iat = datetime.fromtimestamp(iat_raw, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            raise JWTError("Invalid iat timestamp in token")
        if await store.is_user_revoked(user_id, iat):
            raise JWTError("Token has been revoked")
    except JWTError:
        raise  # Re-raise revocation rejections
    except RuntimeError:
        # Revocation store not initialized.
        # R25-FIX-3: Attempt lazy re-initialization before failing.
        # If Valkey was briefly unavailable at startup but is back now,
        # this recovers without a manual restart.
        from app.services.token_revocation import try_lazy_init_revocation_store
        reinitialized = await try_lazy_init_revocation_store()
        if reinitialized:
            # Retry the revocation check with the newly initialized store
            try:
                store = get_revocation_store()
                jti = payload["jti"]
                if await store.is_revoked(jti):
                    raise JWTError("Token has been revoked")
                user_id = payload["sub"]
                iat_raw = payload["iat"]
                try:
                    iat = datetime.fromtimestamp(iat_raw, tz=timezone.utc)
                except (ValueError, OverflowError, OSError):
                    raise JWTError("Invalid iat timestamp in token")
                if await store.is_user_revoked(user_id, iat):
                    raise JWTError("Token has been revoked")
            except JWTError:
                raise
            except Exception:
                # R26-FIX-1: FAIL-CLOSED after lazy re-init. Previously
                # this silently swallowed errors and fell through to
                # `return payload`, accepting revoked tokens as valid
                # when Valkey hiccuped during the re-check.
                from app.config import get_settings as _gs
                if _gs().is_production:
                    raise JWTError("Token revocation service unavailable")
                # Dev: graceful degradation
        else:
            from app.config import get_settings
            if get_settings().is_production:
                # R16-FIX: Fail-closed in production
                raise JWTError("Token revocation service unavailable")
            # In development, skip checks (graceful degradation)
    except Exception:
        # R18-FIX: Catch redis.ConnectionError, TimeoutError, etc.
        # Without this, a Valkey outage AFTER startup causes unhandled exception
        # in verify_token → 500 Internal Server Error on EVERY authenticated
        # endpoint (total service DOS, not graceful fail-closed).
        from app.config import get_settings
        if get_settings().is_production:
            raise JWTError("Token revocation service unavailable")
        # In dev, skip checks (graceful degradation)

    return payload
