"""DEPRECATED: In-memory API Key Service — replaced by api_key_auth.py (DB-backed).

This module stored keys in a Python dict (process memory) and was never connected
to the billing.api_keys database table used by the CRUD router. It has been
superseded by app.services.api_key_auth which:
  - Authenticates via SHA-256 lookup against billing.api_keys in PostgreSQL
  - Works across multi-worker/multi-replica deployments
  - Integrates with FastAPI via the ApiKeyContext dependency in dependencies.py

Do NOT use this module for new code. Use api_key_auth.authenticate_api_key() instead.

Original doc:
API Key Service: manages enterprise API keys (billing.api_keys).

Security (AUDIT FIX - Missing 6):
- Keys generated with secrets.token_urlsafe(64) (512-bit entropy)
- Keys HASHED with SHA-256 before storage -- never plaintext in DB
- Key prefix stored for identification (first 8 chars: "nxsd_abc1...")
- User sees full key ONCE at creation -- cannot retrieve later
- Scoping: keys scoped to specific projects or all projects
- Rotation: regenerate invalidates old key immediately
- Rate limiting: per-key (100/min standard, 1000/min enterprise)
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Key prefix for identification
KEY_PREFIX = "nxsd_"
KEY_PREFIX_LENGTH = 12       # "nxsd_" + 7 chars
KEY_BYTES = 64               # 512-bit entropy


class ApiKeyTier(str, Enum):
    """Rate limit tiers for API keys."""

    STANDARD = "standard"       # 100 requests/min
    PROFESSIONAL = "professional"  # 500 requests/min
    ENTERPRISE = "enterprise"   # 1000 requests/min


# Rate limits per tier (requests per minute)
TIER_RATE_LIMITS: dict[ApiKeyTier, int] = {
    ApiKeyTier.STANDARD: 100,
    ApiKeyTier.PROFESSIONAL: 500,
    ApiKeyTier.ENTERPRISE: 1000,
}


class ApiKeyStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


@dataclass(slots=True)
class ApiKeyRecord:
    """Represents an API key record (without the actual key).

    Mirrors the billing.api_keys table.
    """

    id: str
    organization_id: str
    user_id: str
    key_hash: str               # SHA-256 hash, never plaintext
    key_prefix: str             # First 12 chars for identification
    name: str
    scope: list[str] = field(default_factory=lambda: ["all"])
    tier: ApiKeyTier = ApiKeyTier.STANDARD
    rate_limit: int = 100
    last_used_at: str = ""
    expires_at: str = ""
    is_active: bool = True
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    @property
    def status(self) -> ApiKeyStatus:
        if not self.is_active:
            return ApiKeyStatus.REVOKED
        if self.expires_at:
            try:
                exp = datetime.fromisoformat(self.expires_at)
                # R37-FIX: Handle naive datetime comparison. fromisoformat()
                # can return naive datetime; comparing with aware raises TypeError.
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if exp < datetime.now(timezone.utc):
                    return ApiKeyStatus.EXPIRED
            except (ValueError, TypeError):
                # R37-FIX: Fail closed — malformed expiry treated as EXPIRED.
                # Previously returned ACTIVE, bypassing expiration entirely.
                logger.warning("api_key_unparseable_expiry", key_id=self.id, expires_at=self.expires_at)
                return ApiKeyStatus.EXPIRED
        return ApiKeyStatus.ACTIVE

    def to_dict(self) -> dict[str, Any]:
        """Public representation (never includes key_hash)."""
        return {
            "id": self.id,
            "name": self.name,
            "key_prefix": self.key_prefix,
            "scope": self.scope,
            "tier": self.tier.value,
            "rate_limit": self.rate_limit,
            "status": self.status.value,
            "last_used_at": self.last_used_at,
            "expires_at": self.expires_at,
            "is_active": self.is_active,
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class ApiKeyCreateResult:
    """Result of creating a new API key.

    Contains the full plaintext key -- shown to user ONCE.
    """

    record: ApiKeyRecord
    plaintext_key: str          # Full key shown once, then discarded


def generate_api_key() -> tuple[str, str, str]:
    """Generate a new API key.

    Returns:
        (plaintext_key, key_hash, key_prefix)
    """
    raw = secrets.token_urlsafe(KEY_BYTES)
    plaintext = f"{KEY_PREFIX}{raw}"
    key_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    prefix = plaintext[:KEY_PREFIX_LENGTH]
    return plaintext, key_hash, prefix


def hash_api_key(plaintext_key: str) -> str:
    """Hash an API key for lookup/verification."""
    return hashlib.sha256(plaintext_key.encode("utf-8")).hexdigest()


class ApiKeyService:
    """Manages API key lifecycle.

    In production, backed by billing.api_keys table.
    Currently: in-memory store for pipeline integration.
    """

    _RATE_EVICTION_INTERVAL = 100  # R32-FIX-MEM: evict stale keys every N checks

    def __init__(self) -> None:
        self._keys: dict[str, ApiKeyRecord] = {}  # key_hash -> record
        self._rate_counts: dict[str, list[float]] = {}  # key_hash -> timestamps
        self._rate_check_count: int = 0  # R32-FIX-MEM

    def create_key(
        self,
        organization_id: str,
        user_id: str,
        name: str,
        scope: list[str] | None = None,
        tier: ApiKeyTier = ApiKeyTier.STANDARD,
        expires_at: str = "",
    ) -> ApiKeyCreateResult:
        """Create a new API key.

        Returns the full key ONCE. It cannot be retrieved later.
        """
        plaintext, key_hash, prefix = generate_api_key()
        key_id = secrets.token_hex(16)

        record = ApiKeyRecord(
            id=key_id,
            organization_id=organization_id,
            user_id=user_id,
            key_hash=key_hash,
            key_prefix=prefix,
            name=name,
            scope=scope or ["all"],
            tier=tier,
            rate_limit=TIER_RATE_LIMITS[tier],
            expires_at=expires_at,
        )

        self._keys[key_hash] = record

        logger.info(
            "api_key_created",
            key_id=key_id,
            prefix=prefix,
            tier=tier.value,
            scope=record.scope,
        )

        return ApiKeyCreateResult(record=record, plaintext_key=plaintext)

    def verify_key(self, plaintext_key: str) -> ApiKeyRecord | None:
        """Verify an API key and return its record if valid.

        Returns None if key is invalid, revoked, or expired.
        """
        key_hash = hash_api_key(plaintext_key)
        record = self._keys.get(key_hash)

        if record is None:
            return None

        if record.status != ApiKeyStatus.ACTIVE:
            return None

        # Update last_used_at
        record.last_used_at = datetime.now(timezone.utc).isoformat()

        return record

    def check_rate_limit(self, plaintext_key: str) -> bool:
        """Check if an API key is within its rate limit.

        Returns True if request is allowed, False if rate limited.
        """
        key_hash = hash_api_key(plaintext_key)
        record = self._keys.get(key_hash)
        if record is None:
            return False

        now = datetime.now(timezone.utc).timestamp()
        window_start = now - 60  # 1-minute window

        # Get timestamps within the window
        timestamps = self._rate_counts.get(key_hash, [])
        active = [t for t in timestamps if t > window_start]

        if len(active) >= record.rate_limit:
            return False

        active.append(now)
        self._rate_counts[key_hash] = active

        # R32-FIX-MEM: Periodically evict stale rate-count entries for
        # keys that haven't been used recently. Without this, every unique
        # API key that's ever been rate-checked stays in memory forever.
        self._rate_check_count += 1
        if self._rate_check_count >= self._RATE_EVICTION_INTERVAL:
            self._rate_check_count = 0
            stale = [
                k for k, v in self._rate_counts.items()
                if not v or v[-1] < window_start
            ]
            for k in stale:
                del self._rate_counts[k]

        return True

    def revoke_key(self, key_id: str, user_id: str) -> bool:
        """Revoke an API key. Immediate effect.

        Only the key owner can revoke.
        """
        for record in self._keys.values():
            if record.id == key_id and record.user_id == user_id:
                record.is_active = False
                logger.info(
                    "api_key_revoked",
                    key_id=key_id,
                    prefix=record.key_prefix,
                )
                return True
        return False

    def regenerate_key(
        self,
        key_id: str,
        user_id: str,
    ) -> ApiKeyCreateResult | None:
        """Regenerate an API key. Old key is immediately invalidated.

        Returns new key, or None if key_id not found.
        """
        for key_hash, record in list(self._keys.items()):
            if record.id == key_id and record.user_id == user_id:
                # Revoke old key
                record.is_active = False
                del self._keys[key_hash]

                # Create new key with same config
                return self.create_key(
                    organization_id=record.organization_id,
                    user_id=record.user_id,
                    name=record.name,
                    scope=record.scope,
                    tier=record.tier,
                    expires_at=record.expires_at,
                )
        return None

    def list_keys(self, user_id: str) -> list[ApiKeyRecord]:
        """List all API keys for a user (active only by default)."""
        return [
            r for r in self._keys.values()
            if r.user_id == user_id and r.is_active
        ]

    def list_org_keys(self, organization_id: str) -> list[ApiKeyRecord]:
        """List all active API keys for an organization."""
        return [
            r for r in self._keys.values()
            if r.organization_id == organization_id and r.is_active
        ]

    def get_key_by_id(
        self,
        key_id: str,
        organization_id: str | None = None,
    ) -> ApiKeyRecord | None:
        """Get a key record by its ID.

        R36-FIX: Added organization_id for defense-in-depth tenant isolation.
        Previously, get_key_by_id() searched across ALL orgs — a cross-tenant
        attacker could retrieve another org's key record by guessing the key_id.
        list_keys() and list_org_keys() already filter by tenant, but this
        method did not. When organization_id is provided, only returns records
        belonging to that org.
        """
        for record in self._keys.values():
            if record.id == key_id:
                if organization_id is not None and record.organization_id != organization_id:
                    return None
                return record
        return None

    @property
    def total_keys(self) -> int:
        """Total active keys."""
        return sum(1 for r in self._keys.values() if r.is_active)


# ── Singleton ───────────────────────────────────────────────────

import threading as _threading
_api_key_lock = _threading.Lock()
_service: ApiKeyService | None = None


def get_api_key_service() -> ApiKeyService:
    """Get or create the API key service singleton."""
    global _service
    if _service is not None:
        return _service
    with _api_key_lock:
        if _service is None:
            _service = ApiKeyService()
        return _service
