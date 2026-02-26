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
from jose import JWTError, jwt

from app.config import get_settings

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
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT token.

    Returns the payload dict on success.
    Raises JWTError on invalid/expired tokens.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError:
        raise
    return payload
