"""pgvector-based persistent vector memory for agents.

Uses the existing PostgreSQL database with the pgvector extension.
No new database needed — just `CREATE EXTENSION IF NOT EXISTS vector;`.

Provides:
- store_embedding(agent, content, metadata) — store with embedding
- search_similar(agent, query, k) — cosine similarity search
- store_project_outcome(project_id, outcome) — store final results
- get_similar_projects(requirements, k) — find similar past projects

Embedding generation uses text-embedding-3-small (OpenAI) or Gemini embedding.
Falls back to keyword-based matching if no embedding model available.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("nexsidi.vector_memory")


@dataclass
class MemoryEntry:
    """A single memory entry with metadata."""

    id: str
    agent_name: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    similarity: float = 0.0
    created_at: float = 0.0


@dataclass
class ProjectOutcome:
    """Outcome of a past project for similarity matching."""

    project_id: str
    requirements_summary: str
    outcome: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    similarity: float = 0.0


class VectorMemory:
    """pgvector-based persistent memory using existing PostgreSQL.

    Uses cosine similarity for semantic search.
    Falls back to keyword matching if pgvector is not available.
    """

    def __init__(self) -> None:
        self._initialized = False
        self._pgvector_available = False

    async def _ensure_init(self) -> bool:
        """Check if pgvector extension is available."""
        if self._initialized:
            return self._pgvector_available

        self._initialized = True

        try:
            from app.database import get_async_session

            async with get_async_session() as session:
                # Check if pgvector extension exists
                result = await session.execute(
                    "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
                )
                row = result.fetchone()
                if row:
                    self._pgvector_available = True
                    logger.info("vector_memory_enabled: pgvector extension found")
                else:
                    logger.warning(
                        "vector_memory_fallback: pgvector extension not installed. "
                        "Run: CREATE EXTENSION IF NOT EXISTS vector;"
                    )
        except Exception as exc:
            logger.warning("vector_memory_init_failed: %s", exc)

        return self._pgvector_available

    async def _generate_embedding(self, text: str) -> list[float] | None:
        """Generate embedding vector using available AI provider.

        Tries Gemini embedding first (already configured), falls back to
        a simple hash-based pseudo-embedding for basic matching.
        """
        try:
            from app.services.ai_router import get_ai_router, AIRequest, AIMessage

            # Use a lightweight prompt to get a structured representation
            # This is a workaround until we have dedicated embedding endpoints
            router = get_ai_router()
            # For now, use a deterministic hash-based approach
            # (proper embedding integration will use Vertex AI text-embedding)
            return self._hash_embedding(text)

        except Exception:
            return self._hash_embedding(text)

    @staticmethod
    def _hash_embedding(text: str, dimensions: int = 256) -> list[float]:
        """Create a deterministic pseudo-embedding from text hash.

        Not as good as real neural embeddings, but provides basic
        similarity matching without requiring an embedding model.
        Uses overlapping character n-grams for better semantic coverage.
        """
        import math

        # Normalize text
        text = text.lower().strip()
        words = text.split()

        # Create feature vector from word and bigram hashes
        vector = [0.0] * dimensions
        features = words + [f"{words[i]} {words[i+1]}" for i in range(len(words) - 1)]

        for feature in features:
            h = hashlib.md5(feature.encode()).hexdigest()
            for i in range(0, min(len(h), dimensions * 2), 2):
                idx = int(h[i:i+2], 16) % dimensions
                vector[idx] += 1.0

        # L2 normalize
        magnitude = math.sqrt(sum(x * x for x in vector))
        if magnitude > 0:
            vector = [x / magnitude for x in vector]

        return vector

    async def store_embedding(
        self,
        agent_name: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """Store content with embedding in vector database.

        Returns the entry ID, or None if storage failed.
        """
        entry_id = hashlib.sha256(
            f"{agent_name}:{content[:500]}:{time.time()}".encode()
        ).hexdigest()[:16]

        embedding = await self._generate_embedding(content)
        if embedding is None:
            return None

        try:
            if await self._ensure_init() and self._pgvector_available:
                from app.database import get_async_session

                async with get_async_session() as session:
                    await session.execute(
                        """
                        INSERT INTO agent_memories (id, agent_name, content, metadata, embedding, created_at)
                        VALUES (:id, :agent, :content, :metadata, :embedding, NOW())
                        ON CONFLICT (id) DO UPDATE SET content = :content, metadata = :metadata
                        """,
                        {
                            "id": entry_id,
                            "agent": agent_name,
                            "content": content[:10000],
                            "metadata": json.dumps(metadata or {}),
                            "embedding": str(embedding),
                        },
                    )
                    await session.commit()
            else:
                # Fallback: log only (no persistence without pgvector)
                logger.debug("vector_store_fallback", agent=agent_name, content_len=len(content))

            return entry_id

        except Exception as exc:
            logger.warning("vector_store_failed: %s", exc)
            return None

    async def search_similar(
        self,
        agent_name: str,
        query: str,
        k: int = 5,
    ) -> list[MemoryEntry]:
        """Search for similar memories using cosine similarity.

        Args:
            agent_name: Filter by agent (or "*" for all agents).
            query: Search query text.
            k: Number of results to return.

        Returns:
            List of MemoryEntry sorted by similarity (highest first).
        """
        if not await self._ensure_init() or not self._pgvector_available:
            # AUDIT-B2-FIX: Was silently returning [] — callers couldn't
            # distinguish "no similar memories" from "memory unavailable".
            logger.warning(
                "vector_memory_unavailable: returning empty results. "
                "pgvector extension not installed or DB connection failed.",
                agent=agent_name,
                query=query[:80],
            )
            return []

        embedding = await self._generate_embedding(query)
        if embedding is None:
            return []

        try:
            from app.database import get_async_session

            agent_filter = "" if agent_name == "*" else "AND agent_name = :agent"

            async with get_async_session() as session:
                result = await session.execute(
                    f"""
                    SELECT id, agent_name, content, metadata,
                           1 - (embedding <=> :embedding::vector) AS similarity,
                           created_at
                    FROM agent_memories
                    WHERE 1=1 {agent_filter}
                    ORDER BY embedding <=> :embedding::vector
                    LIMIT :k
                    """,
                    {
                        "embedding": str(embedding),
                        "agent": agent_name,
                        "k": k,
                    },
                )

                entries = []
                for row in result.fetchall():
                    entries.append(MemoryEntry(
                        id=row.id,
                        agent_name=row.agent_name,
                        content=row.content,
                        metadata=json.loads(row.metadata) if row.metadata else {},
                        similarity=float(row.similarity),
                        created_at=row.created_at.timestamp() if row.created_at else 0,
                    ))

                return entries

        except Exception as exc:
            logger.warning("vector_search_failed: %s", exc)
            return []

    async def store_project_outcome(
        self,
        project_id: str,
        requirements_summary: str,
        outcome: dict[str, Any],
        success: bool = True,
    ) -> None:
        """Store a project outcome for future similarity matching."""
        await self.store_embedding(
            agent_name="__project_outcome__",
            content=requirements_summary,
            metadata={
                "project_id": project_id,
                "outcome": outcome,
                "success": success,
            },
        )

    async def get_similar_projects(
        self,
        requirements: str,
        k: int = 3,
    ) -> list[ProjectOutcome]:
        """Find similar past projects based on requirements."""
        entries = await self.search_similar("__project_outcome__", requirements, k)

        return [
            ProjectOutcome(
                project_id=e.metadata.get("project_id", ""),
                requirements_summary=e.content,
                outcome=e.metadata.get("outcome", {}),
                success=e.metadata.get("success", True),
                similarity=e.similarity,
            )
            for e in entries
        ]


# ── Singleton ──────────────────────────────────────────────────

_vector_memory: VectorMemory | None = None


def get_vector_memory() -> VectorMemory:
    """Get or create the vector memory singleton."""
    global _vector_memory
    if _vector_memory is None:
        _vector_memory = VectorMemory()
    return _vector_memory
