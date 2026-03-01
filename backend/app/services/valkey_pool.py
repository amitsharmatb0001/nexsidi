"""Shared Valkey (Redis-compatible) connection pool.

DEFERRED-FIX-10: Previously, context_engine, prompt_engine, and
token_revocation each created their own ``aioredis.from_url()`` connection
pool. Three pools × default pool_size=10 = 30 connections to Valkey.
On a machine with 4 Celery workers, that's 120 connections — which can
exhaust Valkey's ``maxclients`` (default 10,000) in a large deployment.

This module provides a single shared pool used by all three services.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)

_client: Any | None = None
# R25-FIX-11: Protect singleton init with asyncio.Lock. Without this, two
# concurrent coroutines can both pass the `if _client is not None` check,
# both call `aioredis.from_url()`, and the second overwrites _client — the
# first connection pool becomes an orphan with leaked connections. The Lock
# itself is created lazily (synchronous, no await → no event loop yield →
# no TOCTOU race on the Lock's own creation in a single-threaded event loop).
_client_lock: asyncio.Lock | None = None


def _get_client_lock() -> asyncio.Lock:
    """Get or create the client initialization lock (lazy, sync-safe)."""
    global _client_lock
    if _client_lock is None:
        _client_lock = asyncio.Lock()
    return _client_lock


async def get_valkey_client() -> Any:
    """Get or create the shared Valkey async client.

    R25-FIX-11: Protected by asyncio.Lock to prevent duplicate pool creation
    when multiple coroutines call this concurrently during startup.

    Returns an ``redis.asyncio.Redis`` instance backed by a shared
    connection pool. All callers reuse the same pool.
    """
    global _client
    # Fast path — no lock needed when already initialized
    if _client is not None:
        return _client

    async with _get_client_lock():
        # Double-check after acquiring lock (another coroutine may have initialized)
        if _client is not None:
            return _client

        import redis.asyncio as aioredis

        settings = get_settings()
        # R26-FIX-9: Only pass password kwarg when explicitly configured.
        # `password=None` overrides any password embedded in the URL (common
        # for managed Redis services like ElastiCache, Memorystore).
        pool_kwargs: dict[str, Any] = {
            "decode_responses": False,  # All consumers handle encoding themselves
            "max_connections": 20,  # Single pool shared across all services
            # R33-FIX: Add socket_timeout to prevent indefinite blocking.
            # Without this, a hung Valkey server causes all 20 pool connections
            # to block forever. Every subsequent operation (token revocation,
            # rate limiting, context engine) hangs, cascading to HTTP 503 for
            # all requests. 5s is generous for in-region Valkey calls (~1ms RTT).
            "socket_timeout": 5.0,
            "socket_connect_timeout": 5.0,
        }
        if settings.valkey_password:
            pool_kwargs["password"] = settings.valkey_password

        _client = aioredis.from_url(
            settings.valkey_url,
            **pool_kwargs,
        )
        logger.info("shared_valkey_pool_initialized")
        return _client


async def shutdown_valkey_client() -> None:
    """Close the shared Valkey connection pool. Call at app shutdown.

    R26-FIX-30: Protected by _client_lock to prevent race with concurrent
    get_valkey_client() calls during shutdown. Without this, a concurrent
    caller can get a reference to the client between our check and close.
    """
    global _client
    async with _get_client_lock():
        if _client is not None:
            await _client.aclose()
            _client = None
            logger.info("shared_valkey_pool_closed")


def reset_valkey_client() -> None:
    """Reset the singleton for testing. Does NOT close connections."""
    global _client, _client_lock
    _client = None
    _client_lock = None
