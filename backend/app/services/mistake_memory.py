"""Mistake Memory — Agents learn from past failures.

Records error patterns and their fixes so agents don't repeat the same
mistakes across projects. Uses ChromaDB for vector similarity search
to find relevant past failures for the current task.

Integration:
- Fixer: records failures after fixing errors
- Aarav: records test failures
- call_ai(): injects relevant lessons into system prompts

Storage:
- ChromaDB collection per agent (persistent vector store)
- Fallback to in-memory dict if ChromaDB unavailable
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Lazy-initialized ChromaDB client
_chroma_client = None
_FALLBACK_STORE: dict[str, list[dict[str, Any]]] = {}  # agent -> [mistakes]


def _get_chroma():
    """Lazy-initialize ChromaDB client.

    3.3-FIX: Use PersistentClient in production/staging so mistake memory
    survives restarts. Ephemeral mode is kept for dev/tests to avoid
    polluting the filesystem.
    """
    global _chroma_client
    if _chroma_client is not None:
        return _chroma_client
    try:
        import chromadb
        import os
        env = os.environ.get("ENVIRONMENT", "development").lower()
        if env in ("production", "staging"):
            persist_dir = os.environ.get("CHROMADB_PERSIST_DIR", "./data/chromadb")
            os.makedirs(persist_dir, exist_ok=True)
            _chroma_client = chromadb.PersistentClient(path=persist_dir)
            logger.info("chromadb_initialized", backend="persistent", path=persist_dir)
        else:
            _chroma_client = chromadb.Client()
            logger.info("chromadb_initialized", backend="ephemeral")
        return _chroma_client
    except ImportError:
        logger.warning("chromadb_not_installed", fallback="in-memory dict")
        return None
    except Exception as exc:
        logger.warning("chromadb_init_failed", error=str(exc)[:200], fallback="in-memory dict")
        return None


def _collection_name(agent_name: str) -> str:
    """Sanitized collection name for ChromaDB."""
    # ChromaDB collection names: 3-63 chars, alphanumeric + underscores
    name = f"mistakes_{agent_name}"
    return name[:63]


class MistakeMemory:
    """Persistent failure learning across projects.

    Usage:
        from app.services.mistake_memory import mistake_memory

        # Record a failure
        mistake_memory.record_failure(
            agent_name="shubham",
            task_type="backend_generation",
            error="ModuleNotFoundError: No module named 'app.models.user'",
            fix="Added missing __init__.py and corrected import path",
            context={"framework": "fastapi", "file": "routers/auth.py"},
        )

        # Query similar mistakes before a task
        lessons = mistake_memory.build_lessons_prompt("shubham", "backend_generation", context_str)
        # Returns prompt section or empty string
    """

    def record_failure(
        self,
        agent_name: str,
        task_type: str,
        error: str,
        fix: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Store a mistake for future reference."""
        doc_id = hashlib.md5(
            f"{agent_name}:{task_type}:{error[:200]}".encode()
        ).hexdigest()

        doc_text = f"Error: {error}\nFix: {fix}"
        metadata = {
            "agent_name": agent_name,
            "task_type": task_type,
            "error": error[:500],
            "fix": fix[:500],
            "timestamp": time.time(),
            # FIX-48: 90-day expiry — prevents unbounded ChromaDB growth
            "expiry_timestamp": time.time() + (90 * 24 * 3600),
        }
        if context:
            # Store serializable context fields
            for k, v in context.items():
                if isinstance(v, (str, int, float, bool)):
                    metadata[f"ctx_{k}"] = v

        chroma = _get_chroma()
        if chroma:
            try:
                collection = chroma.get_or_create_collection(_collection_name(agent_name))
                collection.upsert(
                    ids=[doc_id],
                    documents=[doc_text],
                    metadatas=[metadata],
                )
                # CHANGE-15: Also write to shared collection so ALL agents learn.
                # This enables cross-agent knowledge: Aanya learns from Shubham's
                # mistakes, Fixer knowledge propagates to all build agents.
                shared_coll = chroma.get_or_create_collection("mistakes_shared")
                shared_coll.upsert(
                    ids=[f"shared_{doc_id}"],
                    documents=[doc_text],
                    metadatas=[metadata],
                )
                logger.debug("mistake_recorded", agent=agent_name, task_type=task_type)
                return
            except Exception as exc:
                logger.warning("mistake_record_chromadb_failed", error=str(exc)[:200])

        # Fallback: in-memory store
        if agent_name not in _FALLBACK_STORE:
            _FALLBACK_STORE[agent_name] = []
        _FALLBACK_STORE[agent_name].append(metadata)
        # Cap at 200 entries per agent
        if len(_FALLBACK_STORE[agent_name]) > 200:
            _FALLBACK_STORE[agent_name] = _FALLBACK_STORE[agent_name][-200:]
        # CHANGE-15: Also store in shared fallback
        if "_shared" not in _FALLBACK_STORE:
            _FALLBACK_STORE["_shared"] = []
        _FALLBACK_STORE["_shared"].append(metadata)
        if len(_FALLBACK_STORE["_shared"]) > 500:
            _FALLBACK_STORE["_shared"] = _FALLBACK_STORE["_shared"][-500:]

    def record_success(
        self,
        agent_name: str,
        task_type: str,
        output_summary: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        """CHANGE-9: Store a successful outcome for positive reinforcement.

        Unlike record_failure() which stores errors, this stores what WORKED.
        build_lessons_prompt() will include both DO (successes) and AVOID (failures).
        """
        doc_id = hashlib.md5(
            f"success:{agent_name}:{task_type}:{output_summary[:200]}".encode()
        ).hexdigest()

        doc_text = f"SUCCESS: {output_summary}"
        metadata = {
            "agent_name": agent_name,
            "task_type": task_type,
            "type": "success",
            "summary": output_summary[:500],
            "timestamp": time.time(),
        }
        if context:
            for k, v in context.items():
                if isinstance(v, (str, int, float, bool)):
                    metadata[f"ctx_{k}"] = v

        chroma = _get_chroma()
        if chroma:
            try:
                coll_name = _collection_name(agent_name) + "_successes"
                collection = chroma.get_or_create_collection(coll_name)
                collection.upsert(
                    ids=[doc_id],
                    documents=[doc_text],
                    metadatas=[metadata],
                )
                logger.debug("success_recorded", agent=agent_name, task_type=task_type)
                return
            except Exception as exc:
                logger.warning("success_record_chromadb_failed", error=str(exc)[:200])

        # Fallback: in-memory
        key = f"{agent_name}_successes"
        if key not in _FALLBACK_STORE:
            _FALLBACK_STORE[key] = []
        _FALLBACK_STORE[key].append(metadata)
        if len(_FALLBACK_STORE[key]) > 100:
            _FALLBACK_STORE[key] = _FALLBACK_STORE[key][-100:]

    def _query_successes(
        self,
        agent_name: str,
        task_type: str,
        input_context: str,
        n_results: int = 3,
    ) -> list[dict[str, Any]]:
        """CHANGE-9: Query past successes for positive reinforcement."""
        chroma = _get_chroma()
        if chroma:
            try:
                coll_name = _collection_name(agent_name) + "_successes"
                collection = chroma.get_or_create_collection(coll_name)
                results = collection.query(
                    query_texts=[f"{task_type}: {input_context[:500]}"],
                    n_results=n_results,
                )
                if results and results.get("metadatas"):
                    successes = []
                    for meta_list in results["metadatas"]:
                        if isinstance(meta_list, list):
                            successes.extend(meta_list)
                        else:
                            successes.append(meta_list)
                    return successes[:n_results]
            except Exception:
                pass

        # Fallback
        key = f"{agent_name}_successes"
        entries = _FALLBACK_STORE.get(key, [])
        return [e for e in entries if e.get("task_type") == task_type][-n_results:]

    def query_similar_mistakes(
        self,
        agent_name: str,
        task_type: str,
        input_context: str,
        n_results: int = 5,
    ) -> list[dict[str, Any]]:
        """Find similar past errors via vector similarity."""
        chroma = _get_chroma()
        if chroma:
            try:
                collection = chroma.get_or_create_collection(_collection_name(agent_name))
                results = collection.query(
                    query_texts=[f"{task_type}: {input_context[:500]}"],
                    n_results=n_results,
                    where={"agent_name": agent_name},
                )
                if results and results.get("metadatas"):
                    # Flatten: results["metadatas"] is list of lists
                    mistakes = []
                    for meta_list in results["metadatas"]:
                        if isinstance(meta_list, list):
                            mistakes.extend(meta_list)
                        else:
                            mistakes.append(meta_list)
                    return mistakes[:n_results]
            except Exception as exc:
                logger.warning("mistake_query_chromadb_failed", error=str(exc)[:200])

        # Fallback: simple keyword matching
        entries = _FALLBACK_STORE.get(agent_name, [])
        matched = [
            e for e in entries
            if e.get("task_type") == task_type
        ]
        return matched[-n_results:]

    def _query_shared_mistakes(
        self,
        task_type: str,
        input_context: str,
        exclude_agent: str = "",
        n_results: int = 3,
    ) -> list[dict[str, Any]]:
        """CHANGE-15: Query shared lessons from ALL agents."""
        chroma = _get_chroma()
        if chroma:
            try:
                shared_coll = chroma.get_or_create_collection("mistakes_shared")
                results = shared_coll.query(
                    query_texts=[f"{task_type}: {input_context[:500]}"],
                    n_results=n_results,
                )
                if results and results.get("metadatas"):
                    mistakes = []
                    for meta_list in results["metadatas"]:
                        if isinstance(meta_list, list):
                            mistakes.extend(meta_list)
                        else:
                            mistakes.append(meta_list)
                    # Exclude own agent's mistakes (already queried separately)
                    if exclude_agent:
                        mistakes = [
                            m for m in mistakes
                            if m.get("agent_name") != exclude_agent
                        ]
                    return mistakes[:n_results]
            except Exception:
                pass

        # Fallback
        entries = _FALLBACK_STORE.get("_shared", [])
        matched = [
            e for e in entries
            if e.get("task_type") == task_type
            and e.get("agent_name") != exclude_agent
        ]
        return matched[-n_results:]

    def build_lessons_prompt(
        self,
        agent_name: str,
        task_type: str,
        context: str,
    ) -> str:
        """Build a prompt section with past lessons, or empty string.

        CHANGE-9: Now includes BOTH positive and negative lessons:
        - DO THIS: patterns that worked in similar projects
        - AVOID THIS: past mistakes and their fixes

        CHANGE-15: Also includes cross-agent lessons from shared collection.
        """
        mistakes = self.query_similar_mistakes(agent_name, task_type, context)
        successes = self._query_successes(agent_name, task_type, context)
        # CHANGE-15: Cross-agent shared lessons
        shared = self._query_shared_mistakes(task_type, context, exclude_agent=agent_name)

        if not mistakes and not successes and not shared:
            return ""

        lines = ["\n\n**LESSONS FROM PAST PROJECTS:**"]

        # Positive lessons (what worked)
        if successes:
            lines.append("\n**DO THIS (proven patterns):**")
            for i, success in enumerate(successes[:3], 1):
                summary = success.get("summary", "")[:200]
                lines.append(f"{i}. {summary}")

        # Negative lessons (what to avoid)
        if mistakes:
            lines.append("\n**AVOID THIS (past mistakes — DO NOT REPEAT):**")
            for i, mistake in enumerate(mistakes[:5], 1):
                error = mistake.get("error", "Unknown")[:200]
                fix = mistake.get("fix", "No fix recorded")[:200]
                lines.append(f"{i}. **Error**: {error}")
                lines.append(f"   **Fix**: {fix}")

        # CHANGE-15: Cross-agent shared lessons
        if shared:
            lines.append("\n**LESSONS FROM OTHER AGENTS:**")
            for i, lesson in enumerate(shared[:3], 1):
                src = lesson.get("agent_name", "unknown")
                error = lesson.get("error", "")[:200]
                fix = lesson.get("fix", "")[:200]
                lines.append(f"{i}. [{src}] **Error**: {error}")
                if fix:
                    lines.append(f"   **Fix**: {fix}")

        lines.append("")
        return "\n".join(lines)

    def build_validation_rules(
        self,
        agent_name: str,
        task_type: str,
        context: str,
    ) -> list:
        """PHASE-F: Convert past mistakes into HARD validation rules.

        Returns a list of callables: rule(path, content, all_files) -> str | None.
        Returns error string if rule fails, None if passes.

        Unlike build_lessons_prompt() which returns prompt text the LLM can
        ignore, these rules run inside VerificationGate and BLOCK writes that
        would repeat past mistakes.
        """
        mistakes = self.query_similar_mistakes(agent_name, task_type, context, n_results=10)
        if not mistakes:
            return []

        rules = []
        seen_patterns: set[str] = set()  # Deduplicate

        for mistake in mistakes:
            error = mistake.get("error", "").lower()
            fix = mistake.get("fix", "").lower()

            # Pattern: Missing __init__.py
            if "__init__.py" in fix and "init_py" not in seen_patterns:
                seen_patterns.add("init_py")

                def init_py_rule(path: str, content: str, all_files: dict) -> str | None:
                    """Ensure package directories have __init__.py."""
                    if not path.endswith(".py"):
                        return None
                    parts = path.split("/")
                    if len(parts) < 2:
                        return None
                    for i in range(1, len(parts)):
                        pkg_dir = "/".join(parts[:i])
                        init_path = pkg_dir + "/__init__.py"
                        if init_path not in all_files and path != init_path:
                            # Check if there are other .py files in this dir
                            has_siblings = any(
                                p.startswith(pkg_dir + "/") and p.endswith(".py") and p != path
                                for p in all_files
                            )
                            if has_siblings:
                                return f"Package '{pkg_dir}' needs __init__.py (past mistake)"
                    return None

                rules.append(init_py_rule)

            # Pattern: Markdown fences in generated code
            if ("markdown" in error or "code fence" in error) and "fence" not in seen_patterns:
                seen_patterns.add("fence")

                def fence_rule(path: str, content: str, all_files: dict) -> str | None:
                    """Strict markdown fence detection (past mistake)."""
                    import re
                    if re.search(r"^```\w*\s*$", content, re.MULTILINE):
                        return "Contains markdown code fences (repeated past mistake)"
                    return None

                rules.append(fence_rule)

            # Pattern: Import errors
            if ("import" in error and ("not found" in error or "no module" in error)
                    and "import_check" not in seen_patterns):
                seen_patterns.add("import_check")

                def import_rule(path: str, content: str, all_files: dict) -> str | None:
                    """Strict import resolution (past mistake with imports)."""
                    import re
                    if not path.endswith(".py"):
                        return None
                    imports = re.findall(r"^from\s+(app\.\S+)\s+import", content, re.MULTILINE)
                    for mod in imports:
                        mod_file = mod.replace(".", "/") + ".py"
                        mod_init = mod.replace(".", "/") + "/__init__.py"
                        if (mod_file not in all_files and mod_init not in all_files
                                and "backend/" + mod_file not in all_files):
                            return f"Import '{mod}' has no matching file (repeated past mistake)"
                    return None

                rules.append(import_rule)

            # Pattern: Syntax errors
            if "syntaxerror" in error and "strict_syntax" not in seen_patterns:
                seen_patterns.add("strict_syntax")
                # Syntax check is already in VerificationGate — no extra rule needed

            # CHANGE-24: Pattern 4 — Duplicate route definitions
            if ("duplicate" in error and "route" in error
                    and "dup_route" not in seen_patterns):
                seen_patterns.add("dup_route")

                def dup_route_rule(path: str, content: str, all_files: dict) -> str | None:
                    """Detect duplicate route definitions in router files."""
                    import re
                    if not path.endswith(".py"):
                        return None
                    routes = re.findall(
                        r"@(?:router|app)\.(get|post|put|delete|patch)\(\s*[\"']([^\"']+)",
                        content,
                    )
                    seen_routes: set[tuple[str, str]] = set()
                    for method, route_path in routes:
                        key = (method, route_path)
                        if key in seen_routes:
                            return (
                                f"Duplicate route {method.upper()} {route_path} "
                                f"in {path} (past mistake pattern)"
                            )
                        seen_routes.add(key)
                    return None

                rules.append(dup_route_rule)

            # CHANGE-24: Pattern 5 — Hardcoded secrets
            if ("secret" in error or "credential" in error or "api_key" in error
                    and "secrets" not in seen_patterns):
                seen_patterns.add("secrets")

                def secrets_rule(path: str, content: str, all_files: dict) -> str | None:
                    """Detect hardcoded secrets in generated code."""
                    import re
                    if not path.endswith((".py", ".ts", ".js", ".env")):
                        return None
                    patterns = [
                        r'(?:api_key|secret_key|password|token)\s*=\s*["\'][^"\']{8,}["\']',
                        r'sk-[a-zA-Z0-9]{20,}',
                        r'ghp_[a-zA-Z0-9]{30,}',
                    ]
                    for pat in patterns:
                        if re.search(pat, content, re.IGNORECASE):
                            return (
                                f"Possible hardcoded secret in {path}. "
                                "Use environment variables instead (past mistake)."
                            )
                    return None

                rules.append(secrets_rule)

        logger.debug(
            "validation_rules_built",
            agent=agent_name,
            rules_count=len(rules),
            patterns=list(seen_patterns),
        )
        return rules

    def analyze_error_patterns(
        self,
        agent_name: str,
        n_results: int = 20,
    ) -> list[dict[str, Any]]:
        """CHANGE-25: Analyze error patterns for proactive prevention.

        Groups recent failures by error_type, computes frequency, returns
        top 3 patterns with percentages and examples. Used by agents at
        startup to inject PROACTIVE WARNINGS into their system prompts.
        """
        chroma = _get_chroma()
        entries: list[dict[str, Any]] = []

        if chroma:
            try:
                collection = chroma.get_or_create_collection(
                    _collection_name(agent_name)
                )
                # Get recent mistakes (by recency)
                results = collection.get(
                    limit=n_results,
                    include=["metadatas"],
                )
                if results and results.get("metadatas"):
                    entries = results["metadatas"] or []
            except Exception:
                pass

        if not entries:
            # Fallback
            entries = _FALLBACK_STORE.get(agent_name, [])[-n_results:]

        if not entries:
            return []

        # Group by error type
        type_counts: dict[str, dict[str, Any]] = {}
        for entry in entries:
            error = entry.get("error", "")
            # Extract error type (first word before ":")
            error_type = error.split(":")[0].strip() if ":" in error else "Unknown"
            if error_type not in type_counts:
                type_counts[error_type] = {"count": 0, "example": error[:200]}
            type_counts[error_type]["count"] += 1

        total = max(len(entries), 1)
        patterns = [
            {
                "type": err_type,
                "pct": info["count"] / total,
                "count": info["count"],
                "example": info["example"],
            }
            for err_type, info in type_counts.items()
        ]
        patterns.sort(key=lambda p: p["count"], reverse=True)
        return patterns[:3]


# Module-level singleton
mistake_memory = MistakeMemory()
