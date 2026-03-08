"""Agent framework: standalone functions, tool registry, agent result model.

Every NexSidi agent is a standalone class — no base class inheritance.
Each agent must define:
- ``name``: agent identifier (e.g., ``"tilotma"``)
- ``display_name``: human-readable name
- ``default_complexity``: TaskComplexity for model routing
- ``tools``: list of ToolDefinition (via ``_tools`` dict + property)
- ``async execute(pipeline_run_id, context) -> AgentResult``

Shared functionality is provided as standalone module-level functions:
- ``call_ai()``: single AI call through AIRouter
- ``call_ai_with_continuation()``: AI call with auto-continuation for large files
- ``call_ai_with_tools()``: AI call with tool use loop
- ``store_output()``: persist agent output to context_engine
- ``get_step_context()``: retrieve a previous step's output
- ``run_agent()``: timing + logging + error handling wrapper
- ``estimate_file_complexity()``: estimate generated file line count

Design:
- Agents are stateless — all state flows through context_engine
- Each agent receives input from the previous step's context output
- Each agent stores its output in context_engine for the next step
- AI calls go through ai_router (unified Claude + Gemini interface)
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

from app.services.ai_router import (
    AIMessage,
    AIRequest,
    AIResponse,
    ContentBlock,
    TaskComplexity,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

logger = structlog.get_logger(__name__)


# ── Agent Interrupt ────────────────────────────────────────────────


class AgentInterruptRequest(Exception):
    """Raised by an agent's tool handler to request re-run of another agent.

    I1-FIX: Enables dynamic re-dispatch.  When an agent discovers that a
    prior agent's output is insufficient (e.g., Aanya needs a new API
    endpoint from Vikram), the tool handler raises this exception.  The
    pipeline's ``_execute_agent()`` catches it, re-runs the target agent
    with interrupt context, then resumes the requesting agent.

    C2c-FIX: Added ``partial_output`` field so the requesting agent's work
    done before the interrupt is preserved and can be resumed from.
    Limit is now configurable via ``settings.max_interrupts_per_pair``.
    """

    def __init__(
        self,
        requesting_agent: str,
        target_agent: str,
        reason: str,
        required_changes: str,
        partial_output: dict | None = None,
    ) -> None:
        self.requesting_agent = requesting_agent
        self.target_agent = target_agent
        self.reason = reason
        self.required_changes = required_changes
        self.partial_output = partial_output  # Work done before interrupt
        super().__init__(
            f"{requesting_agent} requests {target_agent} re-run: {reason}"
        )


# ── Agent Status ────────────────────────────────────────────────────


class AgentStatus(str, Enum):
    """Lifecycle status of an agent execution."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    WAITING_USER = "waiting_user"  # Paused at checkpoint


# ── Agent Result ────────────────────────────────────────────────────


@dataclass(slots=True)
class AgentResult:
    """Output of a single agent execution."""

    agent_name: str
    status: AgentStatus
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    execution_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: float = 0.0
    completed_at: float = 0.0
    model_used: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    # D1-FIX: Agent-directed routing — optional next-stage suggestion.
    # Agents can suggest a specific next stage (PipelineStage.value string).
    # The pipeline orchestrator validates the suggestion against an allowed
    # transitions whitelist before applying it.  If None, standard sequential
    # routing is used.  This enables DCG-style routing without breaking the
    # existing linear fallback.
    route_to: str | None = None
    route_reason: str | None = None

    @property
    def duration_ms(self) -> float:
        if self.completed_at and self.started_at:
            return (self.completed_at - self.started_at) * 1000
        return 0.0


# ── Shared Utilities ────────────────────────────────────────────────


def clamp_completeness(value: Any) -> int:
    """Clamp completeness_pct to valid 0-100 range.

    COMPLETENESS-FIX: LLM self-evaluation can return values outside 0-100
    (e.g., -1, 150, "high"). This utility ensures a valid percentage.
    The default of -1 in some agents was itself invalid.
    """
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return 0


def build_rejection_context(context: dict) -> str:
    """Build a prompt section from __rejected_approaches__ for generator agents.

    FIX-39: Returns a string that can be appended to any agent's system prompt
    to prevent repeating user-rejected approaches. Returns empty string if
    no rejections exist.
    """
    rejected = context.get("__rejected_approaches__", [])
    if not rejected:
        return ""
    lines = [
        "\n## USER REJECTIONS — DO NOT USE THESE APPROACHES",
        "The user has explicitly rejected the following. You MUST NOT repeat them:",
    ]
    for r in rejected:
        lines.append(f"- Stage '{r.get('stage', '?')}': {r.get('feedback', 'no details')}")
    lines.append("")
    return "\n".join(lines)


# ── Tool Definition ─────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """A tool available to an agent during AI execution."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for parameters

    def to_anthropic_format(self) -> dict[str, Any]:
        """Convert to Anthropic tool use format."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


# I1-FIX: Interrupt tool — allows build agents (Shubham, Aanya) to
# request re-execution of a prior agent when they discover its output
# is insufficient (e.g., missing API endpoint, wrong schema).
INTERRUPT_TOOL = ToolDefinition(
    name="request_agent_rerun",
    description=(
        "Request another agent to re-run and produce updated output. "
        "Use ONLY when you discover that a prior agent's output is "
        "genuinely insufficient and you need structural changes — e.g., "
        "a new API endpoint, updated database schema. Do NOT use for "
        "questions (use ask_architect/ask_backend instead). "
        "Max 2 requests per agent pair per pipeline run."
    ),
    parameters={
        "type": "object",
        "properties": {
            "target_agent": {
                "type": "string",
                "enum": ["vikram", "shubham", "aanya", "dhruv"],
                "description": "Name of the agent whose output needs updating",
            },
            "reason": {
                "type": "string",
                "description": "Why the re-run is needed (what is missing/wrong)",
            },
            "required_changes": {
                "type": "string",
                "description": "Specific changes needed in the target agent's output",
            },
        },
        "required": ["target_agent", "reason", "required_changes"],
    },
)


# ── Model Override Resolution ─────────────────────────────────────

# Map raw model IDs to MODELS registry keys (fixes agent bug where
# agents set default_model to "claude-sonnet-4-6" instead of "sonnet")
_MODEL_ID_TO_KEY: dict[str, str] = {
    "claude-sonnet-4-6": "sonnet",
    "claude-sonnet-4-5": "sonnet-4.5",
    "claude-haiku-4-5": "haiku",
    "claude-opus-4-6": "opus",
    "gemini-2.5-pro": "gemini-pro",
    "gemini-2.5-flash": "gemini-flash",
    "gemini-3-flash-preview": "gemini-3-flash",
    "gemini-3.1-flash-lite-preview": "gemini-3.1-flash-lite",
    "gemini-3.1-pro-preview": "gemini-3.1-pro",
}

# Remap targets when an agent's default model doesn't match the mode
_CLAUDE_TO_GEMINI: dict[str, str] = {
    "haiku": "gemini-flash",
    "sonnet-4.5": "gemini-3.1-flash-lite",
    "sonnet": "gemini-3.1-flash-lite",
    "opus": "gemini-3.1-pro",
}
# M4-FIX: gemini-pro is tier 2, maps to sonnet-4.5 (tier 3) not haiku (tier 1)
_GEMINI_TO_CLAUDE: dict[str, str] = {
    "gemini-flash": "haiku",          # tier 1 → tier 1
    "gemini-pro": "sonnet-4.5",       # tier 2 → tier 3 (was haiku — wrong tier)
    "gemini-3-flash": "haiku",            # tier 2 → tier 1 (budget model)
    "gemini-3.1-flash-lite": "sonnet",   # tier 3 → tier 3
    "gemini-3.1-pro": "opus",         # tier 4 → tier 5
}


def resolve_model_override(agent_default_model: str | None) -> str | None:
    """Resolve an agent's default_model for the current AI provider mode.

    - Returns ``None`` when the agent has no override (uses complexity routing).
    - Fixes the model-ID-vs-registry-key bug (``"claude-sonnet-4-6"`` → ``"sonnet"``).
    - In ``"gemini"`` mode, remaps Claude keys to their Gemini equivalents.
    - In ``"claude"`` mode, remaps Gemini keys to their Claude equivalents.
    - In ``"mixed"`` mode, returns the (corrected) key unchanged.
    """
    if agent_default_model is None:
        return None

    from app.services.ai_router import MODELS, Provider, get_ai_mode

    # Fix model ID → registry key
    key = _MODEL_ID_TO_KEY.get(agent_default_model, agent_default_model)

    # Validate key exists in registry
    if key not in MODELS:
        return None

    mode = get_ai_mode()
    if mode == "mixed":
        return key

    spec = MODELS[key]
    if mode == "gemini" and spec.provider == Provider.ANTHROPIC:
        return _CLAUDE_TO_GEMINI.get(key, "gemini-3.1-flash-lite")
    if mode == "claude" and spec.provider == Provider.GOOGLE:
        return _GEMINI_TO_CLAUDE.get(key, "sonnet")

    return key


# ── Standalone Agent Functions ──────────────────────────────────────
#
# These replace the old BaseAgent methods.  Each function accepts an
# ``agent`` object (any class with ``name``, ``default_complexity``,
# ``default_model``, and ``tools`` attributes) as its first argument.
# ────────────────────────────────────────────────────────────────────


async def run_agent(
    agent: Any,
    pipeline_run_id: str,
    context: dict[str, Any],
) -> AgentResult:
    """Wrapper around agent.execute() with timing, logging, error handling.

    This is what the pipeline orchestrator calls.  It delegates to
    the agent's ``execute()`` method.

    ``agent`` must have ``.name`` and an async ``execute(pipeline_run_id, context)``
    method that returns ``AgentResult``.
    """
    execution_id = str(uuid.uuid4())
    started_at = time.monotonic()

    logger.info(
        "agent_start",
        agent=agent.name,
        pipeline_run_id=pipeline_run_id,
        execution_id=execution_id,
    )

    try:
        result = await agent.execute(pipeline_run_id, context)
        result.execution_id = execution_id
        result.started_at = started_at
        result.completed_at = time.monotonic()

        logger.info(
            "agent_complete",
            agent=agent.name,
            status=result.status.value,
            duration_ms=round(result.duration_ms, 1),
            pipeline_run_id=pipeline_run_id,
        )
        return result

    except Exception as exc:
        completed_at = time.monotonic()
        # R11-FIX: Sanitize exception before logging/storing.
        # httpx.HTTPStatusError can contain full request headers
        # including Authorization: Bearer <token> or x-api-key.
        from app.services.ai_router import _sanitize_error
        safe_error = _sanitize_error(exc)
        logger.error(
            "agent_failed",
            agent=agent.name,
            error=safe_error,
            pipeline_run_id=pipeline_run_id,
        )
        # CHANGE-20: Build structured failure report instead of opaque error.
        failure_report = _build_failure_report(agent.name, exc)
        return AgentResult(
            agent_name=agent.name,
            status=AgentStatus.FAILED,
            error=safe_error,
            execution_id=execution_id,
            started_at=started_at,
            completed_at=completed_at,
            output={"failure_report": failure_report},
        )


def _build_failure_report(agent_name: str, exc: Exception) -> dict[str, Any]:
    """CHANGE-20: Structured failure report with diagnostics and suggestions.

    Instead of returning an opaque error string when an agent crashes, this
    extracts the error type, relevant stack frames, heuristic suggestions,
    and whether a retry is viable. Pipeline and users get actionable info.
    """
    import traceback

    tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
    # Only keep frames from our app code (most relevant)
    relevant_frames = [line.strip() for line in tb_lines if "app/" in line][-3:]

    report: dict[str, Any] = {
        "agent": agent_name,
        "error_type": type(exc).__name__,
        "error_message": str(exc)[:500],
        "relevant_frames": relevant_frames,
        "retry_viable": True,
    }

    # Heuristic suggestions based on error type
    msg = str(exc).lower()
    exc_type = type(exc).__name__

    if "nonetype" in msg and ("get" in msg or "attribute" in msg):
        report["suggestion"] = (
            "Upstream agent likely returned None instead of a dict. "
            "Check input context from prior stage."
        )
        report["root_cause_hint"] = "upstream"
    elif "429" in msg or "rate" in msg:
        report["suggestion"] = "API rate limit hit. Retry is likely to succeed."
        report["retry_viable"] = True
    elif exc_type == "KeyError":
        report["suggestion"] = (
            f"Missing key in input data: {exc}. "
            "Verify upstream agent output schema matches expectations."
        )
        report["root_cause_hint"] = "upstream"
    elif exc_type == "TypeError" and "argument" in msg:
        report["suggestion"] = (
            "Function call with wrong argument types. "
            "Check if upstream contract schema changed."
        )
        report["root_cause_hint"] = "upstream"
    elif "timeout" in msg or "timed out" in msg:
        report["suggestion"] = "Request timed out. Retry with longer timeout or simpler input."
    else:
        report["suggestion"] = f"Unexpected {exc_type}. Check agent logs for details."
        report["retry_viable"] = not isinstance(exc, (KeyError, AttributeError))

    return report


# ── File Complexity Estimation ─────────────────────────────────────


def estimate_file_complexity(
    contract: dict[str, Any],
    step_name: str,
) -> int:
    """Estimate the complexity of a generation step (line count estimate).

    Uses contract metadata (tables, endpoints, relationships) to estimate
    how large the generated file will be.  This drives:
    - Model selection (small files → cheap model, huge files → Opus)
    - File splitting (>500 estimated lines → split into sub-steps)

    Returns:
        Estimated line count for the generated file.
    """
    # R8-FIX: Vikram's contract may nest these under "database" and "api"
    # sub-objects (e.g. contract["database"]["tables"]), or have them at
    # the top level. Try nested first, then fall back to top-level.
    # Guard: "database"/"api" may be strings in some contracts (e.g. "postgresql").
    db_section = contract.get("database")
    api_section = contract.get("api")
    if isinstance(db_section, dict):
        tables = db_section.get("tables", contract.get("tables", []))
        relationships = db_section.get("relationships", contract.get("relationships", []))
    else:
        tables = contract.get("tables", [])
        relationships = contract.get("relationships", [])
    if isinstance(api_section, dict):
        endpoints = api_section.get("endpoints", contract.get("endpoints", []))
    else:
        endpoints = contract.get("endpoints", [])
    features = contract.get("features", [])

    table_count = len(tables) if isinstance(tables, list) else 0
    endpoint_count = len(endpoints) if isinstance(endpoints, list) else 0
    relationship_count = len(relationships) if isinstance(relationships, list) else 0
    feature_count = len(features) if isinstance(features, list) else 0

    # Base estimates per step type
    _STEP_BASE_LINES: dict[str, int] = {
        "models": 30,       # imports + Base class
        "schemas": 20,      # imports
        "serializers": 20,  # imports
        "db_schema": 25,    # imports + config
        "types": 20,        # imports
        "security": 80,     # auth is always ~80-120 lines
        "permissions": 60,
        "middleware": 80,
        "routers": 30,      # imports + router setup
        "routes": 30,
        "views": 30,
        "urls": 15,
        "services": 25,
        "tests": 40,        # imports + fixtures
        "seed_db": 40,
        "seed": 40,
        "management": 30,
    }

    base = _STEP_BASE_LINES.get(step_name, 50)

    # Per-table/endpoint multipliers
    _STEP_MULTIPLIERS: dict[str, dict[str, int]] = {
        "models": {"table": 25, "relationship": 5},
        "schemas": {"table": 20, "endpoint": 3},
        "serializers": {"table": 20, "endpoint": 3},
        "db_schema": {"table": 30, "relationship": 8},
        "types": {"table": 15, "endpoint": 5},
        "routers": {"endpoint": 20, "table": 5},
        "routes": {"endpoint": 20, "table": 5},
        "views": {"endpoint": 20, "table": 5},
        "urls": {"endpoint": 2, "table": 1},
        "services": {"endpoint": 15, "table": 10},
        "tests": {"endpoint": 25, "table": 10},
        "seed_db": {"table": 15},
        "seed": {"table": 15},
    }

    multipliers = _STEP_MULTIPLIERS.get(step_name, {})
    estimate = base
    estimate += multipliers.get("table", 0) * table_count
    estimate += multipliers.get("endpoint", 0) * endpoint_count
    estimate += multipliers.get("relationship", 0) * relationship_count
    estimate += multipliers.get("feature", 0) * feature_count

    return max(estimate, base)


# ── AI Call Helpers ─────────────────────────────────────────────────


_AI_MAX_RETRIES = 3
_AI_BACKOFF_BASE = 2.0


def _is_retryable_error(exc: Exception) -> bool:
    """Check if an AI call error is transient (500, 503, 429, timeout)."""
    error_str = str(exc).lower()
    return any(x in error_str for x in ["500", "503", "529", "429", "timeout", "overloaded", "rate_limit"])


async def call_ai(
    agent: Any,
    messages: list[dict[str, str]],
    system_prompt: str | None = None,
    task_type: str = "general",
    complexity: TaskComplexity | None = None,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    enable_thinking: bool = False,
    shared_context: Any | None = None,
    dynamic_system_context: str | None = None,  # CACHE-FIX: dynamic part of system prompt
) -> AIResponse:
    """Make an AI call through the AI Router with automatic retry on transient errors.

    FIX-18: Retries up to 3 times with exponential backoff for 500/503/429/timeout.
    ``agent`` must have ``.default_complexity`` and ``.default_model`` attributes.
    ``shared_context`` is an optional :class:`SharedContext` for prompt caching.
    ``dynamic_system_context`` is appended AFTER the cached system_prompt block
    without cache_control, so it doesn't invalidate the stable prompt cache.
    """
    import asyncio as _asyncio

    from app.services.ai_router import get_ai_router

    router = get_ai_router()
    ai_messages = [AIMessage(role=m["role"], content=m["content"]) for m in messages]

    request = AIRequest(
        messages=ai_messages,
        system_prompt=system_prompt,
        task_type=task_type,
        complexity=complexity or agent.default_complexity,
        model_override=resolve_model_override(agent.default_model),
        temperature=temperature,
        max_tokens=max_tokens,
        enable_thinking=enable_thinking,
        shared_context=shared_context,
        dynamic_system_context=dynamic_system_context,  # CACHE-FIX
    )

    # FIX-18: Retry with exponential backoff for transient errors
    for attempt in range(_AI_MAX_RETRIES):
        try:
            return await router.call(request)
        except Exception as exc:
            if attempt < _AI_MAX_RETRIES - 1 and _is_retryable_error(exc):
                wait = _AI_BACKOFF_BASE ** attempt
                logger.warning(
                    "ai_call_retry",
                    agent=getattr(agent, "name", "unknown"),
                    attempt=attempt + 1,
                    wait_seconds=wait,
                    error=str(exc)[:100],
                )
                await _asyncio.sleep(wait)
            else:
                raise
    # Should never reach here, but satisfy type checker
    raise RuntimeError("AI call retries exhausted")


async def call_ai_with_continuation(
    agent: Any,
    messages: list[dict[str, str]],
    system_prompt: str | None = None,
    task_type: str = "general",
    complexity: TaskComplexity | None = None,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    max_continuations: int = 5,
    shared_context: Any | None = None,
    enable_thinking: bool = False,  # M3-FIX: Forward enable_thinking to call_ai
    dynamic_system_context: str | None = None,  # CACHE-FIX: forwarded to call_ai
) -> AIResponse:
    """AI call with automatic continuation if response is truncated.

    ``agent`` must have ``.name``, ``.default_complexity``, and ``.default_model``.

    When the model hits max_tokens and the response is cut off mid-code:
    1. Detects truncation via ``response.was_truncated``
    2. Sends a continuation prompt asking the model to pick up from
       where it stopped (without repeating already-generated code)
    3. Appends the new content to the accumulated response
    4. Repeats up to ``max_continuations`` times
    5. Logs a warning if still truncated after all attempts
    """
    full_content = ""
    total_output_tokens = 0
    total_latency_ms: float = 0.0  # R29-FIX-7: Accumulate latency across continuations
    total_input_tokens = 0
    continuation = 0
    current_messages = messages

    while True:
        response = await call_ai(
            agent,
            messages=current_messages,
            system_prompt=system_prompt,
            task_type=task_type,
            complexity=complexity,
            temperature=temperature,
            max_tokens=max_tokens,
            shared_context=shared_context,
            enable_thinking=enable_thinking,
            dynamic_system_context=dynamic_system_context,  # CACHE-FIX
        )

        # 4.3-FIX: Strip markdown code fences from continuation responses.
        # LLMs often prefix continuations with ```python\n and suffix with \n```,
        # injecting invalid syntax into the middle of generated code.
        _chunk = response.content
        if continuation > 0:
            import re as _re
            _chunk = _re.sub(r'^```\w*\n?', '', _chunk)
            _chunk = _re.sub(r'\n?```\s*$', '', _chunk)
        full_content += _chunk
        total_output_tokens += response.output_tokens
        total_input_tokens += response.input_tokens
        total_latency_ms += response.latency_ms  # R29-FIX-7

        if not response.was_truncated or continuation >= max_continuations:
            if response.was_truncated:
                logger.warning(
                    "max_continuations_reached",
                    agent=agent.name,
                    continuations=continuation,
                    total_output_tokens=total_output_tokens,
                )
            break

        continuation += 1
        logger.info(
            "continuation_needed",
            agent=agent.name,
            continuation=continuation,
            output_tokens_so_far=total_output_tokens,
        )

        # R9-FIX: Keep ALL original messages, not just messages[0].
        # The original code dropped multi-turn context (e.g., user prompt,
        # assistant prefill, follow-up user instructions). If messages[0]
        # was a system role, the continuation had no user message at all,
        # causing Anthropic API to reject the request.
        #
        # R10-FIX: Only send the TAIL of accumulated output to prevent
        # context window blowout. With max_continuations=5, accumulated
        # output could grow to 300K+ tokens — exceeding the model's
        # context window and causing API rejection.
        _MAX_CONTINUATION_CONTEXT_CHARS = 8000
        tail = full_content[-_MAX_CONTINUATION_CONTEXT_CHARS:] if len(full_content) > _MAX_CONTINUATION_CONTEXT_CHARS else full_content
        current_messages = list(messages) + [
            {"role": "assistant", "content": tail},
            {"role": "user", "content": (
                "Your response was cut off. Continue EXACTLY from where you stopped. "
                "Do NOT repeat any code already generated above. "
                "Do NOT add any explanation — just continue the code."
            )},
        ]

    # Return a merged response with totals
    return AIResponse(
        content=full_content,
        model_used=response.model_used,
        provider=response.provider,
        request_id=response.request_id,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        latency_ms=total_latency_ms,  # R29-FIX-7: Total across all continuations
        cached=response.cached,
        stop_reason=response.stop_reason,
        was_truncated=response.was_truncated,
    )


@dataclass
class Observation:
    """Result of the system observing a tool execution.

    PHASE-G: The system (not the LLM) evaluates every tool result and
    decides whether to proceed, retry, or escalate.
    """
    proceed: bool = True        # Send result to LLM as normal
    needs_escalation: bool = False  # Switch to more expensive model
    override_result: str | None = None  # Replace tool result with this


async def call_ai_with_tools(
    agent: Any,
    messages: list[dict[str, str]],
    system_prompt: str | None = None,
    task_type: str = "general",
    complexity: TaskComplexity | None = None,
    tool_handler: Any = None,
    max_tool_rounds: int = 10,
) -> AIResponse:
    """Make an AI call with observe→decide loop.

    PHASE-G: After every tool execution, the SYSTEM evaluates the result
    via tool_handler.observe() (if available). This allows the system to:
    - Override tool results (e.g., inject verification warnings)
    - Escalate to more expensive models mid-loop
    - Make decisions independent of the LLM

    ``agent`` must have ``.default_complexity``, ``.default_model``, and ``.tools``.
    """
    from app.services.ai_router import get_ai_router

    router = get_ai_router()
    ai_messages = [AIMessage(role=m["role"], content=m["content"]) for m in messages]
    tool_defs = [t.to_anthropic_format() for t in agent.tools] if agent.tools else None

    request = AIRequest(
        messages=ai_messages,
        system_prompt=system_prompt,
        task_type=task_type,
        complexity=complexity or agent.default_complexity,
        model_override=resolve_model_override(agent.default_model),
        tools=tool_defs,
    )

    response = await router.call(request)

    # Tool use loop
    #
    # H10-FIX: Anthropic tool use requires a specific message flow:
    #   1. assistant message with tool_use content blocks
    #   2. user message with tool_result content blocks (one per tool call)
    #
    # PHASE-5: Uses proper ContentBlock types instead of pseudo-XML encoding.
    # Old approach: f'[tool_use id="{id}" name="{name}"]' (fragile string parsing)
    # New approach: ToolUseBlock(id=id, name=name, input=input) (typed, Anthropic-native)
    # AUDIT-FIX: Moved constant out of loop body (was redefined every iteration).
    _MAX_HISTORY_MESSAGES = 13  # 1 initial + 6 rounds × 2
    _KEEP_ROUNDS = 6  # 6 rounds = 12 messages

    rounds = 0
    # CHANGE-16: Stall detection — track tool call fingerprints to detect
    # when the LLM is stuck calling the same tool with identical arguments.
    # Saves 5-15 wasted rounds ($0.10-0.50) per stuck agent.
    import hashlib as _hashlib_stall
    _tool_fingerprints: list[str] = []
    _stall_warnings = 0
    _MAX_STALL_WARNINGS = 2  # Force-break after 2 stall warnings

    while response.tool_calls and rounds < max_tool_rounds:
        rounds += 1

        # PHASE-5: Build structured assistant message with ContentBlock types
        assistant_blocks: list[ContentBlock] = []
        if response.content:
            assistant_blocks.append(TextBlock(text=response.content))
        for tc in response.tool_calls:
            assistant_blocks.append(ToolUseBlock(
                id=tc["id"],
                name=tc["name"],
                input=tc["input"],
            ))
        ai_messages.append(AIMessage(
            role="assistant",
            content=assistant_blocks,
        ))

        # Process each tool call and build structured tool results
        import json as _json
        result_blocks: list[ContentBlock] = []
        for tc in response.tool_calls:
            if tool_handler is None:
                logger.warning("no_tool_handler", tool=tc["name"])
                result_blocks.append(ToolResultBlock(
                    tool_use_id=tc["id"],
                    content="Error: no tool handler configured",
                    is_error=True,
                ))
                continue

            # CHANGE-17: Pre-validate tool arguments against JSON Schema.
            # Catches invalid types and missing required fields instantly
            # (zero latency, zero tokens) instead of burning a round on
            # runtime exceptions.
            _schema = None
            for _td in (tool_defs or []):
                if _td.get("name") == tc["name"]:
                    _schema = _td.get("input_schema", {})
                    break
            if _schema:
                _TYPE_MAP = {
                    "string": str, "integer": int, "number": (int, float),
                    "boolean": bool, "array": list, "object": dict,
                }
                _val_errors: list[str] = []
                _props = _schema.get("properties", {})
                _required = _schema.get("required", [])
                _tool_input = tc.get("input", {})
                if isinstance(_tool_input, dict):
                    for _rf in _required:
                        if _rf not in _tool_input:
                            _val_errors.append(f"'{_rf}' is required but missing")
                    for _k, _v in _tool_input.items():
                        if _k in _props:
                            _expected = _props[_k].get("type")
                            if _expected and _expected in _TYPE_MAP:
                                if not isinstance(_v, _TYPE_MAP[_expected]):
                                    _val_errors.append(
                                        f"'{_k}' must be {_expected}, "
                                        f"got {type(_v).__name__}"
                                    )
                if _val_errors:
                    result_blocks.append(ToolResultBlock(
                        tool_use_id=tc["id"],
                        content=(
                            f"Tool argument error for '{tc['name']}': "
                            + "; ".join(_val_errors)
                        ),
                        is_error=True,
                    ))
                    continue

            try:
                tool_result = await tool_handler(tc["name"], tc["input"])
                result_str = _json.dumps(tool_result) if not isinstance(tool_result, str) else tool_result
                # R16-FIX: Truncate large tool results to prevent OOM and
                # quadratic token cost growth. Each round accumulates ALL
                # previous results, so large results compound dangerously.
                _MAX_TOOL_RESULT_CHARS = 50_000
                if len(result_str) > _MAX_TOOL_RESULT_CHARS:
                    result_str = result_str[:_MAX_TOOL_RESULT_CHARS] + "\n... [truncated]"

                # PHASE-G: System observes tool result and can override/escalate.
                # This is the core agentic mechanism: the SYSTEM (not LLM) evaluates
                # every tool result and makes autonomous decisions.
                if hasattr(tool_handler, 'observe'):
                    observation = tool_handler.observe(tc["name"], result_str)
                    if observation.override_result is not None:
                        result_str = observation.override_result
                    if observation.needs_escalation:
                        # Switch to more expensive model for remaining rounds
                        request = AIRequest(
                            messages=request.messages if hasattr(request, 'messages') else ai_messages,
                            system_prompt=system_prompt,
                            task_type=task_type,
                            complexity=TaskComplexity.HIGH,
                            model_override=resolve_model_override(agent.default_model),
                            tools=tool_defs,
                        )
                        logger.info("tool_loop_escalated", tool=tc["name"], reason="observation")
            except Exception as exc:
                # R11-FIX: Sanitize tool exception — may contain credentials
                # from downstream HTTP calls (httpx error messages include headers).
                from app.services.ai_router import _sanitize_error
                safe_err = _sanitize_error(exc)
                logger.warning("tool_execution_failed", tool=tc["name"], error=safe_err)
                result_str = f"Error executing tool: {safe_err}"
                result_blocks.append(ToolResultBlock(
                    tool_use_id=tc["id"],
                    content=result_str,
                    is_error=True,
                ))
                continue

            result_blocks.append(ToolResultBlock(
                tool_use_id=tc["id"],
                content=result_str,
            ))

        # PHASE-5: Add tool results as structured user message
        ai_messages.append(AIMessage(
            role="user",
            content=result_blocks,
        ))

        # AUDIT-FIX: Early exit if tool handler signals completion.
        # FixerToolHandler uses _complete, Shubham/AanyaToolHandler use _done.
        # Check both attributes so early exit works for ALL agentic agents.
        # Without this, the loop continues calling the AI after task_complete,
        # wasting tokens and risking the model undoing successful work.
        if tool_handler is not None and (
            getattr(tool_handler, '_complete', False)
            or getattr(tool_handler, '_done', False)
        ):
            break

        # CHANGE-16: Stall detection — check if agent is stuck in a loop
        for tc in response.tool_calls:
            _inp = tc.get("input", {})
            _keys = sorted(_inp.keys()) if isinstance(_inp, dict) else []
            _fp = _hashlib_stall.md5(
                f"{tc['name']}:{_keys}".encode()
            ).hexdigest()[:12]
            _tool_fingerprints.append(_fp)

        if len(_tool_fingerprints) >= 3:
            from collections import Counter as _Counter
            _top_fp, _top_count = _Counter(_tool_fingerprints[-5:]).most_common(1)[0]
            if _top_count >= 3:
                _stall_warnings += 1
                _stall_tool = response.tool_calls[0]["name"] if response.tool_calls else "unknown"
                if _stall_warnings >= _MAX_STALL_WARNINGS:
                    logger.warning(
                        "tool_loop_force_break",
                        rounds=rounds,
                        stall_tool=_stall_tool,
                        warnings=_stall_warnings,
                    )
                    break
                # Inject stall warning into the next round's messages
                logger.warning(
                    "tool_loop_stall_detected",
                    rounds=rounds,
                    stall_tool=_stall_tool,
                    warning_num=_stall_warnings,
                )
                ai_messages.append(AIMessage(
                    role="user",
                    content=(
                        f"STALL DETECTED: You have called '{_stall_tool}' with "
                        f"similar arguments {_top_count} times in the last 5 rounds. "
                        "You MUST take a DIFFERENT action — change your approach, "
                        "try a different file, or call task_complete if done."
                    ),
                ))

        # DEFERRED-FIX-2: Sliding window to prevent unbounded message history.
        # Each round adds 2 messages (assistant tool_use + user tool_result).
        # By round 10, we'd send 22 messages with up to 50KB each = 1MB+.
        if len(ai_messages) > _MAX_HISTORY_MESSAGES:
            # R25-FIX-5: Ensure message alternation after truncation.
            # Anthropic requires strict user/assistant alternation. Naive
            # truncation can produce two consecutive "user" messages (the
            # original prompt + a tool_result that starts the tail). We must
            # ensure the tail starts with an "assistant" message.
            #
            # R36-FIX: Ensure we keep COMPLETE rounds (assistant + user pairs).
            # Previously, naive tail extraction could split a round, dropping
            # the assistant tool_use but keeping its orphaned tool_result,
            # which causes Anthropic API errors. We now compute the tail as
            # an even number of messages (complete rounds) and ensure the
            # tail starts with an assistant message.
            tail = ai_messages[-(_KEEP_ROUNDS * 2):]
            # If tail starts with "user" (tool_result), drop it to maintain
            # alternation: first_msg (user) must be followed by assistant.
            if tail and tail[0].role == "user":
                tail = tail[1:]
            # R36-FIX: Guard against empty tail after truncation. If all
            # recent messages were user-role (edge case with tool results),
            # keep at least the last 2 messages to avoid sending the AI
            # only the initial prompt with no context about recent rounds.
            if not tail:
                tail = ai_messages[-2:]
            ai_messages = [ai_messages[0]] + tail

        # Call AI again with tool results
        request = AIRequest(
            messages=ai_messages,
            system_prompt=system_prompt,
            task_type=task_type,
            complexity=complexity or agent.default_complexity,
            model_override=resolve_model_override(agent.default_model),
            tools=tool_defs,
        )
        response = await router.call(request)

    return response


# ── Context Helpers ─────────────────────────────────────────────────


def _sanitize_inter_agent_value(value: Any, _depth: int = 0) -> Any:
    """Sanitize output values to prevent inter-agent prompt injection.

    Strips potential prompt injection markers from string values that will
    become another agent's input. Same neutralization as prompt_engine.py.
    Max recursion depth of 3 to avoid performance issues on deep dicts.
    """
    if _depth > 3:
        return value
    if isinstance(value, str):
        # Neutralize fake role markers and conversation turn indicators
        for marker in ("[SYSTEM]", "[INST]", "[END]", "[STOP]", "System:", "Human:", "User:", "Assistant:"):
            if marker in value:
                safe = marker.replace("[", "(").replace("]", ")").replace(":", " -")
                value = value.replace(marker, safe)
        return value
    if isinstance(value, dict):
        return {k: _sanitize_inter_agent_value(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_inter_agent_value(v, _depth + 1) for v in value]
    return value


async def store_output(
    agent: Any,
    pipeline_run_id: str,
    output: dict[str, Any],
) -> None:
    """Store an agent's output in the context engine.

    ``agent`` must have ``.name`` attribute.
    Gracefully skips if the context engine is not initialized (e.g. in tests).

    Inter-agent sanitization: neutralizes prompt injection markers in output
    values so a compromised agent can't inject instructions into downstream agents.
    """
    # Sanitize output before storing (inter-agent prompt injection defense)
    output = _sanitize_inter_agent_value(output)

    from app.services.context_engine import get_context_engine

    try:
        engine = get_context_engine()
    except RuntimeError:
        logger.debug("store_output_skipped", agent=agent.name, reason="context_engine_not_initialized")
        return

    try:
        await engine.store(pipeline_run_id, agent.name, output)
    except Exception as exc:
        # A-1-FIX: Never let a transient Valkey/Redis failure crash the agent.
        # R28-FIX-8: Sanitize error — Redis connection strings can contain passwords.
        from app.services.ai_router import _sanitize_error
        logger.warning(
            "store_output_failed",
            agent=agent.name,
            pipeline_run_id=pipeline_run_id,
            error=_sanitize_error(exc),
        )


async def get_step_context(
    pipeline_run_id: str,
    step_name: str,
) -> dict[str, Any] | None:
    """Retrieve a specific step's output from context.

    Returns None if the context engine is not initialized or if the step
    has not been stored yet.
    """
    from app.services.context_engine import get_context_engine

    try:
        engine = get_context_engine()
    except RuntimeError:
        logger.debug("get_step_context_skipped", step=step_name, reason="context_engine_not_initialized")
        return None

    try:
        return await engine.get_step(pipeline_run_id, step_name)
    except Exception as exc:
        # A-2-FIX: Don't crash agent if Valkey is temporarily unavailable.
        # R28-FIX-8: Sanitize error — Redis URLs may contain credentials.
        from app.services.ai_router import _sanitize_error
        logger.warning(
            "get_step_context_failed",
            step=step_name,
            pipeline_run_id=pipeline_run_id,
            error=_sanitize_error(exc),
        )
        return None


# ── Agent Registry ──────────────────────────────────────────────────

_agents: dict[str, Any] = {}


def register_agent(agent: Any) -> None:
    """Register an agent instance globally.

    ``agent`` must have ``.name`` and ``.display_name`` attributes.
    """
    if agent.name in _agents:
        logger.warning("agent_overwrite", agent=agent.name, hint="Duplicate agent name — previous registration replaced")
    _agents[agent.name] = agent
    logger.info("agent_registered", agent=agent.name, display_name=agent.display_name)


def get_agent(name: str) -> Any:
    """Get a registered agent by name."""
    if name not in _agents:
        raise KeyError(f"Agent not registered: {name}")
    return _agents[name]


def list_agents() -> list[str]:
    """List all registered agent names."""
    return sorted(_agents.keys())


# ── PHASE-E: Verification Gates ─────────────────────────────────────


@dataclass
class VerificationResult:
    """Result of a verification gate check."""
    passed: bool
    failures: list[str]  # Human-readable failure descriptions
    warnings: list[str]  # Non-blocking warnings

    def rejection_message(self) -> str:
        """Build rejection message for the LLM."""
        parts = [f"REJECTED — {len(self.failures)} error(s):"]
        for f in self.failures[:5]:
            parts.append(f"  • {f}")
        if self.warnings:
            parts.append(f"  (+ {len(self.warnings)} warning(s))")
        parts.append("Fix ALL errors and call write_file again with corrected content.")
        return "\n".join(parts)


class VerificationGate:
    """Hard gate that blocks write_file until verification passes.

    Phase E: Agents can no longer write garbage code — every write_file
    call runs HARD verification. If it fails, the LLM gets a REJECTED
    message and MUST fix the code before the file is accepted.

    This is the single most impactful change: bad code is caught at WRITE
    TIME, not after all files are generated.

    CHANGE-1: Loop detection — hashes rejected content and detects when
    the LLM resubmits identical code that was already rejected. Forces
    the LLM to change approach instead of wasting rounds.
    """

    def __init__(self, extra_rules: list[Any] | None = None):
        self._extra_rules = extra_rules or []
        # CHANGE-1: Track rejected content hashes per path → previous failure reasons
        self._rejection_hashes: dict[str, dict[str, list[str]]] = {}  # {path: {hash: [reasons]}}

    def verify_python(self, path: str, content: str, all_files: dict[str, str]) -> VerificationResult:
        """Verify a Python file before accepting the write."""
        import ast
        import re

        failures: list[str] = []
        warnings: list[str] = []

        # 1. Syntax validity (HARD — must pass)
        try:
            ast.parse(content)
        except SyntaxError as e:
            failures.append(f"SyntaxError at line {e.lineno}: {e.msg}")

        # 2. Markdown fence detection (HARD — LLM artifact)
        if "```python" in content or "```\n" in content:
            failures.append("Contains markdown code fences (```). Remove them.")

        # 3. Empty function bodies with just 'pass' (WARNING)
        pass_only = re.findall(r"def\s+\w+\([^)]*\)(?:\s*->[^:]+)?:\s*\n\s+pass\s*$", content, re.MULTILINE)
        if len(pass_only) > 2:
            warnings.append(f"{len(pass_only)} functions have only 'pass' — likely incomplete")

        # 4. Intra-project import resolution (WARNING)
        import_lines = re.findall(r"^from\s+(app\.\S+)\s+import", content, re.MULTILINE)
        for mod in import_lines:
            # Convert module path to potential file path
            mod_path = mod.replace(".", "/") + ".py"
            mod_init = mod.replace(".", "/") + "/__init__.py"
            if mod_path not in all_files and mod_init not in all_files:
                # Also check without 'backend/' prefix
                alt_path = "backend/" + mod_path
                alt_init = "backend/" + mod_init
                if alt_path not in all_files and alt_init not in all_files:
                    warnings.append(f"Import '{mod}' may not resolve (no matching file generated yet)")

        # 4b. CHANGE-3 + CHANGE-30: Cross-file import NAME validation (HARD FAIL)
        # Check that imported names actually exist in the target module.
        # CHANGE-30: Upgraded from WARNING to FAILURE. When the LLM writes
        # `from app.models import UserCreate` but models.py exports `UserSchema`,
        # the file is REJECTED. The LLM MUST fix the import to use the correct
        # name. This forces the agent to be AWARE of its own generated exports.
        name_imports = re.findall(
            r"^from\s+(app\.\S+)\s+import\s+(.+)$", content, re.MULTILINE
        )
        for mod, names_str in name_imports:
            # Find the matching file in all_files
            mod_file = mod.replace(".", "/") + ".py"
            alt_file = "backend/" + mod_file
            source_content = all_files.get(mod_file) or all_files.get(alt_file)
            if source_content:
                try:
                    source_tree = ast.parse(source_content)
                    available_names: set[str] = set()
                    for node in ast.iter_child_nodes(source_tree):
                        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                            available_names.add(node.name)
                        elif isinstance(node, ast.Assign):
                            for tgt in node.targets:
                                if isinstance(tgt, ast.Name):
                                    available_names.add(tgt.id)
                    # Parse imported names (handle "import A, B, C")
                    imported = [n.strip().split(" as ")[0].strip() for n in names_str.split(",")]
                    for imp_name in imported:
                        if imp_name and imp_name not in available_names and imp_name != "*":
                            avail_sorted = sorted(available_names)[:5]
                            # CHANGE-30: HARD FAIL — file is rejected, LLM must
                            # fix the import to use correct export names.
                            failures.append(
                                f"WRONG IMPORT: '{imp_name}' from '{mod}' does NOT exist. "
                                f"Available exports: {avail_sorted}. "
                                f"Use one of these exact names."
                            )
                except SyntaxError:
                    pass  # Source file has syntax errors — can't parse

        # 5. Package __init__.py check (WARNING)
        dir_path = "/".join(path.split("/")[:-1])
        if dir_path and dir_path.count("/") >= 1:
            init_path = dir_path + "/__init__.py"
            if init_path not in all_files and path != init_path:
                # Only warn if there are other .py files in same directory
                siblings = [p for p in all_files if p.startswith(dir_path + "/") and p.endswith(".py")]
                if siblings:
                    warnings.append(f"Directory '{dir_path}' may need an __init__.py")

        # 6. Extra rules from mistake memory
        for rule in self._extra_rules:
            try:
                result = rule(path, content, all_files)
                if isinstance(result, str) and result:
                    failures.append(result)
            except Exception:
                pass  # Don't let bad rules crash verification

        return VerificationResult(
            passed=len(failures) == 0,
            failures=failures,
            warnings=warnings,
        )

    def verify_typescript(self, path: str, content: str, all_files: dict[str, str]) -> VerificationResult:
        """Verify a TypeScript/JavaScript file before accepting the write."""
        import re

        failures: list[str] = []
        warnings: list[str] = []

        # 1. Markdown fence detection (HARD)
        if "```typescript" in content or "```tsx" in content or "```jsx" in content or "```\n" in content:
            failures.append("Contains markdown code fences (```). Remove them.")

        # 2. Balanced brackets (HARD — catches truncated files)
        opens = content.count("{") + content.count("[") + content.count("(")
        closes = content.count("}") + content.count("]") + content.count(")")
        if abs(opens - closes) > 3:
            failures.append(f"Unbalanced brackets: {opens} openers vs {closes} closers (likely truncated)")

        # 3. Excessive 'any' type usage (WARNING)
        any_count = len(re.findall(r":\s*any\b", content, re.IGNORECASE))
        if any_count > 5:
            warnings.append(f"Used 'any' type {any_count} times — consider proper typing")

        # 4. Component export check for .tsx files (WARNING)
        if path.endswith(".tsx"):
            has_export = "export default" in content or "export function" in content or "export const" in content
            if not has_export:
                warnings.append("No export found in .tsx file — component won't be importable")

        # 5. Extra rules from mistake memory
        for rule in self._extra_rules:
            try:
                result = rule(path, content, all_files)
                if isinstance(result, str) and result:
                    failures.append(result)
            except Exception:
                pass

        return VerificationResult(
            passed=len(failures) == 0,
            failures=failures,
            warnings=warnings,
        )

    def verify(self, path: str, content: str, all_files: dict[str, str]) -> VerificationResult:
        """Auto-dispatch to the right verifier based on file extension.

        CHANGE-1: After verification, checks if this exact content was
        previously rejected for the same path. If so, adds a HARD failure
        forcing the LLM to change its approach.
        """
        import hashlib as _hashlib

        if path.endswith(".py"):
            result = self.verify_python(path, content, all_files)
        elif path.endswith((".ts", ".tsx", ".js", ".jsx")):
            result = self.verify_typescript(path, content, all_files)
        else:
            # Non-code files (JSON, YAML, HTML, CSS) — basic checks only
            failures = []
            if "```" in content and (content.startswith("```") or "\n```" in content[:50]):
                failures.append("Contains markdown code fences.")
            result = VerificationResult(passed=len(failures) == 0, failures=failures, warnings=[])

        # CHANGE-1: Loop detection — if this content was already rejected,
        # the LLM is stuck in a loop. Add a HARD failure with prior reasons.
        if not result.passed:
            content_hash = _hashlib.md5((path + content[:500]).encode()).hexdigest()
            path_hashes = self._rejection_hashes.setdefault(path, {})
            if content_hash in path_hashes:
                prior_reasons = path_hashes[content_hash]
                result.failures.insert(0, (
                    "LOOP DETECTED: You submitted code identical to a previous rejection. "
                    f"Previous rejection reasons: {'; '.join(prior_reasons[:3])}. "
                    "You MUST take a DIFFERENT approach — do not repeat the same code."
                ))
                logger.warning("verification_loop_detected", path=path, hash=content_hash)
            # Store this rejection for future loop detection
            path_hashes[content_hash] = result.failures[:3]

        return result


# ── PHASE-H: Goal Decomposition ──────────────────────────────────────


@dataclass
class SubGoal:
    """A trackable sub-goal within an agent's work."""
    id: str                       # e.g. "models.py"
    description: str              # e.g. "Generate SQLAlchemy models"
    file_path: str                # Expected output file
    dependencies: list[str] = field(default_factory=list)  # Other goal IDs
    completed: bool = False
    failed: bool = False
    attempts: int = 0


class GoalTracker:
    """PHASE-H: Tracks sub-goals and their completion status.

    Decomposes a generation order into independent trackable goals.
    Each goal has dependencies, completion status, and retry tracking.
    """

    def __init__(self, generation_order: list[dict[str, str]]) -> None:
        self.goals: list[SubGoal] = []
        self._build_goals(generation_order)

    def _build_goals(self, generation_order: list[dict[str, str]]) -> None:
        """Convert generation_order into sub-goals with dependencies."""
        prev_id = ""
        for step in generation_order:
            path = step.get("path", "")
            name = step.get("name", path)
            goal_id = path or name

            # Infer dependencies from step ordering and file types
            deps = []
            if prev_id and "model" not in goal_id.lower():
                # Most files depend on models being generated first
                model_goals = [g.id for g in self.goals if "model" in g.id.lower()]
                deps = model_goals[:1]  # Depend on first model file only

            self.goals.append(SubGoal(
                id=goal_id,
                description=name,
                file_path=path,
                dependencies=deps,
            ))
            prev_id = goal_id

    def next_goal(self) -> SubGoal | None:
        """Return next unmet goal whose dependencies are satisfied."""
        completed_ids = {g.id for g in self.goals if g.completed}
        for goal in self.goals:
            if goal.completed or goal.failed:
                continue
            if all(dep in completed_ids for dep in goal.dependencies):
                return goal
        return None

    def mark_complete(self, goal_id: str) -> None:
        """Mark a goal as complete."""
        for g in self.goals:
            if g.id == goal_id:
                g.completed = True
                g.attempts += 1
                break

    def mark_failed(self, goal_id: str) -> None:
        """Mark a goal as failed (max retries exceeded)."""
        for g in self.goals:
            if g.id == goal_id:
                g.failed = True
                break

    def increment_attempt(self, goal_id: str) -> int:
        """Increment attempt count, return new count."""
        for g in self.goals:
            if g.id == goal_id:
                g.attempts += 1
                return g.attempts
        return 0

    def is_all_done(self) -> bool:
        """Check if all goals are completed or failed."""
        return all(g.completed or g.failed for g in self.goals)

    def get_goal_prompt(self, goal: SubGoal, all_files: dict[str, str]) -> str:
        """Build a focused prompt for a single sub-goal."""
        completed_files = [g.file_path for g in self.goals if g.completed]
        prompt = (
            f"Generate the file: {goal.file_path}\n"
            f"Description: {goal.description}\n"
        )
        if completed_files:
            prompt += f"Already generated: {', '.join(completed_files[:10])}\n"
        prompt += "Write the file using write_file, then call task_complete."
        return prompt

    def summary(self) -> dict[str, Any]:
        """Compact summary of progress."""
        return {
            "total": len(self.goals),
            "completed": sum(1 for g in self.goals if g.completed),
            "failed": sum(1 for g in self.goals if g.failed),
            "remaining": sum(1 for g in self.goals if not g.completed and not g.failed),
        }


# ── PHASE-I: Adaptive Model Complexity ───────────────────────────────


class AdaptiveComplexity:
    """PHASE-I: Start cheap, escalate only when verification fails.

    Expected savings: ~50-60% on model costs.
    - Simple files (models, configs, types) → cheap model (Haiku/Flash)
    - Complex files (auth, business logic) → auto-escalate on failure
    """

    def __init__(self, initial: TaskComplexity = TaskComplexity.LOW) -> None:
        self.current = initial
        self.consecutive_failures = 0
        self._consecutive_successes = 0  # CHANGE-10: Track for de-escalation
        self._escalation_count = 0
        self._de_escalation_count = 0  # CHANGE-10

    def on_verification_pass(self) -> None:
        """Reset failure counter on success, track for de-escalation."""
        self.consecutive_failures = 0
        self._consecutive_successes += 1
        # CHANGE-10: De-escalate after sustained success at higher tier
        if self._consecutive_successes >= 3 and self.current != TaskComplexity.LOW:
            self.de_escalate()
            self._consecutive_successes = 0

    def on_verification_fail(self) -> None:
        """Track failures and escalate after threshold."""
        self.consecutive_failures += 1
        self._consecutive_successes = 0  # CHANGE-10: Reset on failure
        if self.consecutive_failures >= 2:
            self.escalate()
            self.consecutive_failures = 0

    def escalate(self) -> None:
        """Move to next complexity tier."""
        if self.current == TaskComplexity.LOW:
            self.current = TaskComplexity.MEDIUM
            self._escalation_count += 1
            logger.info("adaptive_complexity_escalated", to="MEDIUM")
        elif self.current == TaskComplexity.MEDIUM:
            self.current = TaskComplexity.HIGH
            self._escalation_count += 1
            logger.info("adaptive_complexity_escalated", to="HIGH")

    def de_escalate(self) -> None:
        """CHANGE-10: After 3 consecutive successes at higher tier, drop down.

        Saves money: if models.py needed MEDIUM but schemas.py is simple,
        drop back to LOW for schemas.py instead of staying at MEDIUM.
        """
        if self.current == TaskComplexity.HIGH:
            self.current = TaskComplexity.MEDIUM
            self._de_escalation_count += 1
            logger.info("adaptive_complexity_de_escalated", to="MEDIUM")
        elif self.current == TaskComplexity.MEDIUM:
            self.current = TaskComplexity.LOW
            self._de_escalation_count += 1
            logger.info("adaptive_complexity_de_escalated", to="LOW")

    def summary(self) -> dict[str, Any]:
        return {
            "current": self.current.value if hasattr(self.current, 'value') else str(self.current),
            "escalation_count": self._escalation_count,
            "de_escalation_count": self._de_escalation_count,  # CHANGE-10
        }


async def reflect(
    agent_name: str,
    output_summary: str,
    context_summary: str,
    max_tokens: int = 500,
) -> dict[str, Any]:
    """3.1-FIX: Post-execution self-reflection using the cheapest model.

    Calls a cheap model (Haiku/Flash) to critique any agent's output after
    execution. Returns a dict with issues found, confidence score, and
    improvement suggestions.

    This is independent of the agent that produced the output — any agent's
    output can be reflected upon. Cost: ~$0.001 per reflection.

    Args:
        agent_name: Which agent produced the output.
        output_summary: Compact summary of what the agent produced (truncated).
        context_summary: Brief project context (framework, tables, etc.)
        max_tokens: Max response tokens for the reflection.

    Returns:
        Dict with keys: issues (list[str]), confidence (float 0-1),
        suggestion (str). Returns empty dict on failure.
    """
    import json as _json

    critique_prompt = (
        f"You are reviewing the output of agent '{agent_name}'.\n"
        f"Context: {context_summary}\n\n"
        f"Output summary:\n{output_summary[:3000]}\n\n"
        "Analyze the output for:\n"
        "1. Completeness — are there missing components?\n"
        "2. Consistency — do parts contradict each other?\n"
        "3. Quality — are there obvious errors or shortcuts?\n\n"
        "Respond in JSON: {\"issues\": [...], \"confidence\": 0.0-1.0, \"suggestion\": \"...\"}\n"
        "If the output looks good, return {\"issues\": [], \"confidence\": 0.9, \"suggestion\": \"none\"}"
    )

    try:
        from app.services.ai_router import get_ai_router, AIRequest, AIMessage
        router = get_ai_router()  # CHANGE-11: Use singleton, not new instance
        response = await router.call(AIRequest(
            messages=[AIMessage(role="user", content=critique_prompt)],
            complexity=TaskComplexity.LOW,  # Cheapest model
            max_tokens=max_tokens,
            agent_name=f"{agent_name}_reflection",
        ))

        from app.utils.json_parser import parse_json
        result = parse_json(response.content, fallback={})
        if isinstance(result, dict):
            return result
        return {}
    except Exception:
        return {}
