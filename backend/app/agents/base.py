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

    @property
    def duration_ms(self) -> float:
        if self.completed_at and self.started_at:
            return (self.completed_at - self.started_at) * 1000
        return 0.0


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
        return AgentResult(
            agent_name=agent.name,
            status=AgentStatus.FAILED,
            error=safe_error,
            execution_id=execution_id,
            started_at=started_at,
            completed_at=completed_at,
        )


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
    """Make an AI call through the AI Router.

    ``agent`` must have ``.default_complexity`` and ``.default_model`` attributes.
    ``shared_context`` is an optional :class:`SharedContext` for prompt caching.
    ``dynamic_system_context`` is appended AFTER the cached system_prompt block
    without cache_control, so it doesn't invalidate the stable prompt cache.
    """
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

    return await router.call(request)


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

        full_content += response.content
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


async def call_ai_with_tools(
    agent: Any,
    messages: list[dict[str, str]],
    system_prompt: str | None = None,
    task_type: str = "general",
    complexity: TaskComplexity | None = None,
    tool_handler: Any = None,
    max_tool_rounds: int = 10,
) -> AIResponse:
    """Make an AI call with tool use loop.

    ``agent`` must have ``.default_complexity``, ``.default_model``, and ``.tools``.

    Sends tools to the model, handles tool_use responses, calls the
    handler, sends results back, and loops until the model returns
    a final text response (no more tool calls).
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

            try:
                tool_result = await tool_handler(tc["name"], tc["input"])
                result_str = _json.dumps(tool_result) if not isinstance(tool_result, str) else tool_result
                # R16-FIX: Truncate large tool results to prevent OOM and
                # quadratic token cost growth. Each round accumulates ALL
                # previous results, so large results compound dangerously.
                _MAX_TOOL_RESULT_CHARS = 50_000
                if len(result_str) > _MAX_TOOL_RESULT_CHARS:
                    result_str = result_str[:_MAX_TOOL_RESULT_CHARS] + "\n... [truncated]"
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


async def store_output(
    agent: Any,
    pipeline_run_id: str,
    output: dict[str, Any],
) -> None:
    """Store an agent's output in the context engine.

    ``agent`` must have ``.name`` attribute.
    Gracefully skips if the context engine is not initialized (e.g. in tests).
    """
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
