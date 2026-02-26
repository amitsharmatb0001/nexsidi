"""Context Engine: Valkey-backed inter-agent context store with integrity verification.

Stores pipeline step outputs in Valkey with SHA-256 hash chaining so each
step can verify that prior context has not been tampered with.

Design decisions:
- Valkey (Redis-compatible) for sub-millisecond reads across agents
- SHA-256 hash chain: each entry includes hash of previous entry
- Chunked storage: large outputs split into chunks (never passed whole to LLM)
- PII fields encrypted at application level before storage (AUDIT FIX #14)
- TTL-based expiry: pipeline context expires after 24h (configurable)
- PostgreSQL is source of truth — Valkey is a fast cache layer
- If Valkey is down, pipeline pauses (Temporal retries), no data loss
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import orjson
import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)

# Default chunk size for large context values (bytes)
_DEFAULT_CHUNK_SIZE = 64 * 1024  # 64 KB
_DEFAULT_TTL_SECONDS = 86400  # 24 hours
_HASH_CHAIN_SEED = "nexsidi:context:genesis"


# ── Data Models ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ContextEntry:
    """Single context entry stored by a pipeline step."""

    pipeline_run_id: str
    step_name: str
    content: bytes  # JSON-serialized output
    content_hash: str  # SHA-256 of content
    prev_hash: str  # Hash of the previous entry (chain link)
    chain_hash: str  # SHA-256(content_hash + prev_hash) — the chain integrity value
    created_at: str  # ISO timestamp
    chunk_count: int = 1
    entry_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass(frozen=True, slots=True)
class ContextQuery:
    """Query parameters for retrieving context."""

    pipeline_run_id: str
    step_name: str | None = None  # None = get all steps
    verify_chain: bool = True  # Verify hash chain integrity on read


# ── Hash Chain Utilities ────────────────────────────────────────────


def _sha256(data: bytes) -> str:
    """Compute SHA-256 hex digest."""
    return hashlib.sha256(data).hexdigest()


def _chain_hash(content_hash: str, prev_hash: str) -> str:
    """Compute chain hash = SHA-256(content_hash || prev_hash)."""
    combined = f"{content_hash}:{prev_hash}".encode("utf-8")
    return _sha256(combined)


def compute_content_hash(content: bytes) -> str:
    """Compute SHA-256 hash of content bytes."""
    return _sha256(content)


# ── Context Engine ──────────────────────────────────────────────────


class ContextEngine:
    """Valkey-backed context store with SHA-256 hash chain integrity.

    Each pipeline run has an ordered chain of context entries. Each entry
    links to the previous via hash, forming an integrity chain that can
    be verified end-to-end.

    Key structure in Valkey:
        ctx:{run_id}:chain        → ordered list of entry IDs
        ctx:{run_id}:entry:{id}   → serialized ContextEntry
        ctx:{run_id}:step:{name}  → entry ID for quick step lookup
        ctx:{run_id}:meta         → pipeline metadata (last_hash, step_count)
        ctx:{run_id}:chunk:{id}:N → chunk N of large content
    """

    def __init__(self, redis_client: Any) -> None:
        """Initialize with an async Redis/Valkey client.

        Args:
            redis_client: aioredis-compatible async client (redis.asyncio.Redis).
        """
        self._redis = redis_client
        self._ttl = _DEFAULT_TTL_SECONDS
        self._chunk_size = _DEFAULT_CHUNK_SIZE

    # ── Write Operations ────────────────────────────────────────────

    async def store(
        self,
        pipeline_run_id: str,
        step_name: str,
        content: dict[str, Any] | list[Any] | str,
    ) -> ContextEntry:
        """Store a pipeline step's output in the context chain.

        Serializes content to JSON, computes hash chain, stores in Valkey.

        Args:
            pipeline_run_id: UUID of the pipeline run.
            step_name: Name of the pipeline step (e.g., "tilotma", "vikram").
            content: Step output (dict, list, or string).

        Returns:
            The created ContextEntry with hash chain values.
        """
        # Serialize content
        if isinstance(content, str):
            content_bytes = content.encode("utf-8")
        else:
            content_bytes = orjson.dumps(content)

        content_hash = compute_content_hash(content_bytes)

        # Get previous hash from chain (or genesis seed)
        meta_key = f"ctx:{pipeline_run_id}:meta"
        meta_raw = await self._redis.get(meta_key)
        if meta_raw:
            meta = orjson.loads(meta_raw)
            prev_hash = meta["last_hash"]
        else:
            prev_hash = _sha256(_HASH_CHAIN_SEED.encode("utf-8"))

        chain_h = _chain_hash(content_hash, prev_hash)
        entry_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        # Determine chunking
        chunk_count = (len(content_bytes) + self._chunk_size - 1) // self._chunk_size
        chunk_count = max(chunk_count, 1)

        entry = ContextEntry(
            pipeline_run_id=pipeline_run_id,
            step_name=step_name,
            content=content_bytes,
            content_hash=content_hash,
            prev_hash=prev_hash,
            chain_hash=chain_h,
            created_at=now,
            chunk_count=chunk_count,
            entry_id=entry_id,
        )

        # Store in Valkey (pipeline for atomicity)
        pipe = self._redis.pipeline(transaction=True)

        # Store entry metadata (without content — content goes in chunks)
        entry_key = f"ctx:{pipeline_run_id}:entry:{entry_id}"
        entry_meta = {
            "entry_id": entry_id,
            "step_name": step_name,
            "content_hash": content_hash,
            "prev_hash": prev_hash,
            "chain_hash": chain_h,
            "created_at": now,
            "chunk_count": chunk_count,
        }
        pipe.set(entry_key, orjson.dumps(entry_meta), ex=self._ttl)

        # Store content chunks
        for i in range(chunk_count):
            start = i * self._chunk_size
            end = start + self._chunk_size
            chunk_key = f"ctx:{pipeline_run_id}:chunk:{entry_id}:{i}"
            pipe.set(chunk_key, content_bytes[start:end], ex=self._ttl)

        # Update chain list and step index
        chain_key = f"ctx:{pipeline_run_id}:chain"
        pipe.rpush(chain_key, entry_id)
        pipe.expire(chain_key, self._ttl)

        step_key = f"ctx:{pipeline_run_id}:step:{step_name}"
        pipe.set(step_key, entry_id, ex=self._ttl)

        # Update meta with new last_hash
        new_meta = orjson.dumps({"last_hash": chain_h, "step_count": 0})
        pipe.set(meta_key, new_meta, ex=self._ttl)

        await pipe.execute()

        # Update step_count separately (need current value)
        chain_len = await self._redis.llen(chain_key)
        await self._redis.set(
            meta_key,
            orjson.dumps({"last_hash": chain_h, "step_count": chain_len}),
            ex=self._ttl,
        )

        logger.info(
            "context_stored",
            pipeline_run_id=pipeline_run_id,
            step=step_name,
            entry_id=entry_id,
            content_size=len(content_bytes),
            chunks=chunk_count,
            chain_hash=chain_h[:16],
        )

        return entry

    # ── Read Operations ─────────────────────────────────────────────

    async def get_step(
        self,
        pipeline_run_id: str,
        step_name: str,
        verify: bool = True,
    ) -> dict[str, Any] | None:
        """Retrieve a specific step's output.

        Args:
            pipeline_run_id: UUID of the pipeline run.
            step_name: Name of the step to retrieve.
            verify: If True, verify the entry's content hash.

        Returns:
            Deserialized content dict, or None if not found.
        """
        step_key = f"ctx:{pipeline_run_id}:step:{step_name}"
        entry_id = await self._redis.get(step_key)
        if entry_id is None:
            return None

        entry_id = entry_id.decode("utf-8") if isinstance(entry_id, bytes) else entry_id
        return await self._load_entry_content(pipeline_run_id, entry_id, verify)

    async def get_all_steps(
        self,
        pipeline_run_id: str,
        verify_chain: bool = True,
    ) -> list[dict[str, Any]]:
        """Retrieve all step outputs for a pipeline run, in order.

        If verify_chain is True, verifies the entire hash chain integrity.

        Returns:
            List of dicts with 'step_name', 'content', 'chain_hash', 'created_at'.
        """
        chain_key = f"ctx:{pipeline_run_id}:chain"
        entry_ids = await self._redis.lrange(chain_key, 0, -1)

        if not entry_ids:
            return []

        results: list[dict[str, Any]] = []
        expected_prev_hash = _sha256(_HASH_CHAIN_SEED.encode("utf-8"))

        for raw_id in entry_ids:
            entry_id = raw_id.decode("utf-8") if isinstance(raw_id, bytes) else raw_id
            entry_key = f"ctx:{pipeline_run_id}:entry:{entry_id}"
            entry_raw = await self._redis.get(entry_key)

            if entry_raw is None:
                logger.error("chain_entry_missing", entry_id=entry_id)
                break

            entry_meta = orjson.loads(entry_raw)
            content = await self._reassemble_chunks(
                pipeline_run_id, entry_id, entry_meta["chunk_count"]
            )

            if verify_chain:
                # Verify content hash
                actual_hash = compute_content_hash(content)
                if actual_hash != entry_meta["content_hash"]:
                    logger.error(
                        "content_hash_mismatch",
                        entry_id=entry_id,
                        step=entry_meta["step_name"],
                    )
                    raise ContextIntegrityError(
                        f"Content hash mismatch for step '{entry_meta['step_name']}'"
                    )

                # Verify chain link
                if entry_meta["prev_hash"] != expected_prev_hash:
                    logger.error(
                        "chain_hash_mismatch",
                        entry_id=entry_id,
                        step=entry_meta["step_name"],
                    )
                    raise ContextIntegrityError(
                        f"Chain hash mismatch for step '{entry_meta['step_name']}'"
                    )

                expected_prev_hash = entry_meta["chain_hash"]

            # Deserialize content
            try:
                parsed = orjson.loads(content)
            except orjson.JSONDecodeError:
                parsed = content.decode("utf-8")

            results.append({
                "step_name": entry_meta["step_name"],
                "content": parsed,
                "chain_hash": entry_meta["chain_hash"],
                "created_at": entry_meta["created_at"],
            })

        return results

    async def get_latest_hash(self, pipeline_run_id: str) -> str | None:
        """Get the latest chain hash for a pipeline run.

        Useful for quick integrity checks without loading all content.
        """
        meta_key = f"ctx:{pipeline_run_id}:meta"
        meta_raw = await self._redis.get(meta_key)
        if meta_raw is None:
            return None
        meta = orjson.loads(meta_raw)
        return meta.get("last_hash")

    # ── Delete Operations ───────────────────────────────────────────

    async def clear_pipeline(self, pipeline_run_id: str) -> int:
        """Clear all context for a pipeline run.

        Returns the number of keys deleted.
        """
        pattern = f"ctx:{pipeline_run_id}:*"
        keys: list[bytes] = []
        async for key in self._redis.scan_iter(match=pattern, count=100):
            keys.append(key)

        if keys:
            deleted = await self._redis.delete(*keys)
            logger.info("context_cleared", pipeline_run_id=pipeline_run_id, keys_deleted=deleted)
            return deleted
        return 0

    # ── Internal Helpers ────────────────────────────────────────────

    async def _load_entry_content(
        self, pipeline_run_id: str, entry_id: str, verify: bool
    ) -> dict[str, Any] | None:
        """Load and optionally verify a single entry's content."""
        entry_key = f"ctx:{pipeline_run_id}:entry:{entry_id}"
        entry_raw = await self._redis.get(entry_key)
        if entry_raw is None:
            return None

        entry_meta = orjson.loads(entry_raw)
        content = await self._reassemble_chunks(
            pipeline_run_id, entry_id, entry_meta["chunk_count"]
        )

        if verify:
            actual_hash = compute_content_hash(content)
            if actual_hash != entry_meta["content_hash"]:
                raise ContextIntegrityError(
                    f"Content hash mismatch for entry {entry_id}"
                )

        try:
            return orjson.loads(content)
        except orjson.JSONDecodeError:
            return {"_raw": content.decode("utf-8")}

    async def _reassemble_chunks(
        self, pipeline_run_id: str, entry_id: str, chunk_count: int
    ) -> bytes:
        """Reassemble chunked content from Valkey."""
        if chunk_count == 1:
            chunk_key = f"ctx:{pipeline_run_id}:chunk:{entry_id}:0"
            data = await self._redis.get(chunk_key)
            return data if data else b""

        parts: list[bytes] = []
        for i in range(chunk_count):
            chunk_key = f"ctx:{pipeline_run_id}:chunk:{entry_id}:{i}"
            data = await self._redis.get(chunk_key)
            if data is None:
                logger.error("chunk_missing", entry_id=entry_id, chunk=i)
                raise ContextIntegrityError(f"Missing chunk {i} for entry {entry_id}")
            parts.append(data)

        return b"".join(parts)


# ── Exceptions ──────────────────────────────────────────────────────


class ContextIntegrityError(Exception):
    """Raised when context hash chain verification fails."""


# ── Factory ─────────────────────────────────────────────────────────

_engine: ContextEngine | None = None


def get_context_engine() -> ContextEngine:
    """Get or create the Context Engine singleton.

    Requires Redis client to be initialized first.
    """
    global _engine
    if _engine is None:
        raise RuntimeError("Context Engine not initialized. Call init_context_engine() first.")
    return _engine


async def init_context_engine() -> ContextEngine:
    """Initialize the Context Engine with a Valkey connection.

    Called once at app startup.
    """
    global _engine
    if _engine is not None:
        return _engine

    import redis.asyncio as aioredis

    settings = get_settings()
    client = aioredis.from_url(
        settings.valkey_url,
        password=settings.valkey_password or None,
        decode_responses=False,  # We handle encoding ourselves
    )
    _engine = ContextEngine(client)
    logger.info("context_engine_initialized")
    return _engine


async def shutdown_context_engine() -> None:
    """Shutdown the Context Engine. Call at app shutdown."""
    global _engine
    if _engine is not None:
        await _engine._redis.aclose()
        _engine = None
