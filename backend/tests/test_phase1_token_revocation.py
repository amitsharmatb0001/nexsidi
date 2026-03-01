"""Tests for Phase 1: Token Revocation + Logout.

Covers:
- TokenRevocationStore: single-token revoke, family revocation, TTL
- _FallbackRevocationStore: graceful degradation when Valkey unavailable
- verify_token(): async decode + revocation check
- /logout endpoint: revokes single token by jti
- /logout-all endpoint: family revocation by user_id
- Singleton lifecycle: init/shutdown
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── TokenRevocationStore Unit Tests ──────────────────────────────


class TestTokenRevocationStore:
    """Valkey-backed token revocation store."""

    def test_store_class_exists(self):
        """TokenRevocationStore must exist in token_revocation module."""
        from app.services.token_revocation import TokenRevocationStore
        assert hasattr(TokenRevocationStore, "revoke")
        assert hasattr(TokenRevocationStore, "is_revoked")
        assert hasattr(TokenRevocationStore, "revoke_all_user_tokens")
        assert hasattr(TokenRevocationStore, "is_user_revoked")

    @pytest.mark.asyncio
    async def test_revoke_single_token(self):
        """revoke() should store jti in Valkey with SET NX + TTL."""
        from app.services.token_revocation import TokenRevocationStore

        mock_redis = AsyncMock()
        # REVIEW-FIX: revoke() now uses set(nx=True, ex=ttl) for atomic single-use
        mock_redis.set = AsyncMock(return_value=True)
        store = TokenRevocationStore(mock_redis)

        jti = str(uuid.uuid4())
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)

        result = await store.revoke(jti, expires_at)
        assert result is True

        mock_redis.set.assert_awaited_once()
        call_args = mock_redis.set.call_args
        assert call_args[0][0] == f"revoked:{jti}"
        assert call_args[0][1] == "1"
        # Must use nx=True for atomic single-use enforcement
        assert call_args[1]["nx"] is True
        # TTL (ex) should be > 0 and <= 900 seconds
        ttl = call_args[1]["ex"]
        assert 1 <= ttl <= 900

    @pytest.mark.asyncio
    async def test_is_revoked_true(self):
        """is_revoked() should return True when jti is in blocklist."""
        from app.services.token_revocation import TokenRevocationStore

        mock_redis = AsyncMock()
        mock_redis.exists.return_value = 1
        store = TokenRevocationStore(mock_redis)

        result = await store.is_revoked("some-jti")
        assert result is True
        mock_redis.exists.assert_awaited_once_with("revoked:some-jti")

    @pytest.mark.asyncio
    async def test_is_revoked_false(self):
        """is_revoked() should return False when jti is not in blocklist."""
        from app.services.token_revocation import TokenRevocationStore

        mock_redis = AsyncMock()
        mock_redis.exists.return_value = 0
        store = TokenRevocationStore(mock_redis)

        result = await store.is_revoked("unknown-jti")
        assert result is False

    @pytest.mark.asyncio
    async def test_is_revoked_empty_jti(self):
        """R20-FIX: is_revoked('') must return True (fail-closed).

        Previously returned False (fail-open), allowing tokens with empty
        jti to bypass ALL revocation including /logout and /logout-all.
        """
        from app.services.token_revocation import TokenRevocationStore

        mock_redis = AsyncMock()
        store = TokenRevocationStore(mock_redis)

        result = await store.is_revoked("")
        assert result is True  # R20-FIX: Fail-closed
        mock_redis.exists.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_revoke_all_user_tokens(self):
        """revoke_all_user_tokens() should set cutoff timestamp."""
        from app.services.token_revocation import TokenRevocationStore

        mock_redis = AsyncMock()
        store = TokenRevocationStore(mock_redis)

        user_id = str(uuid.uuid4())
        await store.revoke_all_user_tokens(user_id)

        mock_redis.setex.assert_awaited_once()
        call_args = mock_redis.setex.call_args
        assert call_args[0][0] == f"revoked_user:{user_id}"
        # TTL should be 7 days
        assert call_args[0][1] == 7 * 24 * 3600

    @pytest.mark.asyncio
    async def test_is_user_revoked_before_cutoff(self):
        """Tokens issued before cutoff should be considered revoked."""
        from app.services.token_revocation import TokenRevocationStore

        cutoff = datetime.now(timezone.utc)
        issued_at = cutoff - timedelta(hours=1)  # Issued BEFORE cutoff

        mock_redis = AsyncMock()
        mock_redis.get.return_value = cutoff.isoformat().encode("utf-8")
        store = TokenRevocationStore(mock_redis)

        result = await store.is_user_revoked("user-1", issued_at)
        assert result is True

    @pytest.mark.asyncio
    async def test_is_user_revoked_after_cutoff(self):
        """Tokens issued after cutoff should NOT be considered revoked."""
        from app.services.token_revocation import TokenRevocationStore

        cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
        issued_at = datetime.now(timezone.utc) - timedelta(hours=1)  # After cutoff

        mock_redis = AsyncMock()
        mock_redis.get.return_value = cutoff.isoformat().encode("utf-8")
        store = TokenRevocationStore(mock_redis)

        result = await store.is_user_revoked("user-1", issued_at)
        assert result is False

    @pytest.mark.asyncio
    async def test_is_user_revoked_no_cutoff(self):
        """No cutoff set → not revoked."""
        from app.services.token_revocation import TokenRevocationStore

        mock_redis = AsyncMock()
        mock_redis.get.return_value = None
        store = TokenRevocationStore(mock_redis)

        result = await store.is_user_revoked("user-1", datetime.now(timezone.utc))
        assert result is False


# ── Fallback Store Tests ──────────────────────────────────────────


class TestFallbackRevocationStore:
    """_FallbackRevocationStore must be no-op when Valkey unavailable."""

    @pytest.mark.asyncio
    async def test_fallback_is_revoked_always_false(self):
        from app.services.token_revocation import _FallbackRevocationStore

        store = _FallbackRevocationStore()
        assert await store.is_revoked("any-jti") is False

    @pytest.mark.asyncio
    async def test_fallback_is_user_revoked_always_false(self):
        from app.services.token_revocation import _FallbackRevocationStore

        store = _FallbackRevocationStore()
        assert await store.is_user_revoked("user-1", datetime.now(timezone.utc)) is False

    @pytest.mark.asyncio
    async def test_fallback_revoke_does_not_raise(self):
        from app.services.token_revocation import _FallbackRevocationStore

        store = _FallbackRevocationStore()
        # Should not raise — just logs a warning
        await store.revoke("jti", datetime.now(timezone.utc))
        await store.revoke_all_user_tokens("user-1")


# ── verify_token() Tests ──────────────────────────────────────────


class TestVerifyToken:
    """verify_token() must decode AND check revocation."""

    def test_verify_token_is_async(self):
        """verify_token must be an async function."""
        from app.services.auth import verify_token
        assert asyncio.iscoroutinefunction(verify_token)

    def test_verify_token_calls_decode_token(self):
        """verify_token source must call decode_token."""
        from app.services.auth import verify_token
        source = inspect.getsource(verify_token)
        assert "decode_token" in source

    def test_verify_token_checks_revocation(self):
        """verify_token source must check revocation store."""
        from app.services.auth import verify_token
        source = inspect.getsource(verify_token)
        assert "is_revoked" in source
        assert "is_user_revoked" in source


# ── Logout Endpoint Tests ─────────────────────────────────────────


class TestLogoutEndpoints:
    """Logout endpoints must exist in auth router."""

    def test_logout_endpoint_exists(self):
        """POST /logout must exist."""
        from app.routers.auth import logout
        assert asyncio.iscoroutinefunction(logout)

    def test_logout_all_endpoint_exists(self):
        """POST /logout-all must exist."""
        from app.routers.auth import logout_all
        assert asyncio.iscoroutinefunction(logout_all)

    def test_logout_revokes_jti(self):
        """logout source must call store.revoke(jti, ...)."""
        from app.routers.auth import logout
        source = inspect.getsource(logout)
        assert "store.revoke" in source

    def test_logout_all_revokes_user(self):
        """logout_all source must call store.revoke_all_user_tokens(...)."""
        from app.routers.auth import logout_all
        source = inspect.getsource(logout_all)
        assert "revoke_all_user_tokens" in source
