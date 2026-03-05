"""Virtual File System: Valkey-backed code storage for pipeline runs.

D2-FIX: Stores generated code files outside the pipeline context dict.
The context only holds a lightweight manifest (path -> hash + size + agent).
Agents access file contents via VFS.read_file() and VFS.search() instead
of receiving megabytes of file_contents inline in the context dict.

Key structure in Valkey:
    vfs:{run_id}:file:{path}  -> file content (UTF-8 bytes)
    vfs:{run_id}:manifest     -> JSON manifest {path: {hash, size, agent}}

Usage:
    vfs = await init_vfs()           # At startup
    vfs = get_vfs()                  # Anywhere after init
    manifest = await vfs.store_files(run_id, files, agent_name)
    content = await vfs.read_file(run_id, path)
    matches = await vfs.search(run_id, pattern)
"""

from __future__ import annotations

import fnmatch
import hashlib
import re
from typing import Any

import orjson
import structlog

logger = structlog.get_logger(__name__)

# TTL for VFS entries: 7 days (matches context_engine)
_DEFAULT_TTL_SECONDS = 7 * 24 * 60 * 60


class VirtualFileSystem:
    """Valkey-backed storage for generated code files.

    Stores file contents keyed by (run_id, path).  The manifest is a
    lightweight JSON index with path -> {hash, size, agent} for each file.
    """

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client
        self._ttl = _DEFAULT_TTL_SECONDS

    # ── Write Operations ────────────────────────────────────────────

    async def store_file(
        self,
        run_id: str,
        path: str,
        content: str,
        agent: str,
    ) -> dict[str, Any]:
        """Store a single file in VFS and update the manifest.

        Returns the manifest entry for this file.
        """
        key = f"vfs:{run_id}:file:{path}"
        await self._redis.set(key, content.encode("utf-8"), ex=self._ttl)

        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        entry = {"hash": content_hash, "size": len(content), "agent": agent}

        # Update manifest atomically
        manifest_key = f"vfs:{run_id}:manifest"
        manifest = await self._load_manifest(run_id)
        manifest[path] = entry
        await self._redis.set(
            manifest_key, orjson.dumps(manifest), ex=self._ttl,
        )
        return entry

    async def store_files(
        self,
        run_id: str,
        files: dict[str, str],
        agent: str,
    ) -> dict[str, dict[str, Any]]:
        """Bulk store files in VFS.  Returns the complete manifest dict.

        Uses a Redis pipeline for efficiency (single round-trip for all files).
        """
        if not files:
            return {}

        manifest = await self._load_manifest(run_id)
        pipe = self._redis.pipeline()

        for path, content in files.items():
            key = f"vfs:{run_id}:file:{path}"
            pipe.set(key, content.encode("utf-8"), ex=self._ttl)
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
            manifest[path] = {
                "hash": content_hash,
                "size": len(content),
                "agent": agent,
            }

        # Store updated manifest
        manifest_key = f"vfs:{run_id}:manifest"
        pipe.set(manifest_key, orjson.dumps(manifest), ex=self._ttl)

        await pipe.execute()
        logger.debug(
            "vfs_files_stored",
            run_id=run_id,
            file_count=len(files),
            agent=agent,
        )
        return manifest

    # ── Read Operations ─────────────────────────────────────────────

    async def read_file(self, run_id: str, path: str) -> str | None:
        """Read a file from VFS.  Returns None if not found."""
        key = f"vfs:{run_id}:file:{path}"
        data = await self._redis.get(key)
        if data is None:
            return None
        return data.decode("utf-8") if isinstance(data, bytes) else data

    async def get_manifest(self, run_id: str) -> dict[str, dict[str, Any]]:
        """Get the file manifest (paths + metadata, no content)."""
        return await self._load_manifest(run_id)

    async def list_files(self, run_id: str) -> list[str]:
        """List all file paths stored for a pipeline run."""
        manifest = await self._load_manifest(run_id)
        return list(manifest.keys())

    # ── Search ──────────────────────────────────────────────────────

    async def search(
        self,
        run_id: str,
        pattern: str,
        file_glob: str = "",
        max_matches: int = 50,
    ) -> list[dict[str, Any]]:
        """Search across all files in VFS for a text/regex pattern.

        Args:
            run_id: Pipeline run ID.
            pattern: Regex pattern to search for (case-insensitive).
            file_glob: Optional glob to filter files (e.g., ``"*.py"``).
            max_matches: Maximum number of matches to return.

        Returns:
            List of ``{path, line, text}`` dicts for each match.
        """
        manifest = await self._load_manifest(run_id)
        if not manifest:
            return []

        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error:
            regex = re.compile(re.escape(pattern), re.IGNORECASE)

        matches: list[dict[str, Any]] = []

        for path in manifest:
            if file_glob and not fnmatch.fnmatch(path, file_glob):
                continue

            content = await self.read_file(run_id, path)
            if content is None:
                continue

            for line_num, line in enumerate(content.split("\n"), 1):
                if regex.search(line):
                    matches.append({
                        "path": path,
                        "line": line_num,
                        "text": line.strip()[:120],
                    })
                    if len(matches) >= max_matches:
                        return matches

        return matches

    # ── Cleanup ─────────────────────────────────────────────────────

    async def cleanup(self, run_id: str) -> int:
        """Delete all VFS data for a pipeline run.  Returns count of deleted keys."""
        manifest = await self._load_manifest(run_id)
        if not manifest:
            return 0

        pipe = self._redis.pipeline()
        count = 0
        for path in manifest:
            pipe.delete(f"vfs:{run_id}:file:{path}")
            count += 1
        pipe.delete(f"vfs:{run_id}:manifest")
        count += 1

        await pipe.execute()
        logger.debug("vfs_cleanup", run_id=run_id, keys_deleted=count)
        return count

    # ── Internal ────────────────────────────────────────────────────

    async def _load_manifest(self, run_id: str) -> dict[str, dict[str, Any]]:
        """Load the manifest from Valkey, or return empty dict."""
        manifest_key = f"vfs:{run_id}:manifest"
        raw = await self._redis.get(manifest_key)
        if raw is None:
            return {}
        try:
            return orjson.loads(raw)
        except (orjson.JSONDecodeError, Exception):
            return {}


# ── Factory ──────────────────────────────────────────────────────────

_vfs: VirtualFileSystem | None = None


def get_vfs() -> VirtualFileSystem:
    """Get the VFS singleton.  Raises RuntimeError if not initialized."""
    global _vfs
    if _vfs is None:
        raise RuntimeError("VFS not initialized. Call init_vfs() first.")
    return _vfs


async def init_vfs() -> VirtualFileSystem:
    """Initialize the VFS with the shared Valkey connection.

    Called once at app startup (alongside init_context_engine).
    """
    global _vfs
    if _vfs is not None:
        return _vfs

    from app.services.valkey_pool import get_valkey_client

    client = await get_valkey_client()
    _vfs = VirtualFileSystem(client)
    logger.info("vfs_initialized")
    return _vfs


async def shutdown_vfs() -> None:
    """Shutdown the VFS.  Does NOT close the Redis client (shared pool)."""
    global _vfs
    _vfs = None
