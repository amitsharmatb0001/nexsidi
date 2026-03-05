"""Object store abstraction for large generated code files.

REVIEW-FIX: Generated code should NOT live in Valkey/Redis (volatile cache).
Large multi-MB codebases stored in Valkey with TTL-based eviction can be lost
under memory pressure. This module provides durable storage alternatives.

Two implementations:
- GCSObjectStore: Google Cloud Storage (production)
- LocalFileStore: Local filesystem (development fallback)

The ContextEngine uses this for content >64KB — storing a reference pointer
in Valkey and the actual content in the object store.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import structlog

logger = structlog.get_logger(__name__)

# Reference format stored in Valkey when content is offloaded
OBJECT_REF_PREFIX = "__objstore_ref__:"


def is_object_ref(value: bytes) -> bool:
    """Check if a Valkey value is an object store reference pointer."""
    try:
        return value.startswith(OBJECT_REF_PREFIX.encode("utf-8"))
    except (AttributeError, TypeError):
        return False


def make_ref(key: str, content_hash: str) -> bytes:
    """Create a reference pointer for storage in Valkey."""
    return f"{OBJECT_REF_PREFIX}{key}|{content_hash}".encode("utf-8")


def parse_ref(ref_bytes: bytes) -> tuple[str, str]:
    """Parse a reference pointer back to (key, content_hash)."""
    ref_str = ref_bytes.decode("utf-8")
    if not ref_str.startswith(OBJECT_REF_PREFIX):
        raise ValueError(f"Not an object store reference: {ref_str[:50]}")
    payload = ref_str[len(OBJECT_REF_PREFIX):]
    parts = payload.split("|", 1)
    if len(parts) != 2:
        raise ValueError(f"Malformed object store reference: {ref_str[:50]}")
    return parts[0], parts[1]


@runtime_checkable
class ObjectStore(Protocol):
    """Protocol for object storage backends."""

    async def put(self, key: str, data: bytes) -> None:
        """Store data under the given key."""
        ...

    async def get(self, key: str) -> bytes | None:
        """Retrieve data by key. Returns None if not found."""
        ...

    async def delete(self, key: str) -> bool:
        """Delete data by key. Returns True if deleted, False if not found."""
        ...

    async def exists(self, key: str) -> bool:
        """Check if a key exists."""
        ...


class GCSObjectStore:
    """Google Cloud Storage backend for generated code.

    Uses the google-cloud-storage async client for production deployments.
    Files are stored in a configurable bucket with the key as the blob name.
    """

    def __init__(self, bucket_name: str, project_id: str = "") -> None:
        self._bucket_name = bucket_name
        self._project_id = project_id
        self._client: Any = None

    def _get_client(self) -> Any:
        """Lazy-init the GCS client."""
        if self._client is None:
            try:
                from google.cloud import storage
                self._client = storage.Client(project=self._project_id or None)
            except ImportError:
                raise ImportError(
                    "google-cloud-storage is required for GCS object store. "
                    "Install with: pip install google-cloud-storage"
                )
        return self._client

    async def put(self, key: str, data: bytes) -> None:
        """Upload data to GCS."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._put_sync, key, data)

    def _put_sync(self, key: str, data: bytes) -> None:
        client = self._get_client()
        bucket = client.bucket(self._bucket_name)
        blob = bucket.blob(key)
        blob.upload_from_string(data, content_type="application/octet-stream")

    async def get(self, key: str) -> bytes | None:
        """Download data from GCS."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._get_sync, key)

    def _get_sync(self, key: str) -> bytes | None:
        client = self._get_client()
        bucket = client.bucket(self._bucket_name)
        blob = bucket.blob(key)
        if not blob.exists():
            return None
        return blob.download_as_bytes()

    async def delete(self, key: str) -> bool:
        """Delete a blob from GCS."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._delete_sync, key)

    def _delete_sync(self, key: str) -> bool:
        client = self._get_client()
        bucket = client.bucket(self._bucket_name)
        blob = bucket.blob(key)
        if not blob.exists():
            return False
        blob.delete()
        return True

    async def exists(self, key: str) -> bool:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._exists_sync, key)

    def _exists_sync(self, key: str) -> bool:
        client = self._get_client()
        bucket = client.bucket(self._bucket_name)
        return bucket.blob(key).exists()


class LocalFileStore:
    """Local filesystem backend for development.

    Stores files under a configurable directory with the key as subdirectory
    path. Useful for local development without GCS credentials.
    """

    def __init__(self, base_dir: str = "") -> None:
        if not base_dir:
            base_dir = os.path.join(os.getcwd(), ".nexsidi_store")
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _key_to_path(self, key: str) -> Path:
        """Convert object key to filesystem path (sanitize to prevent traversal)."""
        # SHA-256 the key to get a safe filesystem path
        safe_name = hashlib.sha256(key.encode("utf-8")).hexdigest()
        # Use first 2 chars as directory for sharding
        return self._base_dir / safe_name[:2] / safe_name

    async def put(self, key: str, data: bytes) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._put_sync, key, data)

    def _put_sync(self, key: str, data: bytes) -> None:
        path = self._key_to_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    async def get(self, key: str) -> bytes | None:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._get_sync, key)

    def _get_sync(self, key: str) -> bytes | None:
        path = self._key_to_path(key)
        if not path.exists():
            return None
        return path.read_bytes()

    async def delete(self, key: str) -> bool:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._delete_sync, key)

    def _delete_sync(self, key: str) -> bool:
        path = self._key_to_path(key)
        if not path.exists():
            return False
        path.unlink()
        return True

    async def exists(self, key: str) -> bool:
        path = self._key_to_path(key)
        return path.exists()


# ── Singleton ────────────────────────────────────────────────────────

_object_store: ObjectStore | None = None


def get_object_store() -> ObjectStore | None:
    """Get the configured object store instance.

    Returns None if no object store is configured (Valkey-only dev mode).
    """
    global _object_store
    if _object_store is not None:
        return _object_store

    try:
        from app.config import get_settings
        settings = get_settings()
    except Exception:
        return None

    # Check for GCS config
    gcs_bucket = getattr(settings, "gcs_code_bucket", "")
    if gcs_bucket:
        project_id = settings.gcp_project_id or ""
        _object_store = GCSObjectStore(bucket_name=gcs_bucket, project_id=project_id)
        logger.info("object_store_initialized", backend="gcs", bucket=gcs_bucket)
        return _object_store

    # Check for local store config
    local_store_dir = getattr(settings, "local_store_dir", "")
    if local_store_dir:
        _object_store = LocalFileStore(base_dir=local_store_dir)
        logger.info("object_store_initialized", backend="local", dir=local_store_dir)
        return _object_store

    # No object store configured — Valkey-only mode
    return None
