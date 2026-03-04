"""Distributed sliding-window rate limiter backed by Valkey/Redis.

Replaces the in-memory _AuthRateLimiter that could be bypassed in
multi-worker / multi-replica deployments.

Algorithm: Redis sorted-set sliding window.
  - ZADD key <now_ms> <now_ms>   — record this attempt
  - ZREMRANGEBYSCORE key 0 <cutoff_ms>  — evict expired
  - ZCARD key                           — count current window
  - EXPIRE key <window_seconds>         — auto-cleanup

All three operations run atomically in a Lua script to prevent
TOCTOU races between check and record.

Fallback: if Valkey is unavailable (ConnectionError), the limiter
allows the request through and logs a warning rather than hard-failing
the auth endpoint.  This is the safer degradation mode: a temporary
Valkey outage should not lock users out.  Monitoring must alert on
repeated "rate_limiter_valkey_unavailable" log events.

Key format:
    ratelimit:{scope}:{key}
    e.g. ratelimit:login_email:alice@example.com
         ratelimit:login_ip:203.0.113.42
         ratelimit:register_ip:203.0.113.42
"""

from __future__ import annotations

import time
from typing import Any

import structlog
from fastapi import HTTPException, status

logger = structlog.get_logger(__name__)

# Lua script: atomic check-and-record in a single round-trip.
# Returns the count of attempts in the current window AFTER adding this one.
# Arguments: KEYS[1]=rate_limit_key, ARGV[1]=now_ms, ARGV[2]=cutoff_ms,
#            ARGV[3]=window_seconds (for EXPIRE)
_SLIDING_WINDOW_SCRIPT = """
local key        = KEYS[1]
local now_ms     = tonumber(ARGV[1])
local cutoff_ms  = tonumber(ARGV[2])
local window_sec = tonumber(ARGV[3])

-- Remove expired members
redis.call('ZREMRANGEBYSCORE', key, 0, cutoff_ms)
-- Add this attempt (score = timestamp_ms, member = timestamp_ms as string
-- + a small random suffix to allow duplicate timestamps)
redis.call('ZADD', key, now_ms, tostring(now_ms) .. ':' .. tostring(math.random(1, 1000000)))
-- Set TTL so the key auto-expires even if never cleaned up
redis.call('EXPIRE', key, window_sec)
-- Return current window size (AFTER adding this attempt)
return redis.call('ZCARD', key)
"""


class ValKeyRateLimiter:
    """Distributed sliding-window rate limiter.

    Usage::

        limiter = ValKeyRateLimiter()
        await limiter.check(
            scope="login_email",
            key="alice@example.com",
            max_attempts=10,
            window_seconds=900,
        )

    Raises HTTPException(429) if the limit is exceeded.
    Falls back to allow-all with a warning log if Valkey is unavailable.
    """

    def __init__(self) -> None:
        self._script_sha: str | None = None

    async def _get_client(self) -> Any:
        """Return the shared Valkey async client."""
        from app.services.valkey_pool import get_valkey_client

        return await get_valkey_client()

    async def _ensure_script(self, client: Any) -> str:
        """Load the Lua script once and cache its SHA for EVALSHA."""
        if self._script_sha is None:
            self._script_sha = await client.script_load(_SLIDING_WINDOW_SCRIPT)
        return self._script_sha

    async def check(
        self,
        scope: str,
        key: str,
        max_attempts: int,
        window_seconds: int = 900,
    ) -> None:
        """Check the rate limit and record this attempt atomically.

        Args:
            scope:          Limiter category (e.g. "login_email", "login_ip").
            key:            The specific subject (email, IP address, etc.).
            max_attempts:   Maximum allowed attempts in the window.
            window_seconds: Sliding window duration in seconds (default 15 min).

        Raises:
            HTTPException(429): Limit exceeded — caller should return immediately.
        """
        redis_key = f"ratelimit:{scope}:{key}"
        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - (window_seconds * 1000)

        try:
            client = await self._get_client()
            sha = await self._ensure_script(client)
            count: int = await client.evalsha(
                sha,
                1,  # numkeys
                redis_key,  # KEYS[1]
                now_ms,  # ARGV[1]
                cutoff_ms,  # ARGV[2]
                window_seconds,  # ARGV[3]
            )
        except Exception as exc:  # noqa: BLE001
            # Valkey unavailable — degrade gracefully (allow request, alert)
            logger.warning(
                "rate_limiter_valkey_unavailable",
                scope=scope,
                error=str(exc),
                action="allowing_request",
            )
            return

        if count > max_attempts:
            logger.warning(
                "rate_limit_exceeded",
                scope=scope,
                key=key,
                count=count,
                max=max_attempts,
                window_seconds=window_seconds,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many attempts. Please try again later.",
                headers={"Retry-After": str(window_seconds)},
            )


# Module-level singleton — instantiated once, shared across all requests.
# No connection held at import time; connections come from the shared pool.
_rate_limiter = ValKeyRateLimiter()


def get_rate_limiter() -> ValKeyRateLimiter:
    """Return the shared ValKeyRateLimiter instance."""
    return _rate_limiter
