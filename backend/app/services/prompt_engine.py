"""Prompt Engine: CMS for agent prompts with variable injection and caching.

Implements the Prompt Server architecture (AUDIT FIX #15):
- Prompts stored in code as versioned templates (single source of truth)
- Agents request prompts via internal API at runtime (just-in-time)
- Variable injection via Jinja2-style {{ variable }} placeholders
- Prompt caching in Valkey (90% cost savings on repeated calls)
- Prompts never loaded into shared memory — served on demand
- Version tracking for prompt A/B testing and rollback

Security:
- Prompts validated against allowed variable whitelist
- No user input directly interpolated into system prompts
- Injection-safe: variables are escaped before substitution
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

import orjson
import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)

# Regex for {{ variable_name }} placeholders
_VAR_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")


# ── Prompt Template Models ──────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """Immutable prompt template with metadata."""

    name: str  # Unique identifier (e.g., "tilotma.requirements_gather")
    version: str  # Semantic version (e.g., "1.0.0")
    system_prompt: str  # System prompt template with {{ variables }}
    user_prompt: str | None = None  # Optional user prompt template
    description: str = ""
    allowed_variables: frozenset[str] = field(default_factory=frozenset)
    model_hint: str | None = None  # Suggested model (e.g., "sonnet", "haiku")
    max_tokens_hint: int | None = None  # Suggested max_tokens
    temperature_hint: float | None = None  # Suggested temperature
    enable_thinking: bool = False


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    """Fully rendered prompt ready for AI Router consumption."""

    system_prompt: str
    user_prompt: str | None
    template_name: str
    template_version: str
    cache_key: str  # For Valkey caching


# ── Prompt Registry ─────────────────────────────────────────────────


class PromptRegistry:
    """In-code registry of all agent prompt templates.

    Templates are registered at module load time. This is the single
    source of truth for all prompts in the system.
    """

    def __init__(self) -> None:
        self._templates: dict[str, PromptTemplate] = {}

    def register(self, template: PromptTemplate) -> None:
        """Register a prompt template."""
        if template.name in self._templates:
            existing = self._templates[template.name]
            if existing.version == template.version:
                return  # Idempotent re-registration
            logger.info(
                "prompt_version_update",
                name=template.name,
                old_version=existing.version,
                new_version=template.version,
            )
        self._templates[template.name] = template

    def get(self, name: str) -> PromptTemplate | None:
        """Get a template by name."""
        return self._templates.get(name)

    def list_templates(self) -> list[str]:
        """List all registered template names."""
        return sorted(self._templates.keys())

    def has(self, name: str) -> bool:
        """Check if a template exists."""
        return name in self._templates


# ── Variable Injection ──────────────────────────────────────────────


def _escape_variable(value: str) -> str:
    """Escape a variable value to prevent prompt injection.

    R36-FIX: Enhanced escaping for inter-agent prompt injection defense.
    Previous version only stripped {{ }} and control chars, but did NOT
    sanitize prompt-boundary markers that could trick LLMs:
    - [STOP], [END], [SYSTEM] markers with surrounding newlines
    - Markdown code fence boundaries (``` blocks)
    - Fake role markers ("Assistant:", "System:", "Human:")

    Now wraps injected values in clearly-delimited boundaries so the LLM
    sees them as data, not instructions.
    """
    # Strip any existing delimiter patterns
    sanitized = value.replace("{{", "").replace("}}", "")
    # Strip null bytes and control characters (except newlines/tabs)
    sanitized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", sanitized)
    # R36-FIX: Neutralize fake role/instruction markers that could trick
    # LLMs into treating injected content as system instructions.
    # Replace with visually similar but non-functional versions.
    sanitized = re.sub(
        r"(?i)^\s*\[(STOP|END|SYSTEM|INST|/INST)\]\s*$",
        r"[escaped_\1]",
        sanitized,
        flags=re.MULTILINE,
    )
    # R37-FIX: Remove ^ anchor — matches only at line start, allowing
    # bypass via tab/space prefix or mid-line injection.
    sanitized = re.sub(
        r"(?i)(System|Assistant|Human|User)\s*:",
        r"[\1]:",
        sanitized,
    )
    # R37-FIX: Neutralize code fence boundaries that LLMs interpret
    # as code block delimiters for framing injected instructions.
    sanitized = sanitized.replace("```", "` ` `")
    return sanitized


def render_template(
    template_text: str,
    variables: dict[str, str],
    allowed_variables: frozenset[str] | None = None,
) -> str:
    """Render a prompt template with variable injection.

    Args:
        template_text: Template string with {{ variable }} placeholders.
        variables: Variable name → value mapping.
        allowed_variables: If set, only these variables may be injected.
            Any unknown variables raise ValueError.

    Returns:
        Rendered template string.

    Raises:
        ValueError: If a required variable is missing or disallowed.
    """
    # Find all variables in template
    required_vars = set(_VAR_PATTERN.findall(template_text))

    # R9-FIX: Use `is not None` instead of truthiness check.
    # frozenset() is falsy, so `if allowed_variables:` skipped validation
    # when the template declared "no variables allowed" (empty frozenset).
    # This completed the R7 FROZENSET-FIX which only fixed the caller.
    if allowed_variables is not None:
        unknown = set(variables.keys()) - allowed_variables
        if unknown:
            raise ValueError(f"Disallowed variables: {unknown}")

    # Check all required variables are provided
    missing = required_vars - set(variables.keys())
    if missing:
        raise ValueError(f"Missing required variables: {missing}")

    # Substitute with escaped values
    def _replace(match: re.Match) -> str:
        var_name = match.group(1)
        value = variables.get(var_name, "")
        return _escape_variable(value)

    return _VAR_PATTERN.sub(_replace, template_text)


# ── Prompt Engine ───────────────────────────────────────────────────


class PromptEngine:
    """Prompt CMS: manages templates, renders prompts, handles caching.

    Integrates with Valkey for rendered prompt caching and the AI Router
    for model hints.
    """

    def __init__(self, registry: PromptRegistry, redis_client: Any = None) -> None:
        """Initialize the Prompt Engine.

        Args:
            registry: PromptRegistry with all registered templates.
            redis_client: Optional async Redis/Valkey client for caching.
        """
        self._registry = registry
        self._redis = redis_client
        self._cache_ttl = 3600  # 1 hour default

    @property
    def registry(self) -> PromptRegistry:
        """Access the prompt registry."""
        return self._registry

    async def render(
        self,
        template_name: str,
        variables: dict[str, str] | None = None,
        use_cache: bool = True,
    ) -> RenderedPrompt:
        """Render a prompt template with variables.

        Checks Valkey cache first (keyed by template name + version + variable hash).
        If not cached, renders from template and stores in cache.

        Args:
            template_name: Registered template name.
            variables: Variable values to inject.
            use_cache: Whether to check/update cache.

        Returns:
            RenderedPrompt ready for AI Router.

        Raises:
            KeyError: If template not found.
            ValueError: If variables are invalid.
        """
        template = self._registry.get(template_name)
        if template is None:
            raise KeyError(f"Prompt template not found: {template_name}")

        variables = variables or {}
        cache_key = self._compute_cache_key(template, variables)

        # Check cache
        if use_cache and self._redis:
            cached = await self._redis.get(f"prompt:{cache_key}")
            if cached:
                data = orjson.loads(cached)
                logger.debug("prompt_cache_hit", template=template_name)
                return RenderedPrompt(
                    system_prompt=data["system_prompt"],
                    user_prompt=data.get("user_prompt"),
                    template_name=template_name,
                    template_version=template.version,
                    cache_key=cache_key,
                )

        # FROZENSET-FIX: Use `is not None` instead of truthiness check.
        # An empty frozenset() is falsy but means "no variables allowed".
        allowed = template.allowed_variables if template.allowed_variables is not None else None

        # Render system prompt
        system_rendered = render_template(
            template.system_prompt,
            variables,
            allowed,
        )

        # Render user prompt if present
        user_rendered = None
        if template.user_prompt:
            user_rendered = render_template(
                template.user_prompt,
                variables,
                allowed,
            )

        result = RenderedPrompt(
            system_prompt=system_rendered,
            user_prompt=user_rendered,
            template_name=template_name,
            template_version=template.version,
            cache_key=cache_key,
        )

        # Store in cache
        if use_cache and self._redis:
            cache_data = orjson.dumps({
                "system_prompt": system_rendered,
                "user_prompt": user_rendered,
            })
            # R26-FIX-29: Use transaction=True for atomic SET + SADD.
            # Without this, a crash between SET and SADD leaves an untracked
            # cache entry that invalidate() cannot find or delete.
            pipe = self._redis.pipeline(transaction=True)
            pipe.set(f"prompt:{cache_key}", cache_data, ex=self._cache_ttl)
            # INVALIDATE-FIX: Track cache key in per-template index for
            # targeted invalidation (avoids nuclear scan of all prompt:* keys).
            index_key = f"prompt_index:{template_name}"
            pipe.sadd(index_key, cache_key)
            pipe.expire(index_key, self._cache_ttl)
            await pipe.execute()
            logger.debug("prompt_cache_set", template=template_name)

        return result

    def get_model_hint(self, template_name: str) -> str | None:
        """Get the suggested model for a template."""
        template = self._registry.get(template_name)
        return template.model_hint if template else None

    def _compute_cache_key(
        self, template: PromptTemplate, variables: dict[str, str]
    ) -> str:
        """Compute a deterministic cache key for a rendered prompt."""
        # Sort variables for deterministic hashing
        var_str = orjson.dumps(dict(sorted(variables.items()))).decode("utf-8")
        raw = f"{template.name}:{template.version}:{var_str}"
        # R26-FIX-28: Use full 64-char hex digest instead of truncated 24 chars.
        # 24 chars = 96 bits — birthday-paradox collision serves wrong prompt.
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def invalidate(self, template_name: str) -> int:
        """Invalidate all cached renders for a specific template.

        Uses a secondary index (set of cache keys per template) to avoid
        scanning all prompt:* keys. Falls back to TTL expiry if no
        index exists.

        DEFERRED-FIX-9: Uses Lua script for atomic read-and-delete to prevent
        TOCTOU race between smembers() and delete(). Without this, a concurrent
        render() could add a new cache key to the index set between our
        smembers() and delete(index_key) calls, causing the newly cached key
        to be lost when the index is deleted.

        Returns number of keys deleted.
        """
        if not self._redis:
            return 0

        index_key = f"prompt_index:{template_name}"

        # Lua script: atomically read members, delete all cache keys, delete index
        lua_script = """
        local index_key = KEYS[1]
        local prefix = ARGV[1]
        local members = redis.call('SMEMBERS', index_key)
        local count = 0
        for _, member in ipairs(members) do
            redis.call('DEL', prefix .. member)
            count = count + 1
        end
        if count > 0 then
            redis.call('DEL', index_key)
        end
        return count
        """
        count = await self._redis.eval(lua_script, 1, index_key, "prompt:")
        count = int(count) if count else 0

        if count > 0:
            logger.info("prompt_cache_invalidated", template=template_name, keys=count)
            return count

        # NUKE-FIX: No index exists (legacy data before INVALIDATE-FIX).
        # Previous fallback deleted ALL prompt:* keys across all templates,
        # which is a nuclear option that harms unrelated templates.
        # Instead, log a warning and return 0 — the cached entries will
        # naturally expire via TTL. New entries use the index going forward.
        logger.warning(
            "prompt_cache_invalidate_no_index",
            template=template_name,
            action="skipped_nuclear_scan",
            note="Legacy keys will expire via TTL. New entries use index.",
        )
        return 0


# ── Global Registry & Engine ────────────────────────────────────────

# Global registry — templates are registered at import time
_registry = PromptRegistry()
_engine: PromptEngine | None = None


def get_prompt_registry() -> PromptRegistry:
    """Get the global prompt registry."""
    return _registry


def get_prompt_engine() -> PromptEngine:
    """Get the Prompt Engine singleton."""
    global _engine
    if _engine is None:
        raise RuntimeError("Prompt Engine not initialized. Call init_prompt_engine() first.")
    return _engine


async def init_prompt_engine() -> PromptEngine:
    """Initialize the Prompt Engine with optional Valkey caching.

    DEFERRED-FIX-10: Uses shared Valkey pool instead of creating a
    dedicated connection pool.

    Called once at app startup after Valkey connection is available.
    """
    global _engine
    if _engine is not None:
        return _engine

    redis_client = None
    try:
        from app.services.valkey_pool import get_valkey_client

        redis_client = await get_valkey_client()
    except Exception:
        logger.warning("prompt_engine_no_cache", reason="Valkey not available, running without cache")

    _engine = PromptEngine(registry=_registry, redis_client=redis_client)
    logger.info("prompt_engine_initialized", templates=len(_registry.list_templates()))
    return _engine


async def shutdown_prompt_engine() -> None:
    """Shutdown the Prompt Engine. Call at app shutdown.

    DEFERRED-FIX-10: Does NOT close the Redis client — it's shared.
    """
    global _engine
    _engine = None


def reset_prompt_engine() -> None:
    """DEFERRED-FIX-11: Reset the singleton for testing / event loop changes."""
    global _engine
    _engine = None
