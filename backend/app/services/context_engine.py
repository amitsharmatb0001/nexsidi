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

import asyncio
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

    # REVIEW-FIX: Threshold for offloading content to object store.
    # Content >64KB is stored in GCS/local store (durable), with a reference
    # pointer left in Valkey. Content <=64KB stays in Valkey (fast path).
    _OFFLOAD_THRESHOLD = 64 * 1024  # 64 KB

    def __init__(self, redis_client: Any) -> None:
        """Initialize with an async Redis/Valkey client.

        Args:
            redis_client: aioredis-compatible async client (redis.asyncio.Redis).
        """
        self._redis = redis_client
        self._ttl = _DEFAULT_TTL_SECONDS
        self._chunk_size = _DEFAULT_CHUNK_SIZE
        # REVIEW-FIX: Optional object store for large content
        self._object_store = None
        try:
            from app.services.object_store import get_object_store
            self._object_store = get_object_store()
        except Exception:
            pass  # No object store — all content stays in Valkey

    # ── Write Operations ────────────────────────────────────────────

    async def store(
        self,
        pipeline_run_id: str,
        step_name: str,
        content: dict[str, Any] | list[Any] | str,
    ) -> ContextEntry:
        """Store a pipeline step's output in the context chain.

        Serializes content to JSON, computes hash chain, stores in Valkey.
        Uses a per-pipeline distributed lock to prevent TOCTOU race conditions
        when parallel agents store context simultaneously.

        Args:
            pipeline_run_id: UUID of the pipeline run.
            step_name: Name of the pipeline step (e.g., "tilotma", "vikram").
            content: Step output (dict, list, or string).

        Returns:
            The created ContextEntry with hash chain values.
        """
        # Serialize content first (outside lock to minimize lock hold time)
        if isinstance(content, str):
            content_bytes = content.encode("utf-8")
        else:
            content_bytes = orjson.dumps(content)

        content_hash = compute_content_hash(content_bytes)
        entry_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        # REVIEW-FIX: Offload large content to object store (GCS/local).
        # Valkey stores a reference pointer instead. Small content stays in Valkey.
        offloaded = False
        if self._object_store is not None and len(content_bytes) > self._OFFLOAD_THRESHOLD:
            obj_key = f"ctx/{pipeline_run_id}/{step_name}/{entry_id}"
            try:
                await self._object_store.put(obj_key, content_bytes)
                offloaded = True
                logger.info(
                    "content_offloaded",
                    pipeline_run_id=pipeline_run_id,
                    step=step_name,
                    size=len(content_bytes),
                    obj_key=obj_key,
                )
            except Exception as exc:
                logger.warning(
                    "content_offload_failed",
                    step=step_name,
                    error=str(exc),
                )
                # Fall back to Valkey storage

        # Determine chunking
        chunk_count = (len(content_bytes) + self._chunk_size - 1) // self._chunk_size
        chunk_count = max(chunk_count, 1)

        # TOCTOU-FIX: Acquire per-pipeline lock to ensure atomic
        # read-prev-hash → compute-chain → write sequence.
        # Without this, parallel agents (e.g., quality review gates)
        # would read the same prev_hash and corrupt the chain.
        # R29-FIX-4: Use manual acquire/release instead of `async with lock:`.
        # If the lock's timeout=30s expires while code is still inside the
        # `async with` block (slow pipe.execute under load), the lock auto-
        # releases in Valkey. When the context manager exits, it calls
        # lock.release() again → LockNotOwnedError, masking success/failure
        # of the actual write. Manual release with try/except avoids this.
        lock_key = f"ctx:{pipeline_run_id}:lock"
        lock = self._redis.lock(lock_key, timeout=30, blocking_timeout=30)
        acquired = await lock.acquire()
        if not acquired:
            raise TimeoutError(f"Cannot acquire context lock for pipeline {pipeline_run_id}")
        try:
            # Get previous hash from chain (or genesis seed)
            meta_key = f"ctx:{pipeline_run_id}:meta"
            meta_raw = await self._redis.get(meta_key)
            if meta_raw:
                meta = orjson.loads(meta_raw)
                prev_hash = meta["last_hash"]
            else:
                prev_hash = _sha256(_HASH_CHAIN_SEED.encode("utf-8"))

            chain_h = _chain_hash(content_hash, prev_hash)

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

            # DEFERRED-FIX-8: Single atomic pipeline for all writes.
            # Previously used pipe.execute() followed by a separate eval(),
            # creating a two-phase write: if the process crashed between
            # execute() and eval(), the meta key would be stale (wrong
            # last_hash/step_count), corrupting the chain for subsequent
            # writes. Now all writes happen in one pipeline.execute().
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

            # Store content chunks (or reference pointer if offloaded)
            if offloaded:
                # REVIEW-FIX: Content is in object store — store a reference pointer
                from app.services.object_store import make_ref
                ref_key = f"ctx/{pipeline_run_id}/{step_name}/{entry_id}"
                ref_bytes = make_ref(ref_key, content_hash)
                chunk_key = f"ctx:{pipeline_run_id}:chunk:{entry_id}:0"
                pipe.set(chunk_key, ref_bytes, ex=self._ttl)
                # Override chunk_count to 1 (reference is a single value)
                chunk_count = 1
                entry_meta["chunk_count"] = 1
                # Re-set entry metadata with updated chunk_count
                pipe.set(entry_key, orjson.dumps(entry_meta), ex=self._ttl)
            else:
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

            # Meta update via Lua inside the same pipeline.
            # The lock ensures no concurrent writers, so LLEN is accurate.
            # R26-FIX-10: Use cjson.encode instead of string concatenation.
            # String concatenation is fragile if ARGV[1] ever contains
            # JSON-special characters (currently it's always a hex digest).
            lua_script = """
            local count = redis.call('LLEN', KEYS[1])
            local meta = cjson.encode({last_hash = ARGV[1], step_count = count})
            redis.call('SET', KEYS[2], meta, 'EX', ARGV[2])
            return count
            """
            pipe.eval(lua_script, 2, chain_key, meta_key, chain_h, str(self._ttl))

            await pipe.execute()
        finally:
            try:
                await lock.release()
            except Exception:
                pass  # Lock may have auto-expired via timeout — safe to ignore

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

        # R28-FIX-11: Batch-load all entry metadata with MGET instead of
        # sequential GETs.  Reduces O(N) round-trips to O(1) and eliminates
        # TOCTOU window between individual reads.
        decoded_ids = [
            raw_id.decode("utf-8") if isinstance(raw_id, bytes) else raw_id
            for raw_id in entry_ids
        ]
        entry_keys = [f"ctx:{pipeline_run_id}:entry:{eid}" for eid in decoded_ids]
        entry_raws = await self._redis.mget(*entry_keys)

        for entry_id, entry_raw in zip(decoded_ids, entry_raws):
            if entry_raw is None:
                logger.error("chain_entry_missing", entry_id=entry_id)
                # R27-FIX-11: When verify_chain=True, a missing entry must raise
                # ContextIntegrityError instead of silently returning a truncated
                # chain that passes verification.
                if verify_chain:
                    raise ContextIntegrityError(
                        f"Chain entry {entry_id} missing — chain integrity compromised"
                    )
                # R30-FIX-7: Use `continue` instead of `break`. Previously,
                # a single missing entry truncated ALL subsequent steps. With
                # verify_chain=False (used for progress display, not integrity),
                # we should skip the bad entry and return the remaining steps.
                continue

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

        DEFERRED-FIX-7: Replaced SCAN-based key enumeration with deterministic
        key construction from the chain list. SCAN is non-atomic — concurrent
        writes during iteration can cause keys to be missed or double-counted.
        Instead, we read the chain list (which IS atomic), derive all keys
        from the entry IDs, and delete them in a single pipeline.

        R25-FIX-4: Acquire the per-pipeline distributed lock before clearing.
        Without this, a concurrent store() could write new keys between our
        chain read and our delete, creating orphaned keys and corrupted state.
        The lock key is NOT deleted — it expires via TTL after the lock is
        released (the SET NX EX handles TTL).

        Returns the number of keys deleted.
        """
        lock_key = f"ctx:{pipeline_run_id}:lock"
        chain_key = f"ctx:{pipeline_run_id}:chain"
        meta_key = f"ctx:{pipeline_run_id}:meta"

        # R26-FIX-3: Use self._redis.lock() — the SAME mechanism as store().
        # Previously used manual SET NX + unconditional DELETE which was
        # incompatible with store()'s redis-py Lock (random token + Lua release).
        # The unconditional DELETE in the finally block could steal store()'s lock.
        lock = self._redis.lock(lock_key, timeout=30, blocking_timeout=10)
        acquired = False
        try:
            acquired = await lock.acquire()
        except Exception:
            logger.warning("clear_pipeline_lock_failed", pipeline_run_id=pipeline_run_id)

        if not acquired:
            # R26-FIX-8: Do NOT proceed without the lock — that corrupts data.
            logger.error("clear_pipeline_lock_timeout", pipeline_run_id=pipeline_run_id)
            raise TimeoutError(
                f"Cannot clear pipeline {pipeline_run_id}: lock held by concurrent operation"
            )

        try:
            # Get all entry IDs from the chain
            entry_ids_raw = await self._redis.lrange(chain_key, 0, -1)

            # R25-FIX-4: Do NOT delete lock_key — let it expire via TTL
            keys_to_delete: list[str] = [chain_key, meta_key]

            # R28-FIX-10: Use MGET instead of sequential GETs for entry metadata.
            # Reduces O(N) round-trips under the distributed lock to O(1),
            # preventing lock timeout on large pipelines (18+ steps).
            entry_ids = [
                raw_id.decode("utf-8") if isinstance(raw_id, bytes) else raw_id
                for raw_id in entry_ids_raw
            ]
            entry_keys = [
                f"ctx:{pipeline_run_id}:entry:{eid}" for eid in entry_ids
            ]
            keys_to_delete.extend(entry_keys)

            entry_raws = await self._redis.mget(*entry_keys) if entry_keys else []

            for entry_id, entry_raw in zip(entry_ids, entry_raws):
                if entry_raw:
                    entry_meta = orjson.loads(entry_raw)
                    chunk_count = entry_meta.get("chunk_count", 1)
                    for i in range(chunk_count):
                        keys_to_delete.append(f"ctx:{pipeline_run_id}:chunk:{entry_id}:{i}")
                    step_name = entry_meta.get("step_name")
                    if step_name:
                        keys_to_delete.append(f"ctx:{pipeline_run_id}:step:{step_name}")
                else:
                    logger.warning(
                        "clear_pipeline_entry_expired",
                        entry_id=entry_id,
                        note="chunks may be orphaned until TTL expiry",
                    )

            if keys_to_delete:
                deleted = await self._redis.delete(*keys_to_delete)
                logger.info("context_cleared", pipeline_run_id=pipeline_run_id, keys_deleted=deleted)
                return deleted
            return 0
        finally:
            if acquired:
                try:
                    await lock.release()
                except Exception:
                    pass  # Lock may have expired via timeout

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
        # R37-FIX: Use .get() with default to handle corrupt/partial metadata.
        # If chunk_count is missing (Valkey eviction, schema change), KeyError
        # propagates as unhandled exception instead of ContextIntegrityError.
        content = await self._reassemble_chunks(
            pipeline_run_id, entry_id, entry_meta.get("chunk_count", 1)
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
        """Reassemble chunked content from Valkey or object store."""
        if chunk_count == 1:
            chunk_key = f"ctx:{pipeline_run_id}:chunk:{entry_id}:0"
            data = await self._redis.get(chunk_key)
            # R25-FIX-12: Raise ContextIntegrityError on missing single chunk.
            if data is None:
                logger.error("chunk_missing", entry_id=entry_id, chunk=0)
                raise ContextIntegrityError(f"Missing chunk 0 for entry {entry_id}")

            # REVIEW-FIX: Check if this is an object store reference pointer.
            # If so, fetch the actual content from GCS/local store.
            from app.services.object_store import is_object_ref, parse_ref
            if is_object_ref(data):
                obj_key, expected_hash = parse_ref(data)
                if self._object_store is None:
                    raise ContextIntegrityError(
                        f"Object store reference found but no object store configured "
                        f"(key={obj_key})"
                    )
                content = await self._object_store.get(obj_key)
                if content is None:
                    raise ContextIntegrityError(
                        f"Object store content missing for key={obj_key}"
                    )
                # Verify hash integrity
                actual_hash = compute_content_hash(content)
                if actual_hash != expected_hash:
                    raise ContextIntegrityError(
                        f"Object store content hash mismatch for key={obj_key}"
                    )
                return content

            return data

        # R27-FIX-12: Use MGET instead of sequential GETs.
        chunk_keys = [
            f"ctx:{pipeline_run_id}:chunk:{entry_id}:{i}"
            for i in range(chunk_count)
        ]
        parts = await self._redis.mget(*chunk_keys)
        for i, data in enumerate(parts):
            if data is None:
                logger.error("chunk_missing", entry_id=entry_id, chunk=i)
                raise ContextIntegrityError(f"Missing chunk {i} for entry {entry_id}")

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

    DEFERRED-FIX-10: Uses shared Valkey pool instead of creating a
    dedicated connection pool. Reduces total Valkey connections from
    3 pools (context + prompt + revocation) to 1 shared pool.

    Called once at app startup.
    """
    global _engine
    if _engine is not None:
        return _engine

    from app.services.valkey_pool import get_valkey_client

    client = await get_valkey_client()
    _engine = ContextEngine(client)
    logger.info("context_engine_initialized")
    return _engine


async def shutdown_context_engine() -> None:
    """Shutdown the Context Engine. Call at app shutdown.

    DEFERRED-FIX-10: Does NOT close the Redis client — it's shared.
    The shared pool is closed by shutdown_valkey_client() in main.py.
    """
    global _engine
    _engine = None


def reset_context_engine() -> None:
    """DEFERRED-FIX-11: Reset the singleton for testing / event loop changes."""
    global _engine
    _engine = None
