"""Token Revocation Service: Valkey-backed JWT blocklist.

Provides two revocation mechanisms:
1. **Single-token revocation** (logout): Blocks a specific jti.
2. **Family revocation** (logout-all / password change): Blocks all tokens
   for a user issued before a cutoff timestamp.

Storage in Valkey:
    revoked:{jti}          → "1" (TTL = token expiry)
    revoked_user:{user_id} → ISO timestamp cutoff (TTL = max token lifetime)

Both keys auto-expire via Redis TTL — no manual cleanup needed.

Follows the same singleton init/shutdown pattern as context_engine.py.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)

# R28-FIX-16: Removed dead _MAX_TOKEN_TTL_SECONDS constant (was never used;
# TTL is now computed from settings.refresh_token_expire_days per R22-FIX).


class TokenRevocationStore:
    """Valkey-backed token revocation store.

    Uses Redis SET with TTL so revoked tokens auto-expire when they would
    have expired naturally. No unbounded memory growth.
    """

    def __init__(self, redis_client) -> None:  # type: ignore[type-arg]
        """Initialize with an async Redis/Valkey client.

        Args:
            redis_client: aioredis-compatible async client (redis.asyncio.Redis).
        """
        self._redis = redis_client

    async def revoke(self, jti: str, expires_at: datetime) -> bool:
        """Revoke a single token by its jti (JWT ID).

        The blocklist entry auto-expires when the token itself would have
        expired, preventing unbounded memory growth.

        REVIEW-FIX: Uses SET NX (set-if-not-exists) to atomically claim
        the revocation. Returns True if this call performed the revocation,
        False if it was already revoked. This prevents race conditions
        where two concurrent /refresh requests with the same refresh token
        both succeed: the second SET NX returns False → we reject it.

        Args:
            jti: The token's unique identifier (from the jti claim).
            expires_at: When the token expires (from the exp claim).

        Returns:
            True if revoked successfully, False if already revoked (race).
        """
        now = datetime.now(timezone.utc)
        ttl = max(int((expires_at - now).total_seconds()), 1)
        # SET NX: only sets if key doesn't exist → atomic single-use
        was_set = await self._redis.set(f"revoked:{jti}", "1", nx=True, ex=ttl)
        if was_set:
            logger.info("token_revoked", jti=jti, ttl_seconds=ttl)
            return True
        else:
            logger.warning("token_already_revoked", jti=jti)
            return False

    async def is_revoked(self, jti: str) -> bool:
        """Check if a specific token jti has been revoked.

        Args:
            jti: The token's unique identifier.

        Returns:
            True if the token has been explicitly revoked.
        """
        if not jti:
            # R20-FIX: Fail-CLOSED, not fail-open. python-jose's require option
            # enforces that jti EXISTS in the payload, but allows jti="" (empty
            # string). `not ""` is True, so this branch catches it. Returning
            # False (not revoked) would let empty-jti tokens bypass ALL revocation
            # including /logout and /logout-all.
            return True
        return await self._redis.exists(f"revoked:{jti}") > 0

    async def revoke_all_user_tokens(self, user_id: str) -> None:
        """Family revocation: invalidate ALL tokens for a user.

        Sets a cutoff timestamp. Any token issued before this cutoff
        (checked via the iat claim) is considered revoked.

        Useful for: password change, account compromise, admin lockout.

        Args:
            user_id: The user's UUID string.
        """
        now = datetime.now(timezone.utc)
        # R22-FIX: Use configurable refresh_token_expire_days instead of
        # hardcoded 7 days. If the operator sets refresh_token_expire_days=30,
        # the old 7-day TTL caused the family revocation key to expire while
        # refresh tokens were still valid (days 8-30), silently un-revoking
        # all tokens for the user.
        settings = get_settings()
        max_ttl = settings.refresh_token_expire_days * 24 * 3600
        await self._redis.setex(
            f"revoked_user:{user_id}",
            max_ttl,
            now.isoformat(),
        )
        logger.info("user_tokens_revoked", user_id=user_id)

    async def is_user_revoked(self, user_id: str, issued_at: datetime) -> bool:
        """Check if a token was issued before the user's family revocation cutoff.

        Args:
            user_id: The user's UUID string.
            issued_at: When the token was issued (from the iat claim).

        Returns:
            True if the token was issued before the revocation cutoff.
        """
        if not user_id:
            # R20-FIX: Fail-CLOSED on empty user_id (same rationale as is_revoked)
            return True
        cutoff_raw = await self._redis.get(f"revoked_user:{user_id}")
        if cutoff_raw is None:
            return False
        # Redis may return bytes or str depending on decode_responses setting
        cutoff_str = cutoff_raw.decode("utf-8") if isinstance(cutoff_raw, bytes) else cutoff_raw
        try:
            cutoff = datetime.fromisoformat(cutoff_str)
            # R17-FIX: Ensure timezone-aware comparison. On Python < 3.11,
            # fromisoformat() may produce a naive datetime while issued_at
            # is timezone-aware, causing TypeError. Force UTC if missing.
            if cutoff.tzinfo is None:
                cutoff = cutoff.replace(tzinfo=timezone.utc)
            # R27-FIX-18: Use <= instead of <.  A token minted at the exact
            # same microsecond as a /logout-all call should be considered revoked
            # (fail-closed).  With strict <, the TOCTOU race allows a concurrent
            # login/refresh to produce a token that survives the revocation.
            return issued_at <= cutoff
        except (ValueError, TypeError):
            # R21-FIX: Fail-CLOSED on corrupted data. Previously returned False,
            # meaning an attacker who corrupts the revocation timestamp in Valkey
            # (e.g., via SSRF or shared-Valkey compromise) could un-revoke all
            # family-revoked tokens for that user.
            logger.warning("invalid_revocation_cutoff", user_id=user_id, raw=cutoff_str)
            return True


# ── Singleton Management ──────────────────────────────────────────

_store: TokenRevocationStore | None = None


def get_revocation_store() -> TokenRevocationStore:
    """Get the Token Revocation Store singleton.

    Raises RuntimeError if not initialized. Callers in async context should
    use ``try_lazy_init_revocation_store()`` for recovery (see verify_token).

    R28-FIX-16: Removed dead lazy-reconnection code block that retrieved the
    event loop but never used it (lines 178-185 were entirely non-functional).
    """
    global _store
    if _store is None:
        raise RuntimeError(
            "Token Revocation Store not initialized. "
            "Call init_revocation_store() first."
        )
    return _store


_lazy_init_lock: asyncio.Lock | None = None


def _get_lazy_init_lock() -> asyncio.Lock:
    """Get or create the lazy init lock (sync-safe, same pattern as valkey_pool)."""
    global _lazy_init_lock
    if _lazy_init_lock is None:
        _lazy_init_lock = asyncio.Lock()
    return _lazy_init_lock


async def try_lazy_init_revocation_store() -> bool:
    """R25-FIX-3: Attempt lazy re-initialization of the revocation store.

    Called by verify_token() when get_revocation_store() raises RuntimeError.
    Returns True if successfully initialized, False otherwise.

    R26-FIX-26: Protected by asyncio.Lock to prevent TOCTOU race when
    multiple concurrent requests trigger lazy init simultaneously.
    """
    global _store
    if _store is not None:
        return True
    async with _get_lazy_init_lock():
        if _store is not None:
            return True
        try:
            from app.services.valkey_pool import get_valkey_client
            client = await get_valkey_client()
            await client.ping()
            _store = TokenRevocationStore(client)
            logger.info("token_revocation_store_lazy_initialized")
            return True
        except Exception as exc:
            logger.debug("token_revocation_store_lazy_init_failed", error=str(exc)[:100])
            return False


async def init_revocation_store() -> TokenRevocationStore:
    """Initialize the Token Revocation Store with a Valkey connection.

    DEFERRED-FIX-10: Uses shared Valkey pool instead of creating a
    dedicated connection pool.

    Called once at app startup.
    """
    global _store
    if _store is not None:
        return _store

    settings = get_settings()
    try:
        from app.services.valkey_pool import get_valkey_client

        client = await get_valkey_client()
        # Verify connection
        await client.ping()
        _store = TokenRevocationStore(client)
        logger.info("token_revocation_store_initialized")
        return _store
    except Exception as exc:
        # R35-FIX: Sanitize exception before logging. Redis/Valkey connection
        # errors include the full URL with password (e.g., redis://:s3cret@host).
        # Without sanitization, Valkey password leaks to structured logging sinks.
        import re
        _safe = re.sub(r"://:[^@]+@", "://[REDACTED]@", str(exc))
        logger.warning(
            "token_revocation_store_init_failed",
            error=_safe[:500],
        )
        # R17-FIX: In production, do NOT install a fallback store.
        if settings.is_production:
            logger.error(
                "token_revocation_store_init_failed_production",
                error=_safe[:500],
                hint="Valkey is REQUIRED in production for token revocation",
            )
            return None  # type: ignore[return-value]
        # Dev only: graceful degradation with no-op fallback
        _store = _FallbackRevocationStore()
        return _store


async def shutdown_revocation_store() -> None:
    """Shutdown the Token Revocation Store. Call at app shutdown.

    DEFERRED-FIX-10: Does NOT close the Redis client — it's shared.
    """
    global _store
    if _store is not None:
        _store = None
        logger.info("token_revocation_store_shutdown")


class _FallbackRevocationStore(TokenRevocationStore):
    """No-op fallback when Valkey is unavailable.

    All checks return False (not revoked). Revocation calls log warnings.
    This ensures the app can start and serve requests even without Valkey,
    but revocation features are degraded.
    """

    def __init__(self) -> None:
        self._redis = None  # type: ignore[assignment]

    async def revoke(self, jti: str, expires_at: datetime) -> bool:
        # R25-FIX-8: Return True in dev fallback to allow /refresh to work.
        # R17-FIX wanted to prevent replay attacks in production when Valkey
        # was down, but production never uses the fallback (it stays None →
        # RuntimeError → 503). The fallback is dev-only. Returning False here
        # makes /refresh ALWAYS fail with 401 "already used" in dev, which
        # completely breaks token refresh for all developers without Valkey.
        logger.warning("revocation_unavailable", jti=jti, reason="valkey_down_dev_fallback")
        return True

    async def is_revoked(self, jti: str) -> bool:
        # R22-FIX: Fail-closed on empty jti even in dev fallback.
        # Without this, dev has weaker semantics than prod, masking bugs.
        if not jti:
            return True
        return False

    async def revoke_all_user_tokens(self, user_id: str) -> None:
        logger.warning("revocation_unavailable", user_id=user_id, reason="valkey_down")

    async def is_user_revoked(self, user_id: str, issued_at: datetime) -> bool:
        # R22-FIX: Fail-closed on empty user_id even in dev fallback.
        if not user_id:
            return True
        return False
