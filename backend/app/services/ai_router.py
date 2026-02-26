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
        max_output_tokens=8192,
    ),
    "haiku": ModelSpec(
        model_id="claude-haiku-4-5-20251001",
        provider=Provider.ANTHROPIC,
        display_name="Claude Haiku 4.5",
        cost_tier=1,
        max_output_tokens=8192,
    ),
    # ── Tier 2: Mid-range ──
    "gemini-pro": ModelSpec(
        model_id="gemini-2.5-pro",
        provider=Provider.GOOGLE,
        display_name="Gemini 2.5 Pro",
        cost_tier=2,
        supports_thinking=True,
        max_output_tokens=16384,
    ),
    "gemini-3-flash": ModelSpec(
        model_id="gemini-3-flash-preview",
        provider=Provider.GOOGLE,
        display_name="Gemini 3 Flash Preview",
        cost_tier=2,
        supports_thinking=True,
        max_output_tokens=16384,
    ),
    # ── Tier 3: High-capability ──
    "sonnet-4.5": ModelSpec(
        model_id="claude-sonnet-4-5-20241022",
        provider=Provider.ANTHROPIC,
        display_name="Claude Sonnet 4.5",
        cost_tier=3,
        supports_thinking=True,
        max_output_tokens=16384,
    ),
    "sonnet": ModelSpec(
        model_id="claude-sonnet-4-6-20250514",
        provider=Provider.ANTHROPIC,
        display_name="Claude Sonnet 4.6",
        cost_tier=3,
        supports_thinking=True,
        max_output_tokens=16384,
    ),
    "gemini-3-pro": ModelSpec(
        model_id="gemini-3-pro-preview",
        provider=Provider.GOOGLE,
        display_name="Gemini 3 Pro Preview",
        cost_tier=3,
        supports_thinking=True,
        max_output_tokens=16384,
    ),
    # ── Tier 4: Advanced reasoning ──
    "gemini-3.1-pro": ModelSpec(
        model_id="gemini-3.1-pro-preview",
        provider=Provider.GOOGLE,
        display_name="Gemini 3.1 Pro Preview",
        cost_tier=4,
        supports_thinking=True,
        max_output_tokens=32768,
    ),
    # ── Tier 5: Maximum capability ──
    "opus": ModelSpec(
        model_id="claude-opus-4-6-20250514",
        provider=Provider.ANTHROPIC,
        display_name="Claude Opus 4.6",
        cost_tier=5,
        supports_thinking=True,
        max_output_tokens=32768,
    ),
}

# Escalation chain — index 0 is cheapest, last is most powerful
ESCALATION_CHAIN: list[str] = [
    "gemini-flash",
    "haiku",
    "gemini-pro",
    "gemini-3-flash",
    "sonnet-4.5",
    "sonnet",
    "gemini-3-pro",
    "gemini-3.1-pro",
    "opus",
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


# ── Circuit Breaker ─────────────────────────────────────────────────


@dataclass
class CircuitState:
    """Per-provider circuit breaker state."""

    failures: int = 0
    last_failure_at: float = 0.0
    is_open: bool = False

    # Config
    failure_threshold: int = 3
    reset_timeout_seconds: float = 60.0

    def record_failure(self) -> None:
        self.failures += 1
        self.last_failure_at = time.monotonic()
        if self.failures >= self.failure_threshold:
            self.is_open = True
            logger.warning("circuit_breaker_open", failures=self.failures)

    def record_success(self) -> None:
        self.failures = 0
        self.is_open = False

    def is_available(self) -> bool:
        if not self.is_open:
            return True
        # Check if reset timeout has passed (half-open state)
        elapsed = time.monotonic() - self.last_failure_at
        if elapsed >= self.reset_timeout_seconds:
            return True
        return False


# ── Request / Response Models ───────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AIMessage:
    """Single message in a conversation."""

    role: str  # "user", "assistant", "system"
    content: str


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


# ── AI Router ───────────────────────────────────────────────────────


class AIRouter:
    """Unified multi-model AI client with routing, escalation, and caching.

    Google/Gemini calls support two paths:
      1. Google AI API (public key) -- generativelanguage.googleapis.com
      2. Vertex AI REST (YugNex SA)  -- aiplatform.googleapis.com

    Vertex AI is preferred when use_cloud_secrets=True and YUGNEX_AI_CREDENTIALS
    is available. Falls back to the public API key if Vertex AI is unavailable.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._anthropic_key = settings.anthropic_api_key
        self._google_key = settings.google_ai_api_key

        # Vertex AI dual-project settings
        self._use_vertex = settings.use_cloud_secrets
        self._vertex_project = settings.gcp_ai_project_id  # "yugnex-ai"
        self._vertex_location = "us-central1"

        self._circuits: dict[Provider, CircuitState] = {
            Provider.ANTHROPIC: CircuitState(),
            Provider.GOOGLE: CircuitState(),
        }
        # Shared httpx client -- connection pooling across providers
        self._http: httpx.AsyncClient | None = None

    async def _get_http(self) -> httpx.AsyncClient:
        """Lazy-init shared HTTP client."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))
        return self._http

    async def close(self) -> None:
        """Shutdown HTTP client. Call at app shutdown."""
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()
            self._http = None

    def select_model(self, request: AIRequest) -> ModelSpec:
        """Select the best model for this request.

        Priority:
        1. Explicit model_override
        2. Security-critical tasks → Sonnet 4.6 (AUDIT FIX #17)
        3. Complexity-based routing
        4. Fallback through escalation chain if circuit is open
        """
        # 1. Explicit override
        if request.model_override and request.model_override in MODELS:
            return MODELS[request.model_override]

        # 2. Security override — auth/payments/encryption ALWAYS Sonnet 4.6
        if request.task_type in SECURITY_CRITICAL_TASKS:
            logger.info("security_override", task_type=request.task_type, model="sonnet")
            return MODELS["sonnet"]

        # 3. Complexity routing
        model_key = COMPLEXITY_TO_MODEL.get(request.complexity, "haiku")
        spec = MODELS[model_key]

        # 4. Check circuit breaker — escalate if provider is down
        if not self._circuits[spec.provider].is_available():
            logger.warning("circuit_open_escalating", original=model_key)
            spec = self._find_available_model(model_key)

        return spec

    def _find_available_model(self, failed_key: str) -> ModelSpec:
        """Find next available model in escalation chain."""
        try:
            start_idx = ESCALATION_CHAIN.index(failed_key)
        except ValueError:
            start_idx = 0

        # Try models above the failed one in the chain
        for key in ESCALATION_CHAIN[start_idx + 1:]:
            spec = MODELS[key]
            if self._circuits[spec.provider].is_available():
                logger.info("escalated_to", model=key)
                return spec

        # Try models below as last resort
        for key in ESCALATION_CHAIN[:start_idx]:
            spec = MODELS[key]
            if self._circuits[spec.provider].is_available():
                logger.info("fallback_to", model=key)
                return spec

        # Everything is down — return the original and let it fail with a clear error
        logger.error("all_providers_unavailable")
        return MODELS[failed_key]

    async def call(self, request: AIRequest) -> AIResponse:
        """Make a single AI call with automatic model selection.

        Handles provider selection, API formatting, error handling,
        and circuit breaker updates.
        """
        spec = self.select_model(request)
        start = time.monotonic()
        request_id = str(uuid.uuid4())

        logger.info(
            "ai_call_start",
            model=spec.display_name,
            task_type=request.task_type,
            complexity=request.complexity.value,
            request_id=request_id,
        )

        try:
            if spec.provider == Provider.ANTHROPIC:
                response = await self._call_anthropic(spec, request, request_id)
            else:
                response = await self._call_google(spec, request, request_id)

            response.latency_ms = (time.monotonic() - start) * 1000
            self._circuits[spec.provider].record_success()

            logger.info(
                "ai_call_complete",
                model=spec.display_name,
                latency_ms=round(response.latency_ms, 1),
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                request_id=request_id,
            )
            return response

        except Exception as exc:
            self._circuits[spec.provider].record_failure()
            latency = (time.monotonic() - start) * 1000
            logger.error(
                "ai_call_failed",
                model=spec.display_name,
                error=str(exc),
                latency_ms=round(latency, 1),
                request_id=request_id,
            )
            raise

    async def call_stream(self, request: AIRequest) -> AsyncGenerator[str, None]:
        """Stream AI response as an async generator (for SSE).

        Yields text chunks as they arrive from the provider.
        """
        spec = self.select_model(request)

        if spec.provider == Provider.ANTHROPIC:
            async for chunk in self._stream_anthropic(spec, request):
                yield chunk
        else:
            async for chunk in self._stream_google(spec, request):
                yield chunk

    # ── Anthropic (Claude) ──────────────────────────────────────────

    async def _call_anthropic(
        self, spec: ModelSpec, request: AIRequest, request_id: str
    ) -> AIResponse:
        """Call Claude via Anthropic Messages API."""
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

        # Extract text content and tool calls
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

        usage = data.get("usage", {})
        return AIResponse(
            content="\n".join(text_parts),
            model_used=spec.model_id,
            provider=Provider.ANTHROPIC,
            request_id=request_id,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cached=usage.get("cache_read_input_tokens", 0) > 0,
            tool_calls=tool_calls,
        )

    def _build_anthropic_body(self, spec: ModelSpec, request: AIRequest) -> dict[str, Any]:
        """Build Anthropic API request body."""
        messages = [{"role": m.role, "content": m.content} for m in request.messages if m.role != "system"]

        body: dict[str, Any] = {
            "model": spec.model_id,
            "messages": messages,
            "max_tokens": request.max_tokens or spec.max_output_tokens,
            "temperature": request.temperature,
        }

        # System prompt with optional prompt caching
        if request.system_prompt:
            if request.cache_system_prompt:
                body["system"] = [
                    {
                        "type": "text",
                        "text": request.system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
            else:
                body["system"] = request.system_prompt

        # Extended thinking
        if request.enable_thinking and spec.supports_thinking:
            body["temperature"] = 1.0  # Required for thinking
            body["thinking"] = {"type": "enabled", "budget_tokens": min(10000, body["max_tokens"] // 2)}

        # Tools
        if request.tools:
            body["tools"] = request.tools

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

                    event = orjson.loads(line[6:])
                    if event.get("type") == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            yield delta["text"]

    # ── Google (Gemini) ─────────────────────────────────────────────
    #
    # Two paths:
    #   1. Vertex AI REST (production) -- aiplatform.googleapis.com
    #      Uses YugNex SA token via VertexAICredentialManager.
    #   2. Google AI API (dev/fallback) -- generativelanguage.googleapis.com
    #      Uses public API key.
    #
    # _call_google / _stream_google auto-detect which path to use.

    def _get_vertex_token(self) -> str | None:
        """Get a fresh Vertex AI access token from VertexAICredentialManager.

        Returns None if credentials are unavailable (falls back to public API).
        """
        if not self._use_vertex:
            return None
        mgr = get_vertex_credentials()
        return mgr.get_access_token()

    async def _call_google(
        self, spec: ModelSpec, request: AIRequest, request_id: str
    ) -> AIResponse:
        """Call Gemini via Vertex AI REST or Google AI API (auto-detect)."""
        token = self._get_vertex_token()
        if token:
            return await self._call_vertex(spec, request, request_id, token)
        return await self._call_google_ai(spec, request, request_id)

    async def _call_google_ai(
        self, spec: ModelSpec, request: AIRequest, request_id: str
    ) -> AIResponse:
        """Call Gemini via public Google AI Generative Language API (API key)."""
        http = await self._get_http()
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{spec.model_id}:generateContent?key={self._google_key}"
        )

        body = self._build_google_body(spec, request)

        resp = await http.post(url, json=body)
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
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts)

        usage = data.get("usageMetadata", {})
        return AIResponse(
            content=text,
            model_used=spec.model_id,
            provider=Provider.GOOGLE,
            request_id=request_id,
            input_tokens=usage.get("promptTokenCount", 0),
            output_tokens=usage.get("candidatesTokenCount", 0),
        )

    def _build_google_body(self, spec: ModelSpec, request: AIRequest) -> dict[str, Any]:
        """Build Google Generative Language API request body."""
        contents: list[dict[str, Any]] = []

        for msg in request.messages:
            if msg.role == "system":
                continue
            role = "user" if msg.role == "user" else "model"
            contents.append({"role": role, "parts": [{"text": msg.content}]})

        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": request.max_tokens or spec.max_output_tokens,
                "temperature": request.temperature,
            },
        }

        if request.system_prompt:
            body["systemInstruction"] = {"parts": [{"text": request.system_prompt}]}

        # Thinking (Gemini calls it "thought")
        if request.enable_thinking and spec.supports_thinking:
            body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 8192}

        return body

    async def _stream_google(
        self, spec: ModelSpec, request: AIRequest
    ) -> AsyncGenerator[str, None]:
        """Stream Gemini response (auto-detects Vertex AI vs public API)."""
        token = self._get_vertex_token()
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
            f"{spec.model_id}:streamGenerateContent?alt=sse&key={self._google_key}"
        )

        body = self._build_google_body(spec, request)

        async with http.stream("POST", url, json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    import orjson

                    event = orjson.loads(line[6:])
                    candidates = event.get("candidates", [])
                    if candidates:
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

                    event = orjson.loads(line[6:])
                    candidates = event.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        for part in parts:
                            if "text" in part:
                                yield part["text"]


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
