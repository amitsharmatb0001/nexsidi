"""DB-backed API key authentication service.

Authenticates HTTP requests carrying an ``X-Api-Key`` header by:
1. SHA-256 hashing the raw key.
2. Looking up the hash in billing.api_keys.
3. Verifying is_active=True and not expired.
4. Updating last_used_at (fire-and-forget).
5. Returning a TenantContext built from the ApiKey's organization_id/user_id.

Rate limiting uses the same ValKeyRateLimiter as the auth endpoints, keyed by
api_key_id so limits are per-key rather than per-IP.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import HTTPException, status

from app.middleware.tenant import TenantContext
from app.models.billing import ApiKey

logger = structlog.get_logger(__name__)

# Role value used in TenantContext for API-key-authenticated requests.
# Must be in _ALLOWED_ROLES (tenant.py) — see that module's allowlist.
_API_KEY_ROLE = "api_key"


def _hash_key(raw_key: str) -> str:
    """Return the SHA-256 hex digest of the raw API key."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


async def authenticate_api_key(
    raw_key: str,
    session_factory: Any,
) -> tuple[TenantContext, ApiKey] | None:
    """Authenticate a raw API key against the billing.api_keys table.

    Steps:
    - Hash the raw key (SHA-256).
    - Query billing.api_keys by key_hash.
    - Verify is_active=True and not expired.
    - Fire-and-forget last_used_at update (never fails auth).
    - Return (TenantContext, ApiKey) or None if invalid.

    Args:
        raw_key:         The plaintext key from the X-Api-Key header.
        session_factory: Callable that returns an AsyncSession context manager
                         (i.e. the result of get_session_factory()).

    Returns:
        (TenantContext, ApiKey) tuple if valid, None otherwise.
    """
    from sqlalchemy import select

    key_hash = _hash_key(raw_key)

    try:
        async with session_factory() as session:
            # Use autobegin (no explicit begin()) — read-only lookup.
            result = await session.execute(
                select(ApiKey).where(ApiKey.key_hash == key_hash)
            )
            api_key: ApiKey | None = result.scalar_one_or_none()
    except Exception as exc:
        logger.error(
            "api_key_auth_db_error",
            error=str(exc)[:200],
            hint="DB unavailable during API key lookup",
        )
        return None

    if api_key is None:
        return None

    if not api_key.is_active:
        logger.warning("api_key_auth_inactive", key_prefix=api_key.key_prefix)
        return None

    now = datetime.now(timezone.utc)
    if api_key.expires_at is not None:
        expires = api_key.expires_at
        # Normalise naive datetime (DB may return naive UTC)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= now:
            logger.warning(
                "api_key_auth_expired",
                key_prefix=api_key.key_prefix,
                expires_at=expires.isoformat(),
            )
            return None

    # Build context before the fire-and-forget write so we can return quickly.
    ctx = TenantContext(
        organization_id=str(api_key.organization_id),
        user_id=str(api_key.user_id),
        role=_API_KEY_ROLE,
    )

    # Fire-and-forget: update last_used_at.  We deliberately do NOT await a
    # separate session inside a try/except so that a transient DB hiccup cannot
    # prevent an otherwise valid API key from authenticating.
    import asyncio

    async def _update_last_used(key_id: uuid.UUID) -> None:
        try:
            from sqlalchemy import update

            async with session_factory() as sess:
                async with sess.begin():
                    await sess.execute(
                        update(ApiKey)
                        .where(ApiKey.id == key_id)
                        .values(last_used_at=datetime.now(timezone.utc))
                    )
        except Exception as exc:
            logger.warning(
                "api_key_last_used_update_failed",
                key_id=str(key_id),
                error=str(exc)[:200],
            )

    asyncio.ensure_future(_update_last_used(api_key.id))

    logger.info(
        "api_key_authenticated",
        key_prefix=api_key.key_prefix,
        org_id=str(api_key.organization_id),
        user_id=str(api_key.user_id),
    )
    return ctx, api_key


# ── Scope checking ────────────────────────────────────────────────────────────


def require_scope(scope: str):
    """Return a FastAPI dependency that checks whether the API key grants *scope*.

    Only meaningful when the request is authenticated via an API key.  If the
    request is JWT-authenticated (no API key), the dependency is a no-op.

    Example::

        @router.get("/admin/report")
        async def report(
            ctx: CurrentContext,
            _: Annotated[None, Depends(require_scope("reports:read"))],
        ): ...

    The ApiKey is stored in the request state by ``get_current_user_context_from_api_key``
    (see dependencies.py) so we can retrieve it here without an extra DB round-trip.
    """
    from typing import Annotated

    from fastapi import Depends, Request

    async def _check_scope(request: Request) -> None:
        api_key: ApiKey | None = getattr(request.state, "api_key", None)
        if api_key is None:
            # JWT-authenticated — no scope restriction from API key layer.
            return

        granted: list[str] = list(api_key.scope or [])
        if "all" in granted or scope in granted:
            return

        logger.warning(
            "api_key_scope_denied",
            required=scope,
            granted=granted,
            key_prefix=api_key.key_prefix,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"API key does not have the required scope: {scope!r}",
        )

    return Annotated[None, Depends(_check_scope)]


# ── Per-key rate limiting ─────────────────────────────────────────────────────


async def check_api_key_rate_limit(request: "Request") -> None:  # noqa: F821
    """FastAPI dependency: enforce per-key rate limit from ApiKey.rate_limit.

    Keyed by api_key_id so limits are per-key across all workers/replicas.
    Skipped automatically when the request is JWT-authenticated (no API key in
    request.state).

    The rate_limit column stores requests-per-minute.  We use a 60-second
    sliding window.

    Usage::

        @router.get("/data")
        async def data(
            ctx: CurrentContext,
            _: Annotated[None, Depends(check_api_key_rate_limit)],
        ): ...
    """
    from fastapi import Request as _Request

    # Import here to avoid circular imports at module load time.
    from app.services.rate_limiter import get_rate_limiter

    # This dependency is declared as a plain async function; FastAPI injects
    # ``Request`` automatically when it is listed in the function signature.
    # We re-declare the type inline to satisfy the linter without a top-level
    # import that might cause circular dependencies.


async def _check_api_key_rate_limit(request: Any) -> None:
    """Inner implementation — see check_api_key_rate_limit docstring."""
    from app.services.rate_limiter import get_rate_limiter

    api_key: ApiKey | None = getattr(request.state, "api_key", None)
    if api_key is None:
        # JWT-authenticated — skip API-key rate limiting.
        return

    key_id = str(api_key.id)
    rate_limit: int = int(api_key.rate_limit or 100)

    await get_rate_limiter().check(
        scope="api_key",
        key=key_id,
        max_attempts=rate_limit,
        window_seconds=60,  # sliding 1-minute window
    )


# Re-export as the public FastAPI-injectable dependency.
check_api_key_rate_limit = _check_api_key_rate_limit
