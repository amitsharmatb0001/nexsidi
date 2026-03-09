"""AI Router: unified multi-model interface with complexity-based routing.

Supports Claude (Anthropic) and Gemini (Google) with:
- Complexity-based model selection (17 task types)
- Escalation chain: Flash → Haiku → Gemini Pro → Sonnet 4.5 → Sonnet 4.6 → Opus 4.6
- Security override: auth/payments/encryption ALWAYS uses Sonnet 4.6
- Streaming responses (SSE-compatible async generators)
- Prompt caching via cache_control blocks (90% cost savings)
- Circuit breaker per provider (prevents cascading failures)
- Tool use loop (extract → validate → execute → send result → loop)
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from app.config import get_settings
from app.services.secret_manager import get_vertex_credentials

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = structlog.get_logger(__name__)


# COST-CAP-FIX: Raised when cumulative pipeline AI cost exceeds the hard cap.
class PipelineCostLimitError(RuntimeError):
    """Pipeline cost exceeded _HARD_CAP_USD; raised inside ProjectCostTracker.record()."""


# TRUNCATION-FIX: Raised when AI response is cut off by token limit.
# Truncated code is worse than no code — silently returning incomplete output
# causes downstream agents to process broken files, generate incorrect tests,
# and ultimately deliver non-functional apps to customers.
class AIResponseTruncatedError(RuntimeError):
    """AI response was truncated (stop_reason=max_tokens). Caller should retry with higher max_tokens."""


# F6-FIX: Module-level registry mapping run_id → ProjectCostTracker.
# Lives here (not pipeline.py) so AIRouter.call() can do pre-flight cost checks
# without circular imports.
_ACTIVE_COST_TRACKERS: dict[str, "ProjectCostTracker"] = {}


def register_cost_tracker(run_id: str, tracker: "ProjectCostTracker") -> None:
    """F6-FIX: Register a cost tracker so AIRouter.call() can pre-flight check."""
    import time as _time
    # AUDIT-T2-17: Store registration timestamp for TTL-based eviction
    _ACTIVE_COST_TRACKERS[run_id] = tracker
    # Evict stale trackers (orphaned on early failure) — older than 24h
    _evict_stale_cost_trackers()


def _evict_stale_cost_trackers(max_age_seconds: int = 86400) -> None:
    """AUDIT-T2-17: Remove cost trackers orphaned by early pipeline failure."""
    import time as _time
    now = _time.monotonic()
    stale = [
        k for k, v in _ACTIVE_COST_TRACKERS.items()
        if hasattr(v, "_registered_at") and now - v._registered_at > max_age_seconds
    ]
    for k in stale:
        logger.info("cost_tracker_evicted_stale", run_id=k)
        del _ACTIVE_COST_TRACKERS[k]


def unregister_cost_tracker(run_id: str) -> None:
    """F6-FIX: Unregister a cost tracker when pipeline completes."""
    _ACTIVE_COST_TRACKERS.pop(run_id, None)


def get_active_cost_tracker(run_id: str) -> "ProjectCostTracker | None":
    """F6-FIX: Get a registered cost tracker by run_id."""
    return _ACTIVE_COST_TRACKERS.get(run_id)


def _sanitize_error(exc: Exception) -> str:
    """S-4-FIX: Sanitize exception messages to prevent API key leakage.

    httpx.HTTPStatusError.__str__ can include request URLs and headers,
    which may contain API keys (``x-api-key``, ``Authorization``).
    We strip anything that looks like a secret before logging.
    """
    import re
    msg = str(exc)
    # Strip API key patterns (Anthropic keys start with "sk-ant-", Google keys are long alphanumeric)
    msg = re.sub(r"sk-ant-[A-Za-z0-9_-]+", "[REDACTED]", msg)
    msg = re.sub(r"x-api-key['\"]?\s*[:=]\s*['\"]?[A-Za-z0-9_-]{20,}['\"]?", "x-api-key: [REDACTED]", msg, flags=re.IGNORECASE)
    # R19-FIX: Google's Gemini API uses `x-goog-api-key` header (not `x-api-key`).
    # httpx error messages for connection failures can include the full request
    # context with this header. Without this pattern, Google API keys leak in logs.
    msg = re.sub(r"x-goog-api-key['\"]?\s*[:=]\s*['\"]?[A-Za-z0-9_-]{20,}['\"]?", "x-goog-api-key: [REDACTED]", msg, flags=re.IGNORECASE)
    msg = re.sub(r"Authorization['\"]?\s*[:=]\s*['\"]?Bearer\s+[A-Za-z0-9_./-]{20,}['\"]?", "Authorization: [REDACTED]", msg, flags=re.IGNORECASE)
    msg = re.sub(r"AIza[A-Za-z0-9_-]{30,}", "[REDACTED]", msg)
    # R11-FIX: Google OAuth access tokens (used by Vertex AI) start with "ya29."
    # and can be 100+ chars.  Without this, Vertex AI errors leak Bearer tokens.
    msg = re.sub(r"ya29\.[A-Za-z0-9_./-]{20,}", "[REDACTED]", msg)
    # R27-FIX-1: Redact GCP project IDs and service account emails from Vertex AI
    # error messages. A 403 from Vertex AI includes the full endpoint URL like
    # "projects/my-proj/locations/us-central1/..." leaking infrastructure details.
    msg = re.sub(
        r"projects/[A-Za-z0-9_-]+/locations/[A-Za-z0-9_-]+",
        "projects/[REDACTED]/locations/[REDACTED]",
        msg,
    )
    msg = re.sub(
        r"[A-Za-z0-9_.+-]+@[A-Za-z0-9_.-]+\.iam\.gserviceaccount\.com",
        "[REDACTED_SA]",
        msg,
    )
    # 4.8-FIX: Additional patterns for GCS bucket paths, Firebase tokens,
    # and connection strings with embedded passwords.
    msg = re.sub(r"gs://[A-Za-z0-9_./-]+", "gs://[REDACTED]", msg)
    msg = re.sub(
        r"(?:postgresql|mysql|redis|mongodb|amqp)(?:\+\w+)?://[^\s\"']+",
        "[REDACTED_CONNECTION_STRING]",
        msg,
    )
    # Firebase server keys (starts with AAAA) and web API keys
    msg = re.sub(r"AAAA[A-Za-z0-9_:/-]{100,}", "[REDACTED_FIREBASE]", msg)
    return msg


# ── Model Registry ──────────────────────────────────────────────────


class Provider(str, Enum):
    ANTHROPIC = "anthropic"
    GOOGLE = "google"


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """Immutable model specification."""

    model_id: str
    provider: Provider
    display_name: str
    cost_tier: int  # 1 (cheapest) to 5 (most expensive)
    supports_thinking: bool = False
    max_output_tokens: int = 8192


# All available models — ordered cheapest to most expensive
MODELS: dict[str, ModelSpec] = {
    # ── Tier 1: Fast & cheap ──
    "gemini-flash": ModelSpec(
        model_id="gemini-2.5-flash",
        provider=Provider.GOOGLE,
        display_name="Gemini 2.5 Flash",
        cost_tier=1,
        max_output_tokens=65536,
    ),
    "haiku": ModelSpec(
        model_id="claude-haiku-4-5-20251001",
        provider=Provider.ANTHROPIC,
        display_name="Claude Haiku 4.5",
        cost_tier=1,
        max_output_tokens=8192,  # Haiku's actual limit
    ),
    # ── Tier 2: Mid-range ──
    "gemini-pro": ModelSpec(
        model_id="gemini-2.5-pro",
        provider=Provider.GOOGLE,
        display_name="Gemini 2.5 Pro",
        cost_tier=2,
        supports_thinking=True,
        max_output_tokens=65536,
    ),
    "gemini-3-flash": ModelSpec(
        model_id="gemini-3-flash-preview",
        provider=Provider.GOOGLE,
        display_name="Gemini 3 Flash Preview",
        cost_tier=2,
        supports_thinking=True,
        max_output_tokens=65536,
    ),
    # ── Tier 3: High-capability ──
    # V5-FIX (CRITICAL-2): Was "claude-sonnet-4-5-20241022" (wrong date,
    # no such model exists — every API call would 400).
    "sonnet-4.5": ModelSpec(
        model_id="claude-sonnet-4-5-20250514",
        provider=Provider.ANTHROPIC,
        display_name="Claude Sonnet 4.5",
        cost_tier=3,
        supports_thinking=True,
        max_output_tokens=64000,
    ),
    "sonnet": ModelSpec(
        model_id="claude-sonnet-4-6-20250514",
        provider=Provider.ANTHROPIC,
        display_name="Claude Sonnet 4.6",
        cost_tier=3,
        supports_thinking=True,
        max_output_tokens=64000,
    ),
    "gemini-3.1-flash-lite": ModelSpec(
        model_id="gemini-3.1-flash-lite-preview",
        provider=Provider.GOOGLE,
        display_name="Gemini 3.1 Flash Lite Preview",
        cost_tier=3,
        supports_thinking=True,
        max_output_tokens=65536,
    ),
    # ── Tier 4: Advanced reasoning ──
    "gemini-3.1-pro": ModelSpec(
        model_id="gemini-3.1-pro-preview",
        provider=Provider.GOOGLE,
        display_name="Gemini 3.1 Pro Preview",
        cost_tier=4,
        supports_thinking=True,
        max_output_tokens=65536,
    ),
    # ── Tier 5: Maximum capability ──
    "opus": ModelSpec(
        model_id="claude-opus-4-6-20250514",
        provider=Provider.ANTHROPIC,
        display_name="Claude Opus 4.6",
        cost_tier=5,
        supports_thinking=True,
        max_output_tokens=128000,
    ),
}

# Escalation chain — index 0 is cheapest, last is most powerful.
# V5-FIX (HIGH-2): Reordered so cost strictly increases.  Old order had
# gemini-3-flash ($0.20/M) AFTER gemini-pro ($1.25/M) — a cost regression
# that downgraded capability on escalation.  Also moved gemini-3.1-flash-lite
# to its correct cost position and removed sonnet-4.5 from mid-chain
# (it was routing to a broken model ID, now fixed but kept at correct tier).
ESCALATION_CHAIN: list[str] = [
    "gemini-flash",           # tier 1: $0.15/M input
    "gemini-3-flash",         # tier 1: $0.20/M input
    "gemini-3.1-flash-lite",  # tier 1: $0.20/M input
    "haiku",                  # tier 2: $0.25/M input (per 1K: $0.00025)
    "gemini-pro",             # tier 2: $1.25/M input
    "gemini-3.1-pro",         # tier 3: $1.75/M input
    "sonnet-4.5",             # tier 3: $3.00/M input
    "sonnet",                 # tier 3: $3.00/M input (latest)
    "opus",                   # tier 5: $15.00/M input
]


# ── Task Types & Routing ────────────────────────────────────────────


class TaskComplexity(str, Enum):
    """Complexity levels for routing decisions."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# Security-critical task types that ALWAYS use Sonnet 4.6 (AUDIT FIX #17)
SECURITY_CRITICAL_TASKS: frozenset[str] = frozenset({
    "auth_code",
    "payment_code",
    "encryption_code",
    "pii_handling",
    "security_audit",
    "credential_management",
})

# Default model selection by complexity
COMPLEXITY_TO_MODEL: dict[TaskComplexity, str] = {
    TaskComplexity.LOW: "gemini-flash",
    TaskComplexity.MEDIUM: "haiku",
    TaskComplexity.HIGH: "sonnet",
    TaskComplexity.CRITICAL: "opus",
}

# ── Per-mode routing maps ─────────────────────────────────────────

_MODE_COMPLEXITY_MAP: dict[str, dict[TaskComplexity, str]] = {
    "mixed": COMPLEXITY_TO_MODEL,
    "gemini": {
        TaskComplexity.LOW: "gemini-flash",
        TaskComplexity.MEDIUM: "gemini-pro",
        TaskComplexity.HIGH: "gemini-3.1-flash-lite",
        TaskComplexity.CRITICAL: "gemini-3.1-pro",
    },
    "claude": {
        TaskComplexity.LOW: "haiku",
        TaskComplexity.MEDIUM: "haiku",
        TaskComplexity.HIGH: "sonnet",
        TaskComplexity.CRITICAL: "opus",
    },
}

_MODE_SECURITY_MODEL: dict[str, str] = {
    "mixed": "sonnet",
    "gemini": "gemini-3.1-flash-lite",
    "claude": "sonnet",
}

_MODE_GENERATION_MAP: dict[str, dict[str, str]] = {
    "mixed": {"small": "gemini-flash", "medium": "sonnet", "large": "opus"},
    "gemini": {"small": "gemini-flash", "medium": "gemini-3.1-flash-lite", "large": "gemini-3.1-pro"},
    "claude": {"small": "haiku", "medium": "sonnet", "large": "opus"},
}


def get_ai_mode() -> str:
    """Get the current AI provider mode from settings.

    Feature flag logic:
    - Default: ``ai_provider_mode="gemini"`` (Gemini-only for beta)
    - When ``enable_claude=True``: upgrades ``"gemini"`` to ``"mixed"``
      so Claude models become available alongside Gemini
    - Explicit ``ai_provider_mode="mixed"`` or ``"claude"`` via env var
      is respected regardless of the feature flag
    """
    settings = get_settings()
    mode = settings.ai_provider_mode
    # Feature flag: activate Claude alongside Gemini
    if settings.enable_claude and mode == "gemini":
        return "mixed"
    return mode


# ── Circuit Breaker ─────────────────────────────────────────────────


class CircuitState:
    """Per-provider circuit breaker state.

    States:
    - CLOSED (is_open=False, _half_open=False): Normal operation
    - OPEN   (is_open=True, _half_open=False): All requests blocked
    - HALF_OPEN (is_open=True, _half_open=True): One probe allowed

    REFIX: True half-open state — only ONE request is allowed through
    after the timeout. If it succeeds, circuit closes.  If it fails,
    circuit re-opens.  This prevents the thundering herd problem where
    all blocked requests rush through simultaneously.

    S-1-FIX: Uses asyncio.Lock to make state transitions atomic.
    Without this, concurrent coroutines (e.g. parallel agent AI calls)
    can race on failure counting and half-open transitions.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        reset_timeout_seconds: float = 60.0,
        provider_key: str = "",
    ) -> None:
        self.failures: int = 0
        self.last_failure_at: float = 0.0
        self.is_open: bool = False
        self._half_open: bool = False
        self.failure_threshold = failure_threshold
        self.reset_timeout_seconds = reset_timeout_seconds
        # 4.5-FIX: provider_key enables distributed state via Valkey so all
        # workers share the same circuit breaker (e.g. "anthropic", "google").
        self._provider_key = provider_key
        # R27-FIX-23: Lazy-init Lock to avoid binding to wrong/no event loop
        # when CircuitState is created at import time or from a non-async context
        # (same pattern as DEFERRED-FIX-4 for Semaphore).
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        # AUDIT-FIX: Double-check pattern is safe in asyncio (single-threaded),
        # but we document the invariant explicitly. No `await` between check and
        # assignment means no coroutine interleaving is possible here.
        if self._lock is None:
            # Safe: no await between None-check and assignment = no interleaving.
            # This runs on the asyncio event loop thread; no preemption possible.
            self._lock = asyncio.Lock()
        return self._lock

    async def _sync_to_valkey(self) -> None:
        """4.5-FIX: Push circuit state to Valkey for cross-worker sharing."""
        if not self._provider_key:
            return
        try:
            from app.services.valkey_pool import get_valkey_client
            client = await get_valkey_client()
            key = f"circuit:{self._provider_key}"
            await client.hset(key, mapping={
                "failures": str(self.failures),
                "is_open": str(int(self.is_open)),
                "half_open": str(int(self._half_open)),
                "last_failure_at": str(self.last_failure_at),
            })
            await client.expire(key, 300)  # 5min TTL — stale state auto-clears
        except Exception as exc:
            # AUDIT-T1-5: Log sync failures — previously silent, caused cross-worker divergence
            logger.warning("circuit_breaker_valkey_sync_failed", provider=self._provider_key, error=str(exc)[:100])

    async def _sync_from_valkey(self) -> None:
        """4.5-FIX: Read circuit state from Valkey (other workers may have updated)."""
        if not self._provider_key:
            return
        try:
            from app.services.valkey_pool import get_valkey_client
            client = await get_valkey_client()
            data = await client.hgetall(f"circuit:{self._provider_key}")
            if data:
                self.failures = int(data.get(b"failures", 0))
                self.is_open = bool(int(data.get(b"is_open", 0)))
                self._half_open = bool(int(data.get(b"half_open", 0)))
                self.last_failure_at = float(data.get(b"last_failure_at", 0.0))
        except Exception as exc:
            # AUDIT-T1-5: Log sync failures — previously silent, caused cross-worker divergence
            logger.warning("circuit_breaker_valkey_sync_failed", provider=self._provider_key, error=str(exc)[:100])

    async def record_failure(self) -> None:
        async with self._get_lock():
            # 4.5-FIX: Sync from Valkey first so we see other workers' failures.
            await self._sync_from_valkey()
            self.failures += 1
            # 4.5-FIX: Use time.time() (not monotonic) for cross-process consistency.
            self.last_failure_at = time.time()
            if self._half_open:
                # Half-open probe failed → re-open circuit.
                # REVIEW-FIX: last_failure_at is ALREADY updated above, which
                # resets the timeout. Without this, the circuit would immediately
                # transition back to half-open on the next is_available() check
                # because elapsed >= reset_timeout_seconds would be True from the
                # OLD failure timestamp.
                self._half_open = False
                logger.warning("circuit_breaker_half_open_failed", failures=self.failures)
            elif self.failures >= self.failure_threshold:
                self.is_open = True
                logger.warning("circuit_breaker_open", failures=self.failures)
            await self._sync_to_valkey()

    async def record_success(self) -> None:
        async with self._get_lock():
            if self._half_open:
                # Half-open probe succeeded → fully close circuit
                logger.info("circuit_breaker_closed_after_probe")
            self.failures = 0
            self.is_open = False
            self._half_open = False
            await self._sync_to_valkey()

    async def is_available(self) -> bool:
        async with self._get_lock():
            # 4.5-FIX: Read latest state from Valkey (other workers may have tripped).
            await self._sync_from_valkey()
            if not self.is_open:
                return True
            # Check if reset timeout has passed → transition to half-open
            elapsed = time.time() - self.last_failure_at
            if elapsed >= self.reset_timeout_seconds:
                if not self._half_open:
                    # Allow exactly ONE probe request
                    self._half_open = True
                    logger.info("circuit_breaker_half_open")
                    await self._sync_to_valkey()
                    return True
                # Already in half-open with a probe in flight → block others
                return False
            return False


# ── Request / Response Models ───────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ContentBlock:
    """Base content block in a structured message.

    PHASE-5: Replaces pseudo-XML tool use encoding with proper typed blocks.
    Anthropic's API expects structured content arrays for tool use messages:
        [{"type": "text", ...}, {"type": "tool_use", ...}]

    This avoids the fragile pseudo-XML approach
    (``[tool_use id="..." name="..."]``) which required string parsing
    and could not represent nested tool inputs correctly.
    """

    type: str  # "text", "tool_use", "tool_result"


@dataclass(frozen=True, slots=True)
class TextBlock(ContentBlock):
    """Plain text content block."""

    type: str = "text"
    text: str = ""


@dataclass(frozen=True, slots=True)
class ToolUseBlock(ContentBlock):
    """Tool use request block (from assistant)."""

    type: str = "tool_use"
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolResultBlock(ContentBlock):
    """Tool result block (from user, in response to tool_use)."""

    type: str = "tool_result"
    tool_use_id: str = ""
    content: str = ""
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class AIMessage:
    """Single message in a conversation.

    PHASE-5: ``content`` accepts either a plain string (backward-compatible)
    or a list of ContentBlock for structured tool use messages.

    When ``content`` is a list:
    - Assistant messages contain TextBlock + ToolUseBlock
    - User messages contain ToolResultBlock (responses to tool calls)
    """

    role: str  # "user", "assistant", "system"
    content: str | list[ContentBlock] = ""


@dataclass(frozen=True, slots=True)
class SharedContext:
    """Cacheable shared context block for multi-agent pipeline runs.

    When multiple agents run within the same pipeline, they share a common
    project specification and architecture contract.  Wrapping these in a
    ``SharedContext`` allows the first AI call to cache the shared prefix
    (Anthropic ``cache_control: ephemeral``) so subsequent calls get a
    ~90 % input-token cost reduction on the shared portion.
    """

    project_spec: str = ""
    architecture_contract: str = ""

    def to_anthropic_system_blocks(self) -> list[dict[str, Any]]:
        """Convert to Anthropic system blocks with cache_control."""
        if not self.project_spec and not self.architecture_contract:
            return []
        parts: list[str] = []
        if self.project_spec:
            parts.append(f"## Project Specification\n{self.project_spec}")
        if self.architecture_contract:
            parts.append(f"## Architecture Contract\n{self.architecture_contract}")
        return [{
            "type": "text",
            "text": "\n\n".join(parts),
            "cache_control": {"type": "ephemeral"},
        }]

    def to_gemini_context_text(self) -> str:
        """Combine into a single text block for Gemini context caching."""
        parts: list[str] = []
        if self.project_spec:
            parts.append(f"## Project Specification\n{self.project_spec}")
        if self.architecture_contract:
            parts.append(f"## Architecture Contract\n{self.architecture_contract}")
        return "\n\n".join(parts)


@dataclass(frozen=True, slots=True)
class AIRequest:
    """Request to the AI Router."""

    messages: list[AIMessage]
    task_type: str = "general"
    complexity: TaskComplexity = TaskComplexity.MEDIUM
    model_override: str | None = None  # Force a specific model
    system_prompt: str | None = None
    max_tokens: int | None = None
    temperature: float = 0.7
    enable_thinking: bool = False
    cache_system_prompt: bool = True  # Enable prompt caching for system prompt
    tools: list[dict[str, Any]] | None = None  # Tool definitions for tool use
    shared_context: SharedContext | None = None  # Cacheable shared project context
    dynamic_system_context: str | None = None  # CACHE-FIX: appended to system AFTER cached blocks
    pipeline_run_id: str | None = None  # F6-FIX: for pre-flight cost cap check
    # NATIVE-SEARCH: When True, inject provider-native web search:
    #   Claude → web_search_20250305 (Brave Search, server-side)
    #   Gemini → google_search grounding (Google Search, server-side)
    # No extra API key needed — search is built into the AI provider.
    enable_native_web_search: bool = False


@dataclass(slots=True)
class AIResponse:
    """Response from the AI Router."""

    content: str
    model_used: str
    provider: Provider
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    cached: bool = False
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = "end_turn"  # "end_turn", "max_tokens", "stop_sequence", "STOP", "MAX_TOKENS"
    was_truncated: bool = False  # True when response was cut off by token limit
    security_downgraded: bool = False  # F7-FIX: True when security task used non-Sonnet model


# ── AI Router ───────────────────────────────────────────────────────


class AIRouter:
    """Unified multi-model AI client with routing, escalation, and caching.

    Google/Gemini calls support two paths:
      1. Google AI API (public key) -- generativelanguage.googleapis.com
      2. Vertex AI REST (YugNex SA)  -- aiplatform.googleapis.com

    Vertex AI is preferred when use_cloud_secrets=True and YUGNEX_AI_CREDENTIALS
    is available. Falls back to the public API key if Vertex AI is unavailable.
    """

    # H2-FIX: Retry config for transient failures (429, 500, 502, 503, 529)
    _RETRY_STATUS_CODES = frozenset({429, 500, 502, 503, 529})
    _MAX_RETRIES = 3
    _RETRY_BASE_DELAY = 1.0  # seconds, exponential backoff: 1, 2, 4

    def __init__(self) -> None:
        settings = get_settings()
        self._anthropic_key = settings.anthropic_api_key
        self._google_key = settings.google_ai_api_key

        # Vertex AI dual-project settings (Gemini — uses YugNex project)
        self._use_vertex = settings.use_cloud_secrets
        self._vertex_project = settings.gcp_ai_project_id  # "yugnex-ai"
        self._vertex_location = "us-central1"  # Gemini stays on us-central1

        # Claude via Vertex AI Model Garden (asia-south1 / Mumbai)
        # IMPORTANT: This is for Claude ONLY — Gemini config is untouched.
        self._claude_vertex_location = settings.claude_vertex_location  # "asia-south1"
        self._claude_vertex_project = settings.claude_vertex_project_id

        self._circuits: dict[Provider, CircuitState] = {
            Provider.ANTHROPIC: CircuitState(provider_key="anthropic"),
            Provider.GOOGLE: CircuitState(provider_key="google"),
        }
        # Shared httpx client -- connection pooling across providers
        self._http: httpx.AsyncClient | None = None
        # H5-FIX: Lock prevents two coroutines both creating httpx clients
        self._http_lock = asyncio.Lock()

    async def _get_http(self) -> httpx.AsyncClient:
        """Lazy-init shared HTTP client (thread-safe via asyncio.Lock)."""
        if self._http is not None and not self._http.is_closed:
            return self._http
        async with self._http_lock:
            # Double-check after acquiring lock
            if self._http is None or self._http.is_closed:
                self._http = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))
            return self._http

    async def close(self) -> None:
        """Shutdown HTTP client. Call at app shutdown.

        R22-FIX: Acquire _http_lock to prevent race with concurrent
        _get_http() callers. Without the lock, a concurrent call() could
        get a reference to self._http between our None-check and aclose(),
        then use the closed client, raising httpx.PoolError.
        """
        async with self._http_lock:
            if self._http is not None and not self._http.is_closed:
                await self._http.aclose()
                self._http = None

    async def select_model(self, request: AIRequest) -> ModelSpec:
        """Select the best model for this request.

        Priority:
        1. Explicit model_override
        2. Security-critical tasks → mode-appropriate security model
        3. Complexity-based routing (mode-aware)
        4. Fallback through escalation chain if circuit is open
        """
        mode = get_ai_mode()

        # 1. Explicit override
        # R36-FIX: Check circuit breaker for model_override. Previously,
        # model_override returned immediately without checking the circuit
        # breaker. If an agent forced model_override="opus" and the
        # Anthropic circuit was open, the call would fail, record another
        # CB failure (extending the open period), and never fall back to
        # an available provider. Same pattern as R20-FIX for security tasks.
        if request.model_override and request.model_override in MODELS:
            override_spec = MODELS[request.model_override]
            if await self._circuits[override_spec.provider].is_available():
                return override_spec
            logger.warning(
                "model_override_circuit_open",
                model=request.model_override,
                provider=override_spec.provider.value,
            )
            return await self._find_available_model(request.model_override)

        # 2. Security override — mode-appropriate security model
        # R20-FIX: Check circuit breaker BEFORE returning security model.
        # Previously, security-critical tasks (auth, payments, encryption)
        # bypassed the circuit breaker check — if the security model's
        # provider was down (circuit OPEN), every security task would fail
        # immediately, recording another CB failure (extending the open
        # period) and never falling back to an available provider.
        if request.task_type in SECURITY_CRITICAL_TASKS:
            security_key = _MODE_SECURITY_MODEL[mode]
            security_spec = MODELS[security_key]
            # F7-FIX: Warn when security task uses non-Sonnet model
            if security_key not in ("sonnet", "sonnet-4.5"):
                logger.warning(
                    "security_model_downgrade",
                    task_type=request.task_type,
                    model=security_key,
                    mode=mode,
                    recommended="sonnet",
                    msg="Security task routed to non-Sonnet model. "
                        "Set enable_claude=true for security tasks.",
                )
            if await self._circuits[security_spec.provider].is_available():
                logger.info("security_override", task_type=request.task_type, model=security_key, mode=mode)
                return security_spec
            # Security model's provider is down — escalate through chain
            logger.warning(
                "security_model_unavailable_escalating",
                task_type=request.task_type,
                model=security_key,
            )
            return await self._find_available_model(security_key)

        # 3. Complexity routing — mode-aware
        active_map = _MODE_COMPLEXITY_MAP[mode]
        # R27-FIX-22: Use explicit default per mode instead of fragile
        # list(values())[1] which depends on dict insertion order.
        _MODE_DEFAULT_MODEL = {"mixed": "haiku", "gemini": "gemini-pro", "claude": "haiku"}
        model_key = active_map.get(request.complexity)
        if model_key is None:
            # P2-5: Unmapped complexity — use highest available model (fail-safe).
            # Old code fell back to haiku, catastrophic for complex projects.
            model_key = (
                active_map.get(TaskComplexity.CRITICAL)
                or active_map.get(TaskComplexity.HIGH)
                or _MODE_DEFAULT_MODEL.get(mode, "haiku")
            )
            logger.warning(
                "ai_router_complexity_unmapped",
                complexity=request.complexity,
                fallback=model_key,
            )
        spec = MODELS[model_key]

        # 4. Check circuit breaker — escalate if provider is down
        if not await self._circuits[spec.provider].is_available():
            logger.warning("circuit_open_escalating", original=model_key, mode=mode)
            spec = await self._find_available_model(model_key)

        return spec

    def _get_active_escalation_chain(self) -> list[str]:
        """Get escalation chain filtered by current AI provider mode."""
        mode = get_ai_mode()
        if mode == "mixed":
            return ESCALATION_CHAIN
        target_provider = Provider.ANTHROPIC if mode == "claude" else Provider.GOOGLE
        return [k for k in ESCALATION_CHAIN if MODELS[k].provider == target_provider]

    async def _find_available_model(self, failed_key: str) -> ModelSpec:
        """Find next available model in escalation chain (mode-filtered)."""
        chain = self._get_active_escalation_chain()
        try:
            start_idx = chain.index(failed_key)
        except ValueError:
            start_idx = 0

        # Try models above the failed one in the chain
        for key in chain[start_idx + 1:]:
            spec = MODELS[key]
            if await self._circuits[spec.provider].is_available():
                logger.info("escalated_to", model=key)
                return spec

        # Try models below as last resort
        for key in chain[:start_idx]:
            spec = MODELS[key]
            if await self._circuits[spec.provider].is_available():
                logger.info("fallback_to", model=key)
                return spec

        # Everything is down — return the original and let it fail with a clear error
        logger.error("all_providers_unavailable")
        return MODELS[failed_key]

    async def call(self, request: AIRequest) -> AIResponse:
        """Make a single AI call with automatic model selection.

        Handles provider selection, API formatting, error handling,
        circuit breaker updates, and retry with exponential backoff
        for transient failures (429, 500, 502, 503, 529).
        """
        spec = await self.select_model(request)
        start = time.monotonic()
        request_id = str(uuid.uuid4())

        # F6-FIX: Pre-flight cost cap check BEFORE the AI call.
        # If the estimated cost would push the pipeline over the hard cap,
        # raise PipelineCostLimitError immediately (no API call, no billing).
        if request.pipeline_run_id:
            tracker = _ACTIVE_COST_TRACKERS.get(request.pipeline_run_id)
            if tracker is not None:
                # Estimate input tokens from message content length (rough: 1 token ≈ 4 chars)
                est_input = max(
                    sum(len(m.content) for m in request.messages) // 4,
                    2000,  # minimum estimate
                )
                tracker.estimate_and_check(spec.display_name, estimated_input_tokens=est_input)

        logger.info(
            "ai_call_start",
            model=spec.display_name,
            task_type=request.task_type,
            complexity=request.complexity.value,
            request_id=request_id,
        )

        last_exc: Exception | None = None
        for attempt in range(self._MAX_RETRIES + 1):
            try:
                if spec.provider == Provider.ANTHROPIC:
                    response = await self._call_anthropic(spec, request, request_id)
                else:
                    response = await self._call_google(spec, request, request_id)

                response.latency_ms = (time.monotonic() - start) * 1000
                await self._circuits[spec.provider].record_success()

                logger.info(
                    "ai_call_complete",
                    model=spec.display_name,
                    latency_ms=round(response.latency_ms, 1),
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    request_id=request_id,
                    attempt=attempt + 1,
                    was_truncated=response.was_truncated,
                )

                # TRUNCATION-FIX: FAIL on truncated responses instead of silently
                # returning incomplete code. Truncated code/JSON causes downstream
                # agents to process broken files and deliver non-functional apps.
                if response.was_truncated:
                    logger.error(
                        "ai_response_truncated",
                        model=spec.display_name,
                        output_tokens=response.output_tokens,
                        request_id=request_id,
                        task_type=request.task_type,
                        hint="Response was cut off by token limit. Increase max_tokens "
                             "or split the request into smaller parts.",
                    )
                    raise AIResponseTruncatedError(
                        f"AI response truncated (stop_reason=max_tokens) from "
                        f"{spec.display_name} after {response.output_tokens} output tokens. "
                        f"Task type: {request.task_type}. Increase max_tokens or split the request."
                    )

                return response

            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code in self._RETRY_STATUS_CODES and attempt < self._MAX_RETRIES:
                    # PHASE-3: Add jitter to prevent thundering herd on retry
                    delay = self._RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
                    # Respect Retry-After header if present
                    # RETRY-CAP-FIX: Cap at 120s to prevent malicious/misconfigured
                    # upstream from causing unbounded sleep (e.g., Retry-After: 99999)
                    retry_after = exc.response.headers.get("retry-after")
                    if retry_after:
                        try:
                            delay = min(max(delay, float(retry_after)), 120.0)
                        except ValueError:
                            pass  # Expected: invalid value — fall through to default
                    logger.warning(
                        "ai_call_retrying",
                        model=spec.display_name,
                        status=exc.response.status_code,
                        attempt=attempt + 1,
                        delay_s=delay,
                        request_id=request_id,
                    )
                    await asyncio.sleep(delay)
                    continue
                # Non-retryable status or final attempt
                await self._circuits[spec.provider].record_failure()
                latency = (time.monotonic() - start) * 1000
                logger.error(
                    "ai_call_failed",
                    model=spec.display_name,
                    error=_sanitize_error(exc),
                    status=exc.response.status_code,
                    latency_ms=round(latency, 1),
                    request_id=request_id,
                    attempts=attempt + 1,
                )
                raise

            except Exception as exc:
                last_exc = exc
                # DEFERRED-FIX-13: Retry on connection-level errors (ConnectError,
                # ReadTimeout, etc.). Previously, only httpx.HTTPStatusError was
                # retried. Connection failures (network blip, DNS timeout, TLS
                # handshake failure) immediately failed with no retry, even though
                # they're inherently transient.
                _retryable_conn = isinstance(exc, (
                    httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout,
                    httpx.ConnectTimeout, httpx.PoolTimeout,
                    ConnectionError, TimeoutError,
                ))
                if _retryable_conn and attempt < self._MAX_RETRIES:
                    delay = self._RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(
                        "ai_call_retrying_connection",
                        model=spec.display_name,
                        error_type=type(exc).__name__,
                        attempt=attempt + 1,
                        delay_s=round(delay, 2),
                        request_id=request_id,
                    )
                    await asyncio.sleep(delay)
                    continue
                await self._circuits[spec.provider].record_failure()
                latency = (time.monotonic() - start) * 1000
                logger.error(
                    "ai_call_failed",
                    model=spec.display_name,
                    error=_sanitize_error(exc),
                    latency_ms=round(latency, 1),
                    request_id=request_id,
                    attempts=attempt + 1,
                )
                raise

        # Should not reach here, but just in case
        raise last_exc  # type: ignore[misc]

    async def call_stream(self, request: AIRequest) -> AsyncGenerator[str, None]:
        """Stream AI response as an async generator (for SSE).

        Yields text chunks as they arrive from the provider.

        PHASE-3: Retry logic for pre-first-chunk failures. If the connection
        fails BEFORE any chunks are yielded (e.g., 429 rate limit, 502 gateway
        error), we retry with exponential backoff + jitter. Once a chunk has
        been yielded, retry is impossible (caller has partial data).

        H6-FIX: Records circuit breaker success/failure for streaming.
        """
        spec = await self.select_model(request)
        _RETRYABLE_CODES = {429, 500, 502, 503, 529}
        _MAX_STREAM_RETRIES = 2

        for attempt in range(_MAX_STREAM_RETRIES + 1):
            chunks_yielded = 0
            try:
                if spec.provider == Provider.ANTHROPIC:
                    async for chunk in self._stream_anthropic(spec, request):
                        chunks_yielded += 1
                        yield chunk
                else:
                    async for chunk in self._stream_google(spec, request):
                        chunks_yielded += 1
                        yield chunk
                # H6-FIX: Record success after successful stream completion
                await self._circuits[spec.provider].record_success()
                return  # Success — exit generator

            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                can_retry = (
                    status_code in _RETRYABLE_CODES
                    and chunks_yielded == 0  # Only retry pre-first-chunk
                    and attempt < _MAX_STREAM_RETRIES
                )
                if can_retry:
                    # PHASE-3: Exponential backoff with jitter
                    delay = min(2 ** attempt + random.uniform(0, 1), 30)
                    logger.warning(
                        "stream_retry",
                        attempt=attempt + 1,
                        status_code=status_code,
                        delay_s=round(delay, 2),
                        provider=spec.provider.value,
                    )
                    await asyncio.sleep(delay)
                    continue  # Retry
                # Non-retryable or already yielded chunks — fail
                await self._circuits[spec.provider].record_failure()
                logger.error("stream_failed", error=_sanitize_error(exc))
                raise

            except Exception as exc:
                # Non-HTTP errors (connection reset, timeout, etc.)
                # Only retry if no chunks were yielded
                if chunks_yielded == 0 and attempt < _MAX_STREAM_RETRIES:
                    delay = min(2 ** attempt + random.uniform(0, 1), 30)
                    logger.warning(
                        "stream_retry",
                        attempt=attempt + 1,
                        error=_sanitize_error(exc),
                        delay_s=round(delay, 2),
                    )
                    await asyncio.sleep(delay)
                    continue  # Retry
                await self._circuits[spec.provider].record_failure()
                logger.error("stream_failed", error=_sanitize_error(exc))
                raise

    # ── Anthropic Batch API (50% cost discount) ──────────────────────

    async def call_batch(self, requests: list[AIRequest]) -> str:
        """Submit multiple requests as an Anthropic message batch.

        Returns the ``batch_id`` for polling via :meth:`get_batch_results`.
        Batch requests are processed asynchronously (up to 24 h) at 50 %
        of the real-time per-token price.  Only Anthropic models are
        included; non-Anthropic requests are silently skipped.
        """
        import orjson

        http = await self._get_http()
        # BATCH-CACHE-FIX: Include prompt-caching beta alongside batch beta
        # so cached system prompts get the 90% discount even in batch mode.
        has_cacheable = any(r.cache_system_prompt and r.system_prompt for r in requests)
        beta_features = ["message-batches-2024-09-24"]
        if has_cacheable:
            beta_features.append("prompt-caching-2024-07-31")

        headers = {
            "x-api-key": self._anthropic_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "anthropic-beta": ",".join(beta_features),
        }

        batch_requests: list[dict[str, Any]] = []
        for i, req in enumerate(requests):
            spec = await self.select_model(req)
            if spec.provider != Provider.ANTHROPIC:
                continue
            body = self._build_anthropic_body(spec, req)
            batch_requests.append({
                "custom_id": f"req_{i}",
                "params": body,
            })

        if not batch_requests:
            raise ValueError("No Anthropic-routable requests in batch")

        resp = await http.post(
            "https://api.anthropic.com/v1/messages/batches",
            headers=headers,
            content=orjson.dumps({"requests": batch_requests}),
        )
        resp.raise_for_status()
        data = resp.json()
        batch_id = data["id"]
        logger.info("batch_submitted", batch_id=batch_id, count=len(batch_requests))
        return batch_id

    async def get_batch_results(self, batch_id: str) -> list[AIResponse] | None:
        """Poll for batch results.

        Returns ``None`` if the batch is still processing.
        Returns a list of :class:`AIResponse` when complete.
        """
        import re as _re

        import orjson

        # R16-FIX: Validate batch_id format to prevent path injection / SSRF.
        # Without this, a batch_id like "../../v1/other-endpoint" would cause
        # the request to hit an unintended Anthropic API endpoint with our API key.
        if not _re.match(r"^msgbatch_[A-Za-z0-9_-]+$", batch_id):
            raise ValueError(f"Invalid batch_id format: {batch_id!r}")

        http = await self._get_http()
        headers = {
            "x-api-key": self._anthropic_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "message-batches-2024-09-24",
        }

        resp = await http.get(
            f"https://api.anthropic.com/v1/messages/batches/{batch_id}",
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()

        if data["processing_status"] != "ended":
            return None

        # Fetch JSONL results
        # R12-FIX: Validate results_url domain to prevent SSRF. If the API
        # response were tampered (MITM, compromise), the API key would be
        # sent as x-api-key to an attacker-controlled URL.
        results_url = data["results_url"]
        if not results_url.startswith("https://api.anthropic.com/"):
            raise ValueError(f"Unexpected batch results URL domain: {results_url[:80]}")
        results_resp = await http.get(results_url, headers=headers)
        results_resp.raise_for_status()

        responses: list[AIResponse] = []
        failed_count = 0
        for line in results_resp.text.strip().split("\n"):
            if not line.strip():
                continue
            result = orjson.loads(line)
            result_type = result.get("result", {}).get("type", "unknown")
            custom_id = result.get("custom_id", "?")

            if result_type == "succeeded":
                msg = result["result"]["message"]
                text = "".join(
                    b["text"] for b in msg.get("content", []) if b.get("type") == "text"
                )
                usage = msg.get("usage", {})
                responses.append(AIResponse(
                    content=text,
                    model_used=msg.get("model", ""),
                    provider=Provider.ANTHROPIC,
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                ))
            else:
                # H9-FIX: Log failures instead of silently dropping
                failed_count += 1
                error_info = result.get("result", {}).get("error", {})
                logger.warning(
                    "batch_result_failed",
                    batch_id=batch_id,
                    custom_id=custom_id,
                    result_type=result_type,
                    error=error_info,
                )

        logger.info(
            "batch_results_received",
            batch_id=batch_id,
            succeeded=len(responses),
            failed=failed_count,
        )
        return responses

    # ── Anthropic (Claude) ──────────────────────────────────────────

    async def _call_anthropic(
        self, spec: ModelSpec, request: AIRequest, request_id: str
    ) -> AIResponse:
        """Call Claude via Vertex AI Model Garden (preferred) or direct Anthropic API.

        Routing priority:
        1. Vertex AI Model Garden (asia-south1) — if claude_vertex_project_id is set
           and Vertex AI credentials are available. No API key needed.
        2. Direct Anthropic API — fallback when Vertex AI is unavailable.
        """
        # Try Vertex AI Model Garden first (Claude in asia-south1)
        if self._claude_vertex_project and self._use_vertex:
            try:
                return await self._call_anthropic_vertex(spec, request, request_id)
            except Exception as vertex_exc:
                logger.warning(
                    "claude_vertex_fallback_to_direct",
                    error=_sanitize_error(vertex_exc),
                    request_id=request_id,
                )
                # Fall through to direct Anthropic API

        return await self._call_anthropic_direct(spec, request, request_id)

    async def _call_anthropic_vertex(
        self, spec: ModelSpec, request: AIRequest, request_id: str
    ) -> AIResponse:
        """Call Claude via Vertex AI Model Garden (asia-south1 / Mumbai).

        Endpoint:
            POST https://{location}-aiplatform.googleapis.com/v1/projects/{project}/
                 locations/{location}/publishers/anthropic/models/{model}:rawPredict

        Uses OAuth Bearer token from VertexAICredentialManager (same SA as Gemini).
        Response format is identical to direct Anthropic API.
        """
        token = await self._get_vertex_token()
        if not token:
            raise RuntimeError("No Vertex AI credentials for Claude Model Garden")

        http = await self._get_http()
        url = (
            f"https://{self._claude_vertex_location}-aiplatform.googleapis.com/v1/"
            f"projects/{self._claude_vertex_project}/"
            f"locations/{self._claude_vertex_location}/"
            f"publishers/anthropic/models/{spec.model_id}:rawPredict"
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        body = self._build_anthropic_body(spec, request)
        # Vertex AI Model Garden uses rawPredict — body format is identical
        # to direct Anthropic API but wrapped in the Vertex AI endpoint.

        logger.debug(
            "claude_vertex_call",
            project=self._claude_vertex_project,
            location=self._claude_vertex_location,
            model=spec.model_id,
            request_id=request_id,
        )

        resp = await http.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

        return self._parse_anthropic_response(data, spec, request_id)

    async def _call_anthropic_direct(
        self, spec: ModelSpec, request: AIRequest, request_id: str
    ) -> AIResponse:
        """Call Claude via direct Anthropic Messages API (fallback)."""
        http = await self._get_http()
        headers = {
            "x-api-key": self._anthropic_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        # Enable prompt caching header if using cached system prompt
        if request.cache_system_prompt and request.system_prompt:
            headers["anthropic-beta"] = "prompt-caching-2024-07-31"

        body = self._build_anthropic_body(spec, request)

        resp = await http.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()

        return self._parse_anthropic_response(data, spec, request_id)

    @staticmethod
    def _parse_anthropic_response(
        data: dict[str, Any], spec: ModelSpec, request_id: str
    ) -> AIResponse:
        """Parse response from either Vertex AI Model Garden or direct Anthropic API.

        Both return the same Anthropic Messages API format.
        """
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in data.get("content", []):
            if block["type"] == "text":
                text_parts.append(block["text"])
            elif block["type"] == "tool_use":
                tool_calls.append({
                    "id": block["id"],
                    "name": block["name"],
                    "input": block["input"],
                })
            # NATIVE-SEARCH: Claude's server-side web search returns these block types.
            # server_tool_use = search query executed by Claude internally
            # web_search_tool_result = search results (encrypted_content for citations)
            # These are handled internally by the API — we just collect text from them.
            elif block["type"] == "server_tool_use":
                # Claude's internal search query — log but don't add to tool_calls
                # (these are executed server-side, not by our tool handler)
                pass
            elif block["type"] == "web_search_tool_result":
                # Search results — content may contain web_search_result items
                # These are passed back automatically in multi-turn conversations
                pass

        usage = data.get("usage", {})
        stop_reason = data.get("stop_reason", "end_turn")
        return AIResponse(
            content="\n".join(text_parts),
            model_used=spec.model_id,
            provider=Provider.ANTHROPIC,
            request_id=request_id,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cached=usage.get("cache_read_input_tokens", 0) > 0,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            was_truncated=(stop_reason == "max_tokens"),
        )

    @staticmethod
    def _serialize_content(content: str | list[ContentBlock]) -> str | list[dict[str, Any]]:
        """Serialize message content for Anthropic API.

        PHASE-5: Handles both plain strings and structured ContentBlock lists.
        Plain strings are passed through as-is (backward compatible).
        ContentBlock lists are serialized to Anthropic's expected format:
            - TextBlock → {"type": "text", "text": "..."}
            - ToolUseBlock → {"type": "tool_use", "id": "...", "name": "...", "input": {...}}
            - ToolResultBlock → {"type": "tool_result", "tool_use_id": "...", "content": "..."}
        """
        if isinstance(content, str):
            return content

        blocks: list[dict[str, Any]] = []
        for block in content:
            if isinstance(block, TextBlock):
                if block.text:  # Skip empty text blocks
                    blocks.append({"type": "text", "text": block.text})
            elif isinstance(block, ToolUseBlock):
                blocks.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
            elif isinstance(block, ToolResultBlock):
                result_block: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": block.tool_use_id,
                    "content": block.content,
                }
                if block.is_error:
                    result_block["is_error"] = True
                blocks.append(result_block)
            elif isinstance(block, dict):
                # NATIVE-SEARCH: Pass through raw dict blocks (server_tool_use,
                # web_search_tool_result) from Claude's native search responses.
                # These must be preserved verbatim in multi-turn conversations
                # for citations to work correctly.
                blocks.append(block)
        return blocks or ""

    @staticmethod
    def _flatten_content_to_text(content: str | list[ContentBlock]) -> str:
        """Flatten structured content to plain text (for Google/Gemini API).

        PHASE-5: Google's API doesn't support Anthropic-style structured
        content blocks. This flattens them to readable text.
        """
        if isinstance(content, str):
            return content

        parts: list[str] = []
        for block in content:
            if isinstance(block, TextBlock):
                if block.text:
                    parts.append(block.text)
            elif isinstance(block, ToolUseBlock):
                import json as _json
                parts.append(f"[Tool call: {block.name}({_json.dumps(block.input)})]")
            elif isinstance(block, ToolResultBlock):
                parts.append(f"[Tool result: {block.content}]")
        return "\n".join(parts)

    def _build_anthropic_body(self, spec: ModelSpec, request: AIRequest) -> dict[str, Any]:
        """Build Anthropic API request body."""
        messages = [
            {"role": m.role, "content": self._serialize_content(m.content)}
            for m in request.messages if m.role != "system"
        ]

        body: dict[str, Any] = {
            "model": spec.model_id,
            "messages": messages,
            "max_tokens": request.max_tokens or spec.max_output_tokens,
            "temperature": request.temperature,
        }

        # System prompt with optional prompt caching + shared context
        if request.system_prompt:
            if request.cache_system_prompt:
                system_blocks: list[dict[str, Any]] = []
                # Shared context block (cached — same across agents in pipeline)
                if request.shared_context:
                    system_blocks.extend(
                        request.shared_context.to_anthropic_system_blocks()
                    )
                # Agent-specific system prompt (also cached)
                system_blocks.append({
                    "type": "text",
                    "text": request.system_prompt,
                    "cache_control": {"type": "ephemeral"},
                })
                # CACHE-FIX: Dynamic context (e.g., previously generated files) appended WITHOUT
                # cache_control so it doesn't pollute the stable cache prefix.
                if request.dynamic_system_context:
                    system_blocks.append({
                        "type": "text",
                        "text": request.dynamic_system_context,
                        # No cache_control — this changes every call
                    })
                body["system"] = system_blocks
            else:
                body["system"] = request.system_prompt

        # Extended thinking — budget must NOT eat into output budget
        # Use // 4 so thinking gets at most 25% of output capacity
        # THINK-FIX: Enforce minimum budget of 1024 tokens to prevent
        # zero/tiny budget when max_tokens is small (e.g. 100 // 4 = 25).
        if request.enable_thinking and spec.supports_thinking:
            # DEFERRED-FIX-14: Log when overriding caller's temperature.
            # Anthropic requires temperature=1.0 for extended thinking. Previously
            # this silently overwrote the caller's temperature, which could produce
            # unexpected output randomness for agents that specifically set
            # temperature=0.0 for deterministic code generation.
            if request.temperature != 1.0:
                logger.debug(
                    "thinking_temperature_override",
                    original=request.temperature,
                    override=1.0,
                    model=spec.display_name,
                )
            body["temperature"] = 1.0  # Required for thinking
            thinking_budget = max(1024, min(10000, body["max_tokens"] // 4))
            # R30-FIX-2: Clamp budget_tokens < max_tokens — Anthropic API requires
            # strict less-than. Without this, max_tokens < 1025 with enable_thinking
            # produces budget_tokens(1024) >= max_tokens, causing a 400 error.
            if thinking_budget >= body["max_tokens"]:
                logger.warning(
                    "thinking_disabled_low_max_tokens",
                    max_tokens=body["max_tokens"],
                    would_be_budget=thinking_budget,
                )
            else:
                body["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}

        # Tools
        if request.tools:
            body["tools"] = list(request.tools)  # Copy so we don't mutate original
        else:
            body["tools"] = []

        # NATIVE-SEARCH: Inject Claude's built-in web search tool.
        # This gives Claude access to real-time Brave Search without any API key.
        # Runs server-side inside the API call — no external HTTP needed.
        # Supports: Opus 4.6, Sonnet 4.6, Sonnet 4.5, Haiku 4.5
        if request.enable_native_web_search:
            # Only inject if not already present (avoid duplicates)
            _has_native_search = any(
                t.get("type", "").startswith("web_search_2025") or
                t.get("type", "").startswith("web_search_2026")
                for t in body["tools"]
            )
            if not _has_native_search:
                body["tools"].append({
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 5,
                    "user_location": {
                        "type": "approximate",
                        "country": "IN",
                        "region": "Maharashtra",
                        "timezone": "Asia/Kolkata",
                    },
                })
                logger.debug("native_web_search_injected", provider="anthropic")

        # Remove empty tools list to avoid API errors
        if not body["tools"]:
            del body["tools"]

        return body

    async def _stream_anthropic(
        self, spec: ModelSpec, request: AIRequest
    ) -> AsyncGenerator[str, None]:
        """Stream Claude response via SSE."""
        http = await self._get_http()
        headers = {
            "x-api-key": self._anthropic_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        # STREAM-CACHE-FIX: Enable prompt caching header for streaming
        # (was missing — caching blocks were built but header not sent)
        if request.cache_system_prompt and request.system_prompt:
            headers["anthropic-beta"] = "prompt-caching-2024-07-31"

        body = self._build_anthropic_body(spec, request)
        body["stream"] = True

        async with http.stream(
            "POST",
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=body,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    import orjson

                    # R20-FIX: Handle SSE sentinel and unparseable events.
                    # Anthropic sends "data: [DONE]" or event types that aren't
                    # valid JSON after "data: ". orjson.loads("[DONE]") raises
                    # JSONDecodeError, which propagates as Exception, trips the
                    # circuit breaker, and blocks the provider for 60s — even
                    # though the stream completed successfully.
                    data_str = line[6:].strip()
                    if not data_str or data_str == "[DONE]":
                        continue
                    try:
                        event = orjson.loads(data_str)
                    except (ValueError, orjson.JSONDecodeError):
                        continue
                    event_type = event.get("type")
                    if event_type == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            yield delta["text"]
                        # DEFERRED-FIX-12: Log dropped tool_use blocks in streaming.
                        # call_stream() yields text chunks only — it cannot return
                        # structured tool_use blocks to callers expecting plain text.
                        # For tool use, callers MUST use call() (non-streaming).
                        elif delta.get("type") == "input_json_delta":
                            logger.warning(
                                "stream_tool_use_dropped",
                                note="Tool use in streaming is not supported. Use call() instead.",
                            )
                    elif event_type == "message_delta":
                        # DEFERRED-FIX-15: Parse message_delta for stop_reason.
                        # Anthropic sends message_delta at the end of the stream
                        # with stop_reason. If stop_reason is "max_tokens", the
                        # response was truncated — callers should be aware.
                        stop_reason = event.get("delta", {}).get("stop_reason")
                        if stop_reason == "max_tokens":
                            logger.warning("stream_truncated", stop_reason=stop_reason)

    # ── Google (Gemini) ─────────────────────────────────────────────
    #
    # Two paths:
    #   1. Vertex AI REST (production) -- aiplatform.googleapis.com
    #      Uses YugNex SA token via VertexAICredentialManager.
    #   2. Google AI API (dev/fallback) -- generativelanguage.googleapis.com
    #      Uses public API key.
    #
    # _call_google / _stream_google auto-detect which path to use.

    async def _get_vertex_token(self) -> str | None:
        """Get a fresh Vertex AI access token from VertexAICredentialManager.

        Returns None if credentials are unavailable (falls back to public API).

        VERTEX-FIX: Runs the blocking token refresh in a thread pool so it
        doesn't block the event loop. google.auth.transport.requests.Request()
        uses synchronous urllib3 under the hood (~100-500ms network round trip).
        """
        if not self._use_vertex:
            return None
        mgr = get_vertex_credentials()
        return await asyncio.to_thread(mgr.get_access_token)

    async def _call_google(
        self, spec: ModelSpec, request: AIRequest, request_id: str
    ) -> AIResponse:
        """Call Gemini via Vertex AI REST or Google AI API (auto-detect)."""
        token = await self._get_vertex_token()
        if token:
            return await self._call_vertex(spec, request, request_id, token)
        return await self._call_google_ai(spec, request, request_id)

    async def _call_google_ai(
        self, spec: ModelSpec, request: AIRequest, request_id: str
    ) -> AIResponse:
        """Call Gemini via public Google AI Generative Language API (API key).

        H1-FIX: API key moved from URL query string to x-goog-api-key header
        to prevent leakage in logs, proxies, Referer headers, and tracebacks.
        """
        http = await self._get_http()
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{spec.model_id}:generateContent"
        )
        headers = {"x-goog-api-key": self._google_key, "Content-Type": "application/json"}

        # Try Gemini context caching for large shared contexts
        cached_content: str | None = None
        if request.shared_context and self._google_key:
            context_text = request.shared_context.to_gemini_context_text()
            if context_text:
                mgr = get_gemini_cache_manager()
                cached_content = await mgr.get_or_create_cache(
                    http, spec.model_id, context_text, self._google_key,
                )

        body = self._build_google_body(spec, request, cached_content=cached_content)

        resp = await http.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

        return self._parse_google_response(data, spec, request_id)

    async def _call_vertex(
        self, spec: ModelSpec, request: AIRequest, request_id: str, token: str
    ) -> AIResponse:
        """Call Gemini via Vertex AI REST API (YugNex project, SA token).

        Endpoint:
            POST https://aiplatform.googleapis.com/v1/projects/{project}/
                 locations/{location}/publishers/google/models/{model}:generateContent

        The request body format is identical to the Google AI API — we reuse
        _build_google_body() and _parse_google_response().
        """
        http = await self._get_http()
        url = (
            f"https://{self._vertex_location}-aiplatform.googleapis.com/v1/"
            f"projects/{self._vertex_project}/"
            f"locations/{self._vertex_location}/"
            f"publishers/google/models/{spec.model_id}:generateContent"
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        body = self._build_google_body(spec, request)

        logger.debug(
            "vertex_ai_call",
            project=self._vertex_project,
            model=spec.model_id,
            request_id=request_id,
        )

        resp = await http.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

        return self._parse_google_response(data, spec, request_id)

    def _parse_google_response(
        self, data: dict[str, Any], spec: ModelSpec, request_id: str
    ) -> AIResponse:
        """Parse response from either Google AI API or Vertex AI REST.

        Both APIs return the same response structure.
        """
        text = ""
        finish_reason = "STOP"
        candidates = data.get("candidates", [])

        # R10-FIX: Detect Gemini safety filter blocks. When content triggers
        # safety filters, the API returns 200 OK with either empty candidates
        # or candidates[0].finishReason == "SAFETY". Without this check, the
        # caller gets content="" which looks like a successful empty response,
        # causing downstream agents to produce empty files or crash.
        prompt_feedback = data.get("promptFeedback", {})
        block_reason = prompt_feedback.get("blockReason", "")
        if block_reason:
            logger.warning("gemini_safety_blocked", reason=block_reason, request_id=request_id)
            raise ValueError(f"Gemini safety filter blocked request: {block_reason}")

        # R12-FIX: Empty candidates without a blockReason is an edge case
        # where the API returns 200 OK but generates nothing. Without this
        # check, downstream agents receive content="" and produce empty files.
        if not candidates:
            logger.warning("gemini_empty_candidates", request_id=request_id)
            raise ValueError("Gemini returned empty candidates (no content generated)")

        parts = candidates[0].get("content", {}).get("parts", [])
        text_parts: list[str] = []
        # R36-FIX: Extract tool calls from Gemini response parts.
        # Gemini returns functionCall parts alongside text parts:
        #   {"functionCall": {"name": "tool_name", "args": {...}}}
        tool_calls: list[dict[str, Any]] = []
        for p in parts:
            if "text" in p:
                text_parts.append(p["text"])
            elif "functionCall" in p:
                fc = p["functionCall"]
                tool_calls.append({
                    "id": f"gemini_{uuid.uuid4().hex[:12]}",
                    "name": fc.get("name", ""),
                    "input": fc.get("args", {}),
                })
        text = "".join(text_parts)
        finish_reason = candidates[0].get("finishReason", "STOP")
        if finish_reason == "SAFETY":
            logger.warning("gemini_safety_filtered", request_id=request_id)
            raise ValueError("Gemini response blocked by safety filter")
        # R21-FIX: Handle RECITATION (copyright block) and OTHER (unknown error).
        # Previously these passed through silently, returning partial or empty
        # content that downstream agents treated as valid AI output.
        if finish_reason == "RECITATION":
            logger.warning("gemini_recitation_blocked", request_id=request_id)
            raise ValueError("Gemini response blocked: potential copyright recitation")
        if finish_reason == "OTHER":
            logger.warning("gemini_other_finish_reason", request_id=request_id)
            raise ValueError("Gemini response terminated with unspecified reason")

        # NATIVE-SEARCH: Extract Gemini grounding metadata from search results.
        # When google_search tool is used, response includes groundingMetadata
        # with search queries, web results, and grounding chunks (citations).
        grounding = candidates[0].get("groundingMetadata", {})
        if grounding:
            # Append grounding sources as context for the response
            grounding_chunks = grounding.get("groundingChunks", [])
            if grounding_chunks:
                citations: list[str] = []
                for chunk in grounding_chunks[:5]:  # Max 5 citations
                    web = chunk.get("web", {})
                    if web.get("title") and web.get("uri"):
                        citations.append(f"[{web['title']}]({web['uri']})")
                if citations:
                    text += "\n\nSources: " + ", ".join(citations)
            logger.debug(
                "gemini_grounding_metadata",
                search_queries=len(grounding.get("webSearchQueries", [])),
                grounding_chunks=len(grounding_chunks),
                request_id=request_id,
            )

        usage = data.get("usageMetadata", {})
        return AIResponse(
            content=text,
            model_used=spec.model_id,
            provider=Provider.GOOGLE,
            request_id=request_id,
            input_tokens=usage.get("promptTokenCount", 0),
            output_tokens=usage.get("candidatesTokenCount", 0),
            tool_calls=tool_calls,  # R36-FIX: Gemini tool calls
            stop_reason=finish_reason,
            was_truncated=(finish_reason == "MAX_TOKENS"),
        )

    def _build_google_body(
        self, spec: ModelSpec, request: AIRequest, cached_content: str | None = None,
    ) -> dict[str, Any]:
        """Build Google Generative Language API request body."""
        contents: list[dict[str, Any]] = []

        for msg in request.messages:
            if msg.role == "system":
                continue
            role = "user" if msg.role == "user" else "model"
            # PHASE-5: Flatten structured content to text for Google API
            text = self._flatten_content_to_text(msg.content)
            contents.append({"role": role, "parts": [{"text": text}]})

        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": request.max_tokens or spec.max_output_tokens,
                "temperature": request.temperature,
            },
        }

        # C1-REFIX: Google API returns 400 when BOTH cachedContent AND
        # systemInstruction are set.  The cachedContent covers the shared
        # project context.  When caching is active we must inject the
        # agent-specific system_prompt as a leading user message instead.
        # When there's no cache, systemInstruction works normally.
        if cached_content:
            body["cachedContent"] = cached_content
            # Inject system_prompt as leading user preamble (Google forbids
            # systemInstruction alongside cachedContent)
            # R27-FIX-10: Merge into first user message instead of inserting
            # a separate user turn.  Two consecutive "user" roles cause Gemini
            # to return 400 ("Please ensure multi-turn requests alternate
            # between user and model").
            if request.system_prompt:
                if body["contents"] and body["contents"][0]["role"] == "user":
                    # Merge into existing first user message
                    body["contents"][0]["parts"].insert(
                        0, {"text": f"[System Instructions]\n{request.system_prompt}\n\n"}
                    )
                else:
                    preamble = {"role": "user", "parts": [{"text": f"[System Instructions]\n{request.system_prompt}"}]}
                    body["contents"].insert(0, preamble)
        elif request.system_prompt:
            body["systemInstruction"] = {"parts": [{"text": request.system_prompt}]}

        # R36-FIX: Tool definitions for Gemini. Previously completely missing —
        # agents calling call_ai_with_tools() with a Gemini model would silently
        # lose all tool definitions. The model received no tools and could not
        # use them, causing tool use loops to fail silently.
        # Google's format: tools = [{"functionDeclarations": [...]}]
        # Anthropic format uses input_schema; Google uses "parameters".
        _google_tools: list[dict[str, Any]] = []
        if request.tools:
            function_declarations: list[dict[str, Any]] = []
            for tool in request.tools:
                # Skip Anthropic-native tool types (web_search_*) — not valid for Google
                if tool.get("type", "").startswith("web_search_"):
                    continue
                decl: dict[str, Any] = {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                }
                # Anthropic uses "input_schema", Google uses "parameters"
                schema = tool.get("input_schema") or tool.get("parameters")
                if schema:
                    decl["parameters"] = schema
                function_declarations.append(decl)
            if function_declarations:
                _google_tools.append({"functionDeclarations": function_declarations})

        # NATIVE-SEARCH: Inject Gemini's Google Search grounding tool.
        # This gives Gemini access to real-time Google Search without API key.
        # Returns groundingMetadata with search queries, web results, citations.
        # Supports: Gemini 2.5 Flash, Gemini 2.5 Pro on Vertex AI
        if request.enable_native_web_search:
            _google_tools.append({"googleSearch": {}})
            logger.debug("native_web_search_injected", provider="google")

        if _google_tools:
            body["tools"] = _google_tools

        # Thinking (Gemini calls it "thought")
        if request.enable_thinking and spec.supports_thinking:
            body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 8192}

        return body

    async def _stream_google(
        self, spec: ModelSpec, request: AIRequest
    ) -> AsyncGenerator[str, None]:
        """Stream Gemini response (auto-detects Vertex AI vs public API)."""
        token = await self._get_vertex_token()
        if token:
            async for chunk in self._stream_vertex(spec, request, token):
                yield chunk
        else:
            async for chunk in self._stream_google_ai(spec, request):
                yield chunk

    async def _stream_google_ai(
        self, spec: ModelSpec, request: AIRequest
    ) -> AsyncGenerator[str, None]:
        """Stream Gemini response via public Google AI API (API key)."""
        http = await self._get_http()
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{spec.model_id}:streamGenerateContent?alt=sse"
        )
        headers = {"x-goog-api-key": self._google_key, "Content-Type": "application/json"}

        body = self._build_google_body(spec, request)

        async with http.stream("POST", url, headers=headers, json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    import orjson

                    # R20-FIX: Handle SSE sentinel (same as _stream_anthropic)
                    data_str = line[6:].strip()
                    if not data_str or data_str == "[DONE]":
                        continue
                    try:
                        event = orjson.loads(data_str)
                    except (ValueError, orjson.JSONDecodeError):
                        continue
                    candidates = event.get("candidates", [])
                    if candidates:
                        # R22-FIX: Check finishReason in streaming events.
                        # Non-streaming _parse_google_response catches SAFETY,
                        # RECITATION, OTHER — but streaming silently yielded
                        # truncated output, causing downstream agents to process
                        # safety-blocked content as valid AI output.
                        finish_reason = candidates[0].get("finishReason")
                        if finish_reason in ("SAFETY", "RECITATION", "OTHER"):
                            raise ValueError(f"Gemini stream terminated: {finish_reason}")
                        parts = candidates[0].get("content", {}).get("parts", [])
                        for part in parts:
                            if "text" in part:
                                yield part["text"]

    async def _stream_vertex(
        self, spec: ModelSpec, request: AIRequest, token: str
    ) -> AsyncGenerator[str, None]:
        """Stream Gemini response via Vertex AI REST API (YugNex project).

        Endpoint:
            POST https://{location}-aiplatform.googleapis.com/v1/projects/{project}/
                 locations/{location}/publishers/google/models/{model}:streamGenerateContent?alt=sse
        """
        http = await self._get_http()
        url = (
            f"https://{self._vertex_location}-aiplatform.googleapis.com/v1/"
            f"projects/{self._vertex_project}/"
            f"locations/{self._vertex_location}/"
            f"publishers/google/models/{spec.model_id}:streamGenerateContent?alt=sse"
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        body = self._build_google_body(spec, request)

        async with http.stream("POST", url, headers=headers, json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    import orjson

                    # R20-FIX: Handle SSE sentinel (same as _stream_anthropic)
                    data_str = line[6:].strip()
                    if not data_str or data_str == "[DONE]":
                        continue
                    try:
                        event = orjson.loads(data_str)
                    except (ValueError, orjson.JSONDecodeError):
                        continue
                    candidates = event.get("candidates", [])
                    if candidates:
                        # R22-FIX: Check finishReason in streaming (same as _stream_google_ai)
                        finish_reason = candidates[0].get("finishReason")
                        if finish_reason in ("SAFETY", "RECITATION", "OTHER"):
                            raise ValueError(f"Gemini stream terminated: {finish_reason}")
                        parts = candidates[0].get("content", {}).get("parts", [])
                        for part in parts:
                            if "text" in part:
                                yield part["text"]


# ── Gemini Context Caching ─────────────────────────────────────────


class GeminiCacheManager:
    """Manages Google Gemini ``cachedContents`` for large shared context.

    Creates a cached content resource when the combined system/shared context
    exceeds ~32K tokens (~128K chars).  Subsequent requests that share the
    same content get a cache hit, reducing input token costs.

    TTL defaults to 1 hour.  Cache entries are keyed by content hash.

    H7-FIX: Cache entries now store creation time and are bounded to
    MAX_ENTRIES to prevent unbounded memory growth.  Stale entries
    (past TTL) are evicted on access.
    """

    _MIN_CHARS_FOR_CACHE: int = 128_000  # ~32K tokens
    _MAX_ENTRIES: int = 100  # prevent unbounded memory growth
    _DEFAULT_TTL_SECONDS: int = 3600
    # R18-FIX: Expire local cache 5 min before remote to avoid returning
    # stale cache names that Google has already expired. Without this margin,
    # near TTL boundaries the fast path returns an expired cache name →
    # Google returns 404 → agent fails → circuit breaker trips.
    _TTL_SAFETY_MARGIN: int = 300  # 5 min

    def __init__(self) -> None:
        # content_hash → (cache_name, created_at_monotonic)
        self._cache: dict[str, tuple[str, float]] = {}
        # R17-FIX: Per-hash locks instead of a single global lock.
        # The global lock was held during the Google API HTTP POST (500ms-5s),
        # serializing ALL cache operations including those for different content
        # hashes. With 21 agents, this caused severe latency (20+ seconds of
        # serialized waits). Per-hash locks allow concurrent cache creation
        # for different content hashes while still preventing duplicate remote
        # cache creation for the SAME hash.
        self._global_lock = asyncio.Lock()  # Only for dict access (fast)
        self._pending: dict[str, asyncio.Lock] = {}  # Per-hash locks (slow I/O)

    async def get_or_create_cache(
        self,
        http: httpx.AsyncClient,
        model_id: str,
        context_text: str,
        api_key: str,
        ttl: str = "3600s",
    ) -> str | None:
        """Create or retrieve a cached content resource.

        Returns the cache name (e.g. ``cachedContents/abc123``) or ``None``
        if the content is too small for caching.
        """
        if len(context_text) < self._MIN_CHARS_FOR_CACHE:
            return None

        # R19-FIX: Include model_id in cache key. Google cached content is
        # scoped to a specific model — using a Flash cache entry for a Pro
        # request returns 400 from Google's API. Previously only content_hash
        # was used, causing cross-model cache poisoning when different agents
        # use different models with the same shared context.
        # Also use full SHA-256 (64 hex chars = 256 bits) instead of truncated
        # 16 chars (64 bits). With birthday paradox, 64 bits gives 50% collision
        # at ~2^32 inputs — allowing intentional cross-tenant cache poisoning.
        content_hash = hashlib.sha256(
            f"{model_id}:{context_text}".encode()
        ).hexdigest()

        # Fast path: check cache without lock
        # R21-FIX: Use .get() instead of [] to avoid KeyError if another
        # coroutine evicts this hash between the `in` check and the subscript.
        # The eviction at line ~1484 runs under per-hash lock, but this fast
        # path is lockless, so the entry can disappear mid-read.
        cached = self._cache.get(content_hash)
        if cached is not None:
            cache_name, created_at = cached
            if (time.monotonic() - created_at) < (self._DEFAULT_TTL_SECONDS - self._TTL_SAFETY_MARGIN):
                logger.debug("gemini_cache_hit", hash=content_hash)
                return cache_name

        # R17-FIX: Get or create per-hash lock (fast, global lock only for dict access)
        async with self._global_lock:
            if content_hash not in self._pending:
                self._pending[content_hash] = asyncio.Lock()
            hash_lock = self._pending[content_hash]

        # Per-hash lock: only serializes concurrent requests for the SAME hash
        async with hash_lock:
            # Double-check after lock (another coroutine may have created it)
            if content_hash in self._cache:
                cache_name, created_at = self._cache[content_hash]
                if (time.monotonic() - created_at) < (self._DEFAULT_TTL_SECONDS - self._TTL_SAFETY_MARGIN):
                    logger.debug("gemini_cache_hit", hash=content_hash)
                    return cache_name
                else:
                    # Stale — evict
                    del self._cache[content_hash]
                    logger.debug("gemini_cache_stale_evicted", hash=content_hash)

            # H7-FIX: Evict oldest if at capacity (fast, no I/O)
            if len(self._cache) >= self._MAX_ENTRIES:
                oldest_key = min(self._cache, key=lambda k: self._cache[k][1])
                del self._cache[oldest_key]
                self._pending.pop(oldest_key, None)  # Clean up stale lock
                logger.debug("gemini_cache_evicted_oldest", hash=oldest_key)

            # H1-FIX: API key in header, not URL query string
            # Network I/O: NOT under global lock — only per-hash lock
            url = "https://generativelanguage.googleapis.com/v1beta/cachedContents"
            headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
            body = {
                "model": f"models/{model_id}",
                "contents": [{"role": "user", "parts": [{"text": context_text}]}],
                "ttl": ttl,
            }

            try:
                resp = await http.post(url, headers=headers, json=body)
                if resp.status_code == 200:
                    cache_name = resp.json().get("name")
                    if cache_name:
                        self._cache[content_hash] = (cache_name, time.monotonic())
                        logger.info(
                            "gemini_cache_created",
                            hash=content_hash,
                            cache_name=cache_name,
                        )
                        return cache_name
                logger.warning(
                    "gemini_cache_create_failed",
                    status=resp.status_code,
                    hash=content_hash,
                )
            except Exception as exc:
                logger.warning("gemini_cache_create_error", error=_sanitize_error(exc))
            finally:
                # 4.4-FIX: Guarantee _pending cleanup on ALL exit paths via
                # try/finally. Previously, cleanup was duplicated across
                # success/failure/exception branches; any new early return
                # could leak a _pending Lock object forever.
                async with self._global_lock:
                    self._pending.pop(content_hash, None)

            return None

    def clear(self) -> None:
        """Clear local cache map (does not delete remote cached contents)."""
        self._cache.clear()
        self._pending.clear()  # R18-FIX: Also clear orphaned per-hash locks


# Module-level Gemini cache manager singleton
_gemini_cache_manager: GeminiCacheManager | None = None


def get_gemini_cache_manager() -> GeminiCacheManager:
    """Get or create the Gemini cache manager singleton."""
    global _gemini_cache_manager
    if _gemini_cache_manager is None:
        _gemini_cache_manager = GeminiCacheManager()
    return _gemini_cache_manager


# ── Model Auto-Selection by Task Size ──────────────────────────────


def select_model_for_generation(estimated_lines: int, task_type: str = "general") -> str:
    """Select the optimal model based on estimated file size and task type.

    Strategy (mode-aware):
    - Security-critical tasks use mode-appropriate security model
    - Small files (<200 lines) → cheapest model in mode
    - Medium files (200-500 lines) → mid-range model in mode
    - Large files (500+ lines) → most capable model in mode

    Args:
        estimated_lines: Estimated line count from estimate_file_complexity().
        task_type: Task type for security override check.

    Returns:
        Model key from MODELS registry.
    """
    mode = get_ai_mode()

    # Security override — mode-appropriate security model
    if task_type in SECURITY_CRITICAL_TASKS:
        return _MODE_SECURITY_MODEL[mode]

    gen_map = _MODE_GENERATION_MAP[mode]
    if estimated_lines < 200:
        return gen_map["small"]
    elif estimated_lines < 500:
        return gen_map["medium"]
    else:
        return gen_map["large"]


# ── Cost Tracking ──────────────────────────────────────────────────


# V5-FIX (CRITICAL-1): Derive per-1K pricing from cost_service's
# authoritative per-million pricing — single source of truth.
# Previously this was a hardcoded dict that used OLD Haiku 3 pricing
# ($0.25/$1.25 per M) for Haiku 4.5 (actual: $0.80/$4.00 per M),
# under-reporting Haiku costs by 3.2×.  By deriving from cost_service,
# pricing stays consistent across the real-time tracker (here) and
# the post-run cost report (cost_service.calculate_pipeline_cost).

# Mapping: ai_router alias → cost_service model key
_ALIAS_TO_COST_KEY: dict[str, str] = {
    "gemini-flash":         "gemini-2.5-flash",
    "haiku":                "claude-haiku-4-5",
    "gemini-pro":           "gemini-2.5-pro",
    "gemini-3-flash":       "gemini-3-flash-preview",
    "sonnet-4.5":           "claude-sonnet-4-5",
    "sonnet":               "claude-sonnet-4-6",
    "gemini-3.1-flash-lite": "gemini-3.1-flash-lite-preview",
    "gemini-3.1-pro":       "gemini-3.1-pro-preview",
    "opus":                 "claude-opus-4-6",
}


def _build_cost_per_1k_tokens() -> dict[str, dict[str, float]]:
    """Derive per-1K-token pricing from cost_service per-million table."""
    try:
        from app.services.cost_service import _ALL_PRICING
    except ImportError:
        logger.warning("cost_service_unavailable_using_fallback_pricing")
        # Hardcoded fallback (should never happen in production)
        return {
            "gemini-flash": {"input": 0.00015, "output": 0.0006},
            "haiku": {"input": 0.0008, "output": 0.004},
            "gemini-pro": {"input": 0.00125, "output": 0.005},
            "gemini-3-flash": {"input": 0.0002, "output": 0.0008},
            "sonnet-4.5": {"input": 0.003, "output": 0.015},
            "sonnet": {"input": 0.003, "output": 0.015},
            "gemini-3.1-flash-lite": {"input": 0.0002, "output": 0.0008},
            "gemini-3.1-pro": {"input": 0.00175, "output": 0.007},
            "opus": {"input": 0.015, "output": 0.075},
        }

    table: dict[str, dict[str, float]] = {}
    for alias, cs_key in _ALIAS_TO_COST_KEY.items():
        per_m = _ALL_PRICING.get(cs_key)
        if per_m:
            table[alias] = {
                "input": per_m["input"] / 1000,
                "output": per_m["output"] / 1000,
            }
        else:
            logger.warning("cost_derivation_miss", alias=alias, cost_key=cs_key)
    return table


_COST_PER_1K_TOKENS: dict[str, dict[str, float]] = _build_cost_per_1k_tokens()


@dataclass
class CostEntry:
    """Single cost entry for an AI call."""

    model_key: str
    agent_name: str
    input_tokens: int
    output_tokens: int
    input_cost: float
    output_cost: float
    total_cost: float
    # M6-FIX: Use wall-clock time for auditing (monotonic is useless for logs)
    timestamp: float = field(default_factory=time.time)


class ProjectCostTracker:
    """Track AI costs per project/pipeline run.

    Tracks:
    - Total tokens (input + output)
    - Total cost (USD)
    - Per-model breakdown
    - Per-agent breakdown

    Thread-safe for async usage (single event loop, no true concurrency).

    MEM-FIX: _entries bounded by deque(maxlen=_MAX_ENTRIES) to prevent OOM
    on long-running pipelines. Aggregates (totals, breakdowns) are maintained
    incrementally so they remain accurate even after entry eviction.
    """

    _MAX_ENTRIES: int = 10_000  # Safety cap for in-memory entries
    # COST-CAP-FIX: Hard cost cap in USD; override via PIPELINE_COST_CAP_USD env var.
    _HARD_CAP_USD: float = float(os.environ.get("PIPELINE_COST_CAP_USD", "50.0"))  # COST-CAP-FIX
    # CHANGE-7: Soft cap ratio for graceful degradation
    _SOFT_CAP_RATIO: float = 0.75  # 75% of hard cap → constrained mode

    def __init__(self, pipeline_run_id: str = "") -> None:
        from collections import deque

        self.pipeline_run_id = pipeline_run_id
        self._entries: deque[CostEntry] = deque(maxlen=self._MAX_ENTRIES)
        self._total_input_tokens: int = 0
        self._total_output_tokens: int = 0
        self._total_cost: float = 0.0
        # DEFERRED-FIX-17: Incremental per-model and per-agent breakdown dicts.
        # Previously, per_model_breakdown() iterated _entries (bounded deque),
        # so after eviction, early entries were lost and breakdowns under-reported.
        # Now we update these dicts on every record() call so they stay accurate
        # regardless of deque eviction.
        self._per_model: dict[str, dict[str, Any]] = {}
        self._per_agent: dict[str, dict[str, Any]] = {}
        # R25-FIX-6: Incremental call_count counter. Previously used
        # len(self._entries) which caps at deque maxlen (10,000).
        self._total_call_count: int = 0
        # 4.10-FIX: asyncio.Lock prevents concurrent record() calls from
        # racing on cost accumulation and exceeding the hard cap. Lazy-init
        # to avoid binding to wrong event loop at import time.
        self._record_lock: asyncio.Lock | None = None

    async def record(
        self,
        response: AIResponse,
        agent_name: str = "unknown",
        model_key: str = "",
    ) -> CostEntry:
        """Record an AI response's cost.

        4.10-FIX: Now async with asyncio.Lock to prevent concurrent calls from
        racing on cost accumulation. Without the lock, two concurrent AI calls
        completing simultaneously could both pass the cap check, allowing total
        cost to exceed _HARD_CAP_USD by up to one call's cost.

        Args:
            response: AIResponse from ai_router.call().
            agent_name: Name of the agent that made the call.
            model_key: Model registry key (e.g. "sonnet", "opus").
                       If empty, attempts to resolve from response.model_used.

        Returns:
            CostEntry with calculated costs.
        """
        # 4.10-FIX: Lazy-init lock (same pattern as CircuitState._get_lock)
        if self._record_lock is None:
            self._record_lock = asyncio.Lock()

        async with self._record_lock:
            # Resolve model key from model_id if not provided
            if not model_key:
                model_key = self._resolve_model_key(response.model_used)

            # M5-FIX: Warn on unknown model instead of silently defaulting to sonnet pricing
            costs = _COST_PER_1K_TOKENS.get(model_key)
            if costs is None:
                logger.warning("unknown_model_cost_fallback", model_key=model_key)
                costs = {"input": 0.003, "output": 0.015}  # sonnet pricing as safe default
            input_cost = (response.input_tokens / 1000) * costs["input"]
            output_cost = (response.output_tokens / 1000) * costs["output"]
            total_cost = input_cost + output_cost

            entry = CostEntry(
                model_key=model_key,
                agent_name=agent_name,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                input_cost=input_cost,
                output_cost=output_cost,
                total_cost=total_cost,
                timestamp=time.time(),
            )

            self._entries.append(entry)
            self._total_input_tokens += response.input_tokens
            self._total_output_tokens += response.output_tokens
            self._total_cost += total_cost
            self._total_call_count += 1

            # COST-CAP-FIX: Enforce hard cost cap per pipeline run.
            if self._total_cost > self._HARD_CAP_USD:  # COST-CAP-FIX
                raise PipelineCostLimitError(
                    f"Pipeline cost {self._total_cost:.2f} exceeded cap {self._HARD_CAP_USD:.2f}"
                )  # COST-CAP-FIX

            # DEFERRED-FIX-17: Update incremental breakdown dicts
            if model_key not in self._per_model:
                self._per_model[model_key] = {
                    "input_tokens": 0, "output_tokens": 0,
                    "total_cost": 0.0, "call_count": 0,
                }
            m = self._per_model[model_key]
            m["input_tokens"] += entry.input_tokens
            m["output_tokens"] += entry.output_tokens
            m["total_cost"] += entry.total_cost
            m["call_count"] += 1

            if agent_name not in self._per_agent:
                self._per_agent[agent_name] = {
                    "input_tokens": 0, "output_tokens": 0,
                    "total_cost": 0.0, "call_count": 0,
                }
            a = self._per_agent[agent_name]
            a["input_tokens"] += entry.input_tokens
            a["output_tokens"] += entry.output_tokens
            a["total_cost"] += entry.total_cost
            a["call_count"] += 1

            return entry

    @staticmethod
    def _resolve_model_key(model_id: str) -> str:
        """Resolve model registry key from model_id string.

        V5-FIX (HIGH-1): Also match partial model_id (prefix) so that
        API responses with truncated or variant suffixes (e.g.,
        ``"gemini-3.1-flash-lite"`` vs ``"gemini-3.1-flash-lite-preview"``)
        still resolve correctly instead of falling back to Sonnet pricing.
        """
        # Exact match first
        for key, spec in MODELS.items():
            if spec.model_id == model_id:
                return key
        # Prefix match (model_id may drop "-preview" suffix)
        for key, spec in MODELS.items():
            if spec.model_id.startswith(model_id) or model_id.startswith(spec.model_id):
                logger.info("cost_tracker_prefix_match", model_id=model_id, matched_key=key)
                return key
        logger.warning("cost_tracker_unknown_model_id", model_id=model_id,
                       hint="Add this model to MODELS registry to avoid Sonnet fallback pricing")
        return "sonnet"  # Default fallback

    def estimate_and_check(self, model_key: str, estimated_input_tokens: int = 4000) -> None:
        """F6-FIX: Pre-flight cost check BEFORE making the AI call.

        Estimates cost from model pricing and raises PipelineCostLimitError
        if the estimated call would push total cost over the hard cap.
        """
        costs = _COST_PER_1K_TOKENS.get(model_key)
        if costs is None:
            costs = {"input": 0.003, "output": 0.015}
        # Estimate: assume output ≈ input tokens (conservative)
        est_input_cost = (estimated_input_tokens / 1000) * costs["input"]
        est_output_cost = (estimated_input_tokens / 1000) * costs["output"]
        est_total = est_input_cost + est_output_cost
        if self._total_cost + est_total > self._HARD_CAP_USD:
            raise PipelineCostLimitError(
                f"Pre-flight check: estimated call cost ${est_total:.3f} "
                f"would push total ${self._total_cost:.2f} over cap "
                f"${self._HARD_CAP_USD:.2f}. Stopping before API call."
            )

    @property
    def total_input_tokens(self) -> int:
        return self._total_input_tokens

    @property
    def total_output_tokens(self) -> int:
        return self._total_output_tokens

    @property
    def total_tokens(self) -> int:
        return self._total_input_tokens + self._total_output_tokens

    @property
    def total_cost(self) -> float:
        return self._total_cost

    @property
    def call_count(self) -> int:
        # R25-FIX-6: Use incremental counter instead of len(deque)
        return self._total_call_count

    # ── CHANGE-7: Budget awareness for graceful degradation ──

    def get_budget_state(self) -> str:
        """Return budget state for pipeline decision-making.

        CHANGE-7: Enables graceful degradation instead of hard crash.
        - "normal": < 75% of cap used — operate normally
        - "constrained": 75-90% — use cheaper models, simplify
        - "critical": 90-100% — skip optional stages, cheapest model only
        """
        if self._HARD_CAP_USD <= 0:
            return "normal"
        ratio = self._total_cost / self._HARD_CAP_USD
        if ratio >= 0.90:
            return "critical"
        elif ratio >= self._SOFT_CAP_RATIO:
            return "constrained"
        return "normal"

    @property
    def remaining_budget_usd(self) -> float:
        """USD remaining before hard cap."""
        return max(0.0, self._HARD_CAP_USD - self._total_cost)

    @property
    def budget_ratio(self) -> float:
        """Fraction of budget consumed (0.0 → 1.0+)."""
        if self._HARD_CAP_USD <= 0:
            return 0.0
        return self._total_cost / self._HARD_CAP_USD

    def per_model_breakdown(self) -> dict[str, dict[str, Any]]:
        """Get cost breakdown per model.

        DEFERRED-FIX-17: Returns from incremental dict instead of iterating
        the bounded deque. Stays accurate even after deque eviction.

        Returns:
            Dict keyed by model_key with tokens, cost, and call count.
        """
        # Return a copy to prevent mutation
        return {k: dict(v) for k, v in self._per_model.items()}

    def per_agent_breakdown(self) -> dict[str, dict[str, Any]]:
        """Get cost breakdown per agent.

        DEFERRED-FIX-17: Returns from incremental dict instead of iterating
        the bounded deque. Stays accurate even after deque eviction.

        Returns:
            Dict keyed by agent_name with tokens, cost, and call count.
        """
        return {k: dict(v) for k, v in self._per_agent.items()}

    def summary(self) -> dict[str, Any]:
        """Full cost summary for this project/pipeline run.

        Returns:
            Dict with total tokens, cost, call count, and breakdowns.
        """
        return {
            "pipeline_run_id": self.pipeline_run_id,
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self._total_cost, 6),
            "call_count": self.call_count,
            "per_model": self.per_model_breakdown(),
            "per_agent": self.per_agent_breakdown(),
        }


# ── Module-level singleton ──────────────────────────────────────────

_router: AIRouter | None = None


def get_ai_router() -> AIRouter:
    """Get or create the AI Router singleton."""
    global _router
    if _router is None:
        _router = AIRouter()
    return _router


async def shutdown_ai_router() -> None:
    """Shutdown the AI Router. Call at app shutdown."""
    global _router
    if _router is not None:
        await _router.close()
        _router = None


def reset_ai_router() -> None:
    """DEFERRED-FIX-11: Reset the AI Router singleton without closing connections.

    Used in tests and when the event loop changes (e.g., Celery worker fork).
    Module-level singletons created in one event loop are invalid in another
    because their asyncio.Lock objects are bound to the original loop.
    Without this, ``asyncio.Lock`` operations raise ``RuntimeError: ... attached
    to a different loop``.
    """
    global _router
    _router = None


def reset_gemini_cache_manager() -> None:
    """DEFERRED-FIX-11: Reset the GeminiCacheManager singleton.

    Same rationale as reset_ai_router(): the manager holds asyncio.Lock
    objects bound to the original event loop.
    """
    global _gemini_cache_manager
    _gemini_cache_manager = None
