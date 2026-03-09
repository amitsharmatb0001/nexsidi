"""Agent Permanent Memory — Per-agent knowledge that persists across projects.

Each agent builds a personal knowledge base of patterns, preferences, and
best practices learned over time. Shubham learns FastAPI patterns, Aanya
learns React patterns, Karan learns security anti-patterns, etc.

Storage: ChromaDB collections (one per agent per category).
Fallback: In-memory dict if ChromaDB unavailable.

Integration:
- Agents call store_knowledge() when they discover useful patterns
- Agents call recall_knowledge() before generating code to use past learnings
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_chroma_client = None
_FALLBACK_STORE: dict[str, list[dict[str, Any]]] = {}


def _get_chroma():
    """Lazy-initialize ChromaDB client (shared with mistake_memory).

    BLOCKER-2 FIX: Uses PersistentClient in production/staging so agent
    knowledge survives process restarts.  Ephemeral Client only in dev.
    """
    global _chroma_client
    if _chroma_client is not None:
        return _chroma_client
    try:
        import chromadb

        from app.config import get_settings
        settings = get_settings()

        if settings.environment in ("production", "staging"):
            import os
            persist_dir = os.environ.get(
                "CHROMADB_PERSIST_DIR",
                "/data/chromadb",
            )
            os.makedirs(persist_dir, exist_ok=True)
            _chroma_client = chromadb.PersistentClient(path=persist_dir)
            logger.info(
                "chromadb_persistent_client_initialized",
                persist_dir=persist_dir,
                environment=settings.environment,
            )
        else:
            _chroma_client = chromadb.Client()
            logger.info("chromadb_ephemeral_client_initialized")

        return _chroma_client
    except (ImportError, Exception) as exc:
        logger.warning("chromadb_init_failed", error=str(exc)[:200])
        return None


class AgentMemory:
    """Per-agent persistent knowledge base.

    Usage:
        from app.services.agent_memory import agent_memory

        # Store a learned pattern
        agent_memory.store_knowledge(
            agent_name="shubham",
            topic="FastAPI dependency injection",
            content="Use Depends() for auth middleware, never manual token parsing",
            metadata={"framework": "fastapi", "category": "best_practice"},
        )

        # Recall relevant knowledge
        results = agent_memory.recall_knowledge("shubham", "authentication middleware")
    """

    def store_knowledge(
        self,
        agent_name: str,
        topic: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Store a learned pattern/preference."""
        doc_id = hashlib.md5(
            f"{agent_name}:knowledge:{topic}".encode()
        ).hexdigest()

        doc_text = f"Topic: {topic}\n{content}"
        meta = {
            "agent_name": agent_name,
            "topic": topic,
            "timestamp": time.time(),
            "category": "knowledge",
        }
        if metadata:
            for k, v in metadata.items():
                if isinstance(v, (str, int, float, bool)):
                    meta[k] = v

        chroma = _get_chroma()
        if chroma:
            try:
                col_name = f"knowledge_{agent_name}"[:63]
                collection = chroma.get_or_create_collection(col_name)
                collection.upsert(
                    ids=[doc_id],
                    documents=[doc_text],
                    metadatas=[meta],
                )
                return
            except Exception as exc:
                logger.warning("agent_memory_store_failed", error=str(exc)[:200])

        # Fallback
        key = f"knowledge_{agent_name}"
        if key not in _FALLBACK_STORE:
            _FALLBACK_STORE[key] = []
        _FALLBACK_STORE[key].append(meta | {"content": content})
        if len(_FALLBACK_STORE[key]) > 200:
            _FALLBACK_STORE[key] = _FALLBACK_STORE[key][-200:]

    def recall_knowledge(
        self,
        agent_name: str,
        query: str,
        n_results: int = 5,
    ) -> list[dict[str, Any]]:
        """Vector search for relevant knowledge."""
        chroma = _get_chroma()
        if chroma:
            try:
                col_name = f"knowledge_{agent_name}"[:63]
                collection = chroma.get_or_create_collection(col_name)
                results = collection.query(
                    query_texts=[query[:500]],
                    n_results=n_results,
                )
                if results and results.get("metadatas"):
                    items = []
                    for meta_list in results["metadatas"]:
                        if isinstance(meta_list, list):
                            items.extend(meta_list)
                        else:
                            items.append(meta_list)
                    return items[:n_results]
            except Exception as exc:
                logger.warning("agent_memory_recall_failed", error=str(exc)[:200])

        # Fallback: return most recent entries
        key = f"knowledge_{agent_name}"
        return _FALLBACK_STORE.get(key, [])[-n_results:]

    def get_agent_stats(self, agent_name: str) -> dict[str, int]:
        """Get stats for an agent's knowledge base."""
        chroma = _get_chroma()
        knowledge_count = 0
        if chroma:
            try:
                col_name = f"knowledge_{agent_name}"[:63]
                collection = chroma.get_or_create_collection(col_name)
                knowledge_count = collection.count()
            except Exception as exc:
                # AUDIT-T3-8: Log instead of silently swallowing
                logger.warning("agent_memory_count_failed", agent=agent_name, error=str(exc)[:200])

        if not knowledge_count:
            key = f"knowledge_{agent_name}"
            knowledge_count = len(_FALLBACK_STORE.get(key, []))

        return {"knowledge_entries": knowledge_count}


# Module-level singleton
agent_memory = AgentMemory()


def get_agent_memory() -> AgentMemory:
    """Get the module-level AgentMemory singleton."""
    return agent_memory
