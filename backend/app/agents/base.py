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


# ── Inter-Agent Communication Tool ────────────────────────────────

ASK_AGENT_TOOL = ToolDefinition(
    name="ask_agent",
    description=(
        "Ask another agent a question and wait for their response. "
        "Use this when you need information from a specific agent "
        "(e.g., ask vikram about architecture decisions, ask shubham "
        "about API endpoint details). The target agent will be notified "
        "and their response will be returned."
    ),
    parameters={
        "type": "object",
        "properties": {
            "to_agent": {
                "type": "string",
                "enum": [
                    "tilotma", "saanvi", "vikram", "challenger", "dhruv",
                    "vanya", "shubham", "aanya", "karan", "navya",
                    "deepika", "aarav", "pranav", "fixer",
                ],
                "description": "Name of the agent to ask",
            },
            "question": {
                "type": "string",
                "description": "The question to ask the target agent",
            },
            "context": {
                "type": "string",
                "description": "Additional context to help the target agent answer",
            },
        },
        "required": ["to_agent", "question"],
    },
)


# ── Web Search Tools (Firecrawl) ──────────────────────────────────

WEB_SEARCH_TOOL = ToolDefinition(
    name="web_search",
    description=(
        "Search the web for documentation, best practices, error solutions, "
        "market research, and framework-specific APIs. Returns titles, URLs, "
        "and content snippets. Use this to research before making decisions."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query (e.g., 'FastAPI JWT authentication best practices 2025')",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results (default 5, max 10)",
                "default": 5,
            },
        },
        "required": ["query"],
    },
)

WEB_SCRAPE_TOOL = ToolDefinition(
    name="web_scrape",
    description=(
        "Scrape a specific URL and extract its content as markdown. "
        "Use this to read documentation pages, API references, or "
        "blog posts found via web_search."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to scrape (must be a valid http/https URL)",
            },
        },
        "required": ["url"],
    },
)


# ── Self-Coding Tools (Evolution Engine) ─────────────────────────────
#
# These tools let agents read and modify their own source code.
# SAFETY PROTOCOL: Changes to security/privacy/safety code are BLOCKED
# and require human engineer approval. See self_coder.py for details.

SELF_READ_SOURCE_TOOL = ToolDefinition(
    name="self_read_source",
    description=(
        "Read a NexSidi source file to understand existing code. "
        "Path is relative to backend/app/ (e.g., 'agents/vikram.py'). "
        "Use this to inspect how agents, services, or tools work."
    ),
    parameters={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path relative to backend/app/ (e.g., 'agents/shubham.py', 'services/ai_router.py')",
            },
        },
        "required": ["file_path"],
    },
)

SELF_LIST_FILES_TOOL = ToolDefinition(
    name="self_list_files",
    description="List NexSidi source files matching a glob pattern.",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern (default: '**/*.py'). Examples: 'agents/*.py', 'services/*.py'",
                "default": "**/*.py",
            },
        },
    },
)

SELF_SEARCH_SOURCE_TOOL = ToolDefinition(
    name="self_search_source",
    description="Search across NexSidi source files for a regex pattern.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Regex pattern to search for (e.g., 'def execute', 'class.*Agent')",
            },
            "file_pattern": {
                "type": "string",
                "description": "File glob to limit search (default: '*.py')",
                "default": "*.py",
            },
        },
        "required": ["query"],
    },
)

SELF_PROPOSE_CHANGE_TOOL = ToolDefinition(
    name="self_propose_change",
    description=(
        "Propose a code change to any NexSidi source file. "
        "The change is validated (AST check, path safety) before it can be applied. "
        "NOTE: Changes to security/privacy/safety code require human approval."
    ),
    parameters={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path relative to backend/app/",
            },
            "old_code": {
                "type": "string",
                "description": "Exact code to replace (must exist in file). Empty for new files.",
            },
            "new_code": {
                "type": "string",
                "description": "New code to insert in place of old_code.",
            },
            "reason": {
                "type": "string",
                "description": "Why this change is needed (logged for audit trail).",
            },
            "proposed_by": {
                "type": "string",
                "description": "Your agent name.",
            },
            "change_type": {
                "type": "string",
                "enum": ["edit", "create", "add_tool", "modify_prompt", "create_agent"],
                "description": "Type of change.",
                "default": "edit",
            },
        },
        "required": ["file_path", "new_code", "reason", "proposed_by"],
    },
)

SELF_APPLY_CHANGE_TOOL = ToolDefinition(
    name="self_apply_change",
    description=(
        "Apply a validated change proposal to the filesystem. "
        "Only works if the proposal passed validation. "
        "Changes to protected code will be BLOCKED (requires human approval)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "proposal_id": {
                "type": "string",
                "description": "The proposal ID returned by self_propose_change.",
            },
        },
        "required": ["proposal_id"],
    },
)

SELF_ROLLBACK_CHANGE_TOOL = ToolDefinition(
    name="self_rollback_change",
    description="Rollback a previously applied change to restore the original code.",
    parameters={
        "type": "object",
        "properties": {
            "proposal_id": {
                "type": "string",
                "description": "The proposal ID to rollback.",
            },
        },
        "required": ["proposal_id"],
    },
)

SELF_CREATE_TOOL_TOOL = ToolDefinition(
    name="self_create_tool",
    description=(
        "Add a new tool to an existing agent. Generates the ToolDefinition "
        "registration and handler code, then creates a validated proposal."
    ),
    parameters={
        "type": "object",
        "properties": {
            "agent_name": {"type": "string", "description": "Target agent name"},
            "tool_name": {"type": "string", "description": "Name for the new tool"},
            "tool_description": {"type": "string", "description": "Tool description for the AI"},
            "tool_parameters": {"type": "object", "description": "JSON Schema for tool parameters"},
            "handler_code": {"type": "string", "description": "Python source for the handler method"},
            "reason": {"type": "string", "description": "Why this tool is needed"},
        },
        "required": ["agent_name", "tool_name", "tool_description", "handler_code", "reason"],
    },
)

SELF_GET_CHANGE_LOG_TOOL = ToolDefinition(
    name="self_get_change_log",
    description="View the history of all code changes made by agents.",
    parameters={
        "type": "object",
        "properties": {},
    },
)

# All self-coding tools bundled for easy registration
SELF_CODING_TOOLS: list[ToolDefinition] = [
    SELF_READ_SOURCE_TOOL,
    SELF_LIST_FILES_TOOL,
    SELF_SEARCH_SOURCE_TOOL,
    SELF_PROPOSE_CHANGE_TOOL,
    SELF_APPLY_CHANGE_TOOL,
    SELF_ROLLBACK_CHANGE_TOOL,
    SELF_CREATE_TOOL_TOOL,
    SELF_GET_CHANGE_LOG_TOOL,
]


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

    # Directive 5: Emit agent_thinking "start" event
    try:
        from app.services.pipeline_events import publish_agent_thinking_event
        await publish_agent_thinking_event(
            pipeline_run_id, agent.name, "start",
            detail=f"Agent {agent.name} starting execution",
        )
    except Exception as _evt_exc:
        logger.debug("agent_thinking_event_failed", error=str(_evt_exc)[:200])

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

        # AGENTIC-FIX: Activate post-execution self-reflection.
        # reflect() existed but was NEVER CALLED — now it runs after every
        # successful agent execution using the cheapest model (~$0.001/call).
        # Results are stored in agent output for downstream consumers.
        if result.status == AgentStatus.COMPLETED and result.output:
            try:
                import json as _refl_json
                output_summary = _refl_json.dumps(result.output, default=str)[:3000]
                context_summary = str({
                    k: type(v).__name__ for k, v in context.items()
                    if not k.startswith("__")
                })[:500]
                reflection = await reflect(agent.name, output_summary, context_summary)
                if reflection and reflection.get("issues"):
                    result.output["__self_reflection__"] = reflection
                    logger.info(
                        "agent_reflection",
                        agent=agent.name,
                        issues=len(reflection.get("issues", [])),
                        confidence=reflection.get("confidence", 0),
                    )
            except Exception as _refl_exc:
                logger.debug("agent_reflection_failed", error=str(_refl_exc)[:200])

        # Directive 5: Emit agent_thinking "complete" event
        try:
            await publish_agent_thinking_event(
                pipeline_run_id, agent.name, "complete",
                detail=f"Completed in {round(result.duration_ms)}ms — {result.status.value}",
            )
        except Exception as _evt_exc:
            logger.debug("agent_thinking_complete_event_failed", error=str(_evt_exc)[:200])

        return result

    except AgentInterruptRequest:
        # CONTROL-FLOW FIX: Re-raise AgentInterruptRequest so the pipeline's
        # handler in _execute_agent_stage() can catch it for dynamic re-dispatch.
        # Previously the generic `except Exception` below swallowed it, marking
        # the agent as FAILED instead of triggering the interrupt handler.
        raise

    except KeyboardInterrupt:
        # Never swallow keyboard interrupt — allow clean shutdown.
        raise

    except Exception as exc:
        # DEADLOCK FIX: If an agent hits a deadlock, re-raise so the pipeline
        # can skip the ask or route differently — don't mark the whole agent FAILED.
        try:
            from app.services.agent_message_bus import AgentDeadlockDetected
            if isinstance(exc, AgentDeadlockDetected):
                logger.warning(
                    "agent_deadlock_propagated",
                    agent=agent.name,
                    pipeline_run_id=pipeline_run_id,
                    error=str(exc)[:300],
                )
                raise
        except ImportError:
            pass  # agent_message_bus not available — continue to failure handling
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

        # Directive 5: Emit agent_thinking "error" event
        try:
            await publish_agent_thinking_event(
                pipeline_run_id, agent.name, "error",
                detail=safe_error[:200],
            )
        except Exception as _evt_exc:
            logger.debug("agent_thinking_error_event_failed", error=str(_evt_exc)[:200])

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
    return any(x in error_str for x in ["500", "502", "503", "429", "timeout", "overloaded", "rate_limit"])


# ── Shared Web Search Tool Handler ─────────────────────────────────

async def handle_web_tool(tool_name: str, tool_input: dict[str, Any]) -> str | None:
    """Handle shared tool calls (web, inter-agent, self-coding) across all agents.

    Returns the tool result string, or None if this isn't a shared tool.
    Agent tool handlers should call this FIRST and return the result if not None.
    """
    import json as _json

    # ── Web Search (multi-provider with fallback) ───────────────────
    if tool_name == "web_search":
        from app.services.web_search import get_multi_provider_search

        ws = get_multi_provider_search()
        query = tool_input.get("query", "")
        max_results = min(tool_input.get("max_results", 5), 10)
        results = await ws.search(query, max_results=max_results)

        if not results:
            return _json.dumps({"results": [], "note": "No results found or web search is disabled"})

        return _json.dumps({
            "results": [
                {"title": r.title, "url": r.url, "snippet": r.snippet, "content": r.content[:2000]}
                for r in results
            ]
        })

    # ── Web Scrape (multi-provider with fallback) ───────────────────
    if tool_name == "web_scrape":
        from app.services.web_search import get_multi_provider_search

        ws = get_multi_provider_search()
        url = tool_input.get("url", "")
        page = await ws.scrape(url)

        if not page:
            return _json.dumps({"error": "Failed to scrape URL or web search is disabled"})

        return _json.dumps({
            "url": page.url,
            "title": page.title,
            "markdown": page.markdown[:10000],
        })

    # ── Inter-Agent Communication ───────────────────────────────────
    if tool_name == "ask_agent":
        to_agent = tool_input.get("to_agent", "")
        question = tool_input.get("question", "")
        ctx = tool_input.get("context", "")

        if not to_agent or not question:
            return _json.dumps({"error": "to_agent and question are required"})

        try:
            from app.services.agent_message_bus import get_message_bus

            bus = get_message_bus()
            response = await bus.ask(
                from_agent="requesting_agent",
                to_agent=to_agent,
                question=question,
                context=ctx,
                timeout=30,
            )

            if response:
                return _json.dumps({"agent": to_agent, "response": response})
            else:
                return _json.dumps({"agent": to_agent, "response": "No response received (timeout)"})
        except Exception as exc:
            return _json.dumps({"error": f"Failed to ask {to_agent}: {str(exc)[:200]}"})

    # ── Self-Coding Tools ───────────────────────────────────────────
    # HIGH-1 FIX: Gate all self-coding tools behind feature flag.
    # Agents modifying their own source at runtime is v3+ territory.
    if tool_name.startswith("self_"):
        from app.config import get_settings as _get_settings
        if not _get_settings().enable_self_coder:
            return _json.dumps({
                "error": "Self-coding is disabled in this environment. "
                         "Set ENABLE_SELF_CODER=true to enable."
            })

    if tool_name == "self_read_source":
        from app.services.self_coder import get_self_coder
        sc = get_self_coder()
        file_path = tool_input.get("file_path", "")
        content = sc.read_source(file_path)
        return _json.dumps({"file_path": file_path, "content": content[:20000]})

    if tool_name == "self_list_files":
        from app.services.self_coder import get_self_coder
        sc = get_self_coder()
        pattern = tool_input.get("pattern", "**/*.py")
        files = sc.list_source_files(pattern)
        return _json.dumps({"files": files, "total": len(files)})

    if tool_name == "self_search_source":
        from app.services.self_coder import get_self_coder
        sc = get_self_coder()
        query = tool_input.get("query", "")
        file_pattern = tool_input.get("file_pattern", "*.py")
        matches = sc.search_source(query, file_pattern)
        return _json.dumps({"matches": matches, "total": len(matches)})

    if tool_name == "self_propose_change":
        from app.services.self_coder import get_self_coder
        sc = get_self_coder()
        proposal = sc.propose_change(
            file_path=tool_input.get("file_path", ""),
            old_code=tool_input.get("old_code", ""),
            new_code=tool_input.get("new_code", ""),
            reason=tool_input.get("reason", ""),
            proposed_by=tool_input.get("proposed_by", "unknown_agent"),
            change_type=tool_input.get("change_type", "edit"),
        )
        result: dict[str, Any] = {
            "proposal_id": proposal.id,
            "status": proposal.status,
            "file_path": proposal.file_path,
            "validation_errors": proposal.validation_errors,
        }
        if proposal.requires_human_review:
            result["requires_human_review"] = True
            result["safety_flags"] = proposal.safety_flags
            result["message"] = (
                "SAFETY PROTOCOL: This change touches protected code. "
                "A human engineer must approve before it can be applied. "
                "The proposal is BLOCKED until then."
            )
        return _json.dumps(result)

    if tool_name == "self_apply_change":
        from app.services.self_coder import get_self_coder
        sc = get_self_coder()
        proposal_id = tool_input.get("proposal_id", "")
        result = sc.apply_change(proposal_id)
        return _json.dumps(result)

    if tool_name == "self_rollback_change":
        from app.services.self_coder import get_self_coder
        sc = get_self_coder()
        proposal_id = tool_input.get("proposal_id", "")
        result = sc.rollback_change(proposal_id)
        return _json.dumps(result)

    if tool_name == "self_create_tool":
        from app.services.self_coder import get_self_coder
        sc = get_self_coder()
        proposal = sc.create_tool_for_agent(
            agent_name=tool_input.get("agent_name", ""),
            tool_name=tool_input.get("tool_name", ""),
            tool_description=tool_input.get("tool_description", ""),
            tool_parameters=tool_input.get("tool_parameters", {}),
            handler_code=tool_input.get("handler_code", ""),
            reason=tool_input.get("reason", ""),
        )
        result = {
            "proposal_id": proposal.id,
            "status": proposal.status,
            "validation_errors": proposal.validation_errors,
        }
        if proposal.requires_human_review:
            result["requires_human_review"] = True
            result["safety_flags"] = proposal.safety_flags
        return _json.dumps(result)

    if tool_name == "self_get_change_log":
        from app.services.self_coder import get_self_coder
        sc = get_self_coder()
        log = sc.get_change_log()
        return _json.dumps({"changes": log[-20:], "total": len(log)})

    return None  # Not a shared tool — let the agent's handler process it


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
    max_continuations: int = 50,  # PHASE-1: Safety cap only — no functional limit
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
                f"(Accumulated {len(full_content)} chars so far across {continuation + 1} parts.) "
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


async def _summarize_dropped_messages(messages: list) -> str:
    """Extract key decisions/outputs from messages about to be dropped.

    Uses a compact bullet-list extraction first, then optionally summarises
    via a cheap model.  Falls back to the bullet list if the AI call fails.

    Accepts both plain dicts and AIMessage dataclass objects.
    """
    from app.services.ai_router import ToolUseBlock as _TUB

    summary_parts: list[str] = []
    for msg in messages:
        role = getattr(msg, "role", None) or (msg.get("role", "") if isinstance(msg, dict) else "")
        content = getattr(msg, "content", None)
        if content is None and isinstance(msg, dict):
            content = msg.get("content", "")

        if role == "assistant":
            # AIMessage: content is list[ContentBlock] with ToolUseBlock entries
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, _TUB):
                        summary_parts.append(f"Called: {block.name}")
            # Dict-style: tool_calls list
            elif isinstance(msg, dict):
                for tc in msg.get("tool_calls", []):
                    fn = tc.get("function", {})
                    summary_parts.append(f"Called: {fn.get('name', '?')}")
        elif role == "user":
            # User messages often contain ToolResultBlock content
            if isinstance(content, list):
                for block in content:
                    block_content = getattr(block, "content", "")
                    if block_content:
                        summary_parts.append(f"Result: {str(block_content)[:200]}")
            elif isinstance(content, str) and content:
                summary_parts.append(f"Result: {content[:200]}")
        elif role == "tool":
            content_str = str(content or "")[:200]
            if content_str:
                summary_parts.append(f"Result: {content_str}")

    if not summary_parts:
        return f"[{len(messages)} messages omitted — no substantive content]"

    summary_prompt = (
        "Summarize these agent actions and results in 3-5 bullet points. "
        "Focus on: decisions made, files created/modified, errors encountered, "
        "key outputs. Be extremely concise.\n\n" + "\n".join(summary_parts[:30])
    )
    try:
        from app.services.ai_router import get_ai_router
        _router = get_ai_router()
        resp = await _router.call(
            prompt=summary_prompt,
            task_type="general",
            max_tokens=300,
        )
        return f"[Summary of {len(messages)} earlier messages:\n{resp.text}\n]"
    except Exception:
        # Fallback: return the compact bullet list directly (no AI needed)
        return (
            f"[Summary of {len(messages)} earlier actions:\n"
            + "\n".join(summary_parts[:10])
            + "\n]"
        )


async def call_ai_with_tools(
    agent: Any,
    messages: list[dict[str, str]],
    system_prompt: str | None = None,
    task_type: str = "general",
    complexity: TaskComplexity | None = None,
    tool_handler: Any = None,
    max_tool_rounds: int = 200,  # PHASE-1: Safety cap only — no functional limit
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

    # NATIVE-SEARCH: Detect if agent has web_search tool → enable provider-native search.
    # This injects Claude's web_search_20250305 or Gemini's google_search grounding
    # directly into the AI call. The model decides when to search autonomously.
    _has_web_search = any(t.name == "web_search" for t in (agent.tools or []))

    request = AIRequest(
        messages=ai_messages,
        system_prompt=system_prompt,
        task_type=task_type,
        complexity=complexity or agent.default_complexity,
        model_override=resolve_model_override(agent.default_model),
        tools=tool_defs,
        enable_native_web_search=_has_web_search,
    )

    # V5-FIX (CRITICAL-12): Handle AIResponseTruncatedError specifically.
    # The old code let truncation errors bubble up to _llm_recovery() which
    # would just retry with a bigger model — but if the output is architecturally
    # too large, every model will truncate.  Now we catch truncation, increase
    # max_tokens on the request, and retry ONCE before escalating.
    from app.services.ai_router import AIResponseTruncatedError as _TruncErr

    try:
        response = await router.call(request)
    except _TruncErr:
        # Double max_tokens and retry once before giving up
        if request.max_tokens and request.max_tokens < 64000:
            request.max_tokens = min(request.max_tokens * 2, 64000)
            logger.warning("truncation_retry_with_higher_max_tokens",
                           new_max_tokens=request.max_tokens, task_type=task_type)
            response = await router.call(request)
        else:
            raise  # Already at max — let _llm_recovery handle escalation

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
    _MAX_HISTORY_MESSAGES = 40  # 1 initial + ~15 rounds × 2 + buffer
    _KEEP_ROUNDS = 15  # 15 rounds = 30 messages

    rounds = 0
    # CHANGE-16: Stall detection — track tool call fingerprints to detect
    # when the LLM is stuck calling the same tool with identical arguments.
    # Saves 5-15 wasted rounds ($0.10-0.50) per stuck agent.
    import hashlib as _hashlib_stall
    _tool_fingerprints: list[str] = []
    _stall_warnings = 0
    _MAX_STALL_WARNINGS = 10  # PHASE-1: Safety cap only — generous before force-break

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
                            enable_native_web_search=_has_web_search,
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
            # P2-1: Improved sliding window — keeps first message (system context)
            # + inserts a brief summary of dropped middle, then keeps recent rounds.
            # Old code lost critical intermediate context silently.
            first_msg = ai_messages[0]
            # R36-FIX: Ensure we keep COMPLETE rounds (assistant + user pairs).
            tail = ai_messages[-(_KEEP_ROUNDS * 2):]
            # R25-FIX-5: Ensure message alternation after truncation.
            if tail and tail[0].role == "user":
                tail = tail[1:]
            # R36-FIX: Guard against empty tail after truncation.
            if not tail:
                tail = ai_messages[-2:]
            # P2-1: Insert a smart summary of dropped messages so the AI
            # retains awareness of key decisions/actions from earlier rounds.
            _dropped = ai_messages[1:len(ai_messages) - len(tail)]
            if _dropped:
                from app.services.ai_router import AIMessage
                _summary = await _summarize_dropped_messages(_dropped)
                summary_msg = AIMessage(
                    role="user",
                    content=_summary,
                )
                ai_messages = [first_msg, summary_msg] + tail
            else:
                ai_messages = [first_msg] + tail

        # Call AI again with tool results
        request = AIRequest(
            messages=ai_messages,
            system_prompt=system_prompt,
            task_type=task_type,
            complexity=complexity or agent.default_complexity,
            model_override=resolve_model_override(agent.default_model),
            tools=tool_defs,
            enable_native_web_search=_has_web_search,
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


# ── PHASE-3: Inter-Agent Communication Utilities ─────────────────────


async def notify_agents(
    from_agent: str,
    pipeline_run_id: str,
    message: str,
    to_agents: list[str] | None = None,
    priority: str = "NORMAL",
) -> None:
    """Send a broadcast message to other agents (fire-and-forget).

    Safe to call — silently does nothing if message bus unavailable.
    """
    try:
        from app.services.agent_message_bus import get_agent_message_bus
        bus = get_agent_message_bus()
        await bus.broadcast(
            from_agent=from_agent,
            pipeline_run_id=pipeline_run_id,
            message=message,
            to_agents=to_agents,
            cc_agents=["tilotma", "vikram"],  # Always CC both oversight agents
            priority=priority,
        )
    except Exception as _bus_exc:
        logger.debug("agent_broadcast_failed", from_agent=from_agent, error=str(_bus_exc)[:100])


async def report_error_to_agent(
    from_agent: str,
    to_agent: str,
    pipeline_run_id: str,
    error_type: str,
    file_path: str,
    description: str,
) -> str | None:
    """Report an error to another agent. Returns their response or None.

    E.g., Aanya finds broken API endpoint → reports to Shubham.
    """
    try:
        from app.services.agent_message_bus import get_agent_message_bus
        bus = get_agent_message_bus()
        return await bus.report_error(
            from_agent=from_agent,
            to_agent=to_agent,
            pipeline_run_id=pipeline_run_id,
            error_type=error_type,
            file_path=file_path,
            description=description,
        )
    except Exception as _bus_exc:
        logger.debug("agent_error_report_failed", from_agent=from_agent, to_agent=to_agent, error=str(_bus_exc)[:100])
        return None


async def check_inbox(
    agent_name: str,
    pipeline_run_id: str,
) -> list[dict[str, Any]]:
    """Check for pending messages from other agents.

    Returns list of messages (broadcasts, error reports, questions).
    Call at the START of execute() to incorporate cross-agent context.
    """
    try:
        from app.services.agent_message_bus import get_agent_message_bus
        bus = get_agent_message_bus()
        messages = await bus.get_pending_questions(agent_name, pipeline_run_id)
        if messages:
            logger.info(
                "agent_inbox_messages",
                agent=agent_name,
                count=len(messages),
                priorities=[m.get("priority", "NORMAL") for m in messages],
            )
        return messages
    except Exception as _inbox_exc:
        logger.debug("agent_inbox_check_failed", agent=agent_name, error=str(_inbox_exc)[:100])
        return []


def format_inbox_for_prompt(messages: list[dict[str, Any]]) -> str:
    """Format inbox messages as a prompt section for the agent.

    Converts raw message dicts into a readable prompt supplement
    that informs the agent about cross-agent communications.

    PHASE-3: AUTHORITY messages from Tilotma/Vikram are rendered with
    non-ignorable markers. Agents MUST address these before proceeding.
    """
    if not messages:
        return ""

    # Sort: AUTHORITY first, then CRITICAL, then NORMAL/INFO
    _PRIORITY_ORDER = {"AUTHORITY": 0, "CRITICAL": 1, "NORMAL": 2, "INFO": 3}
    sorted_msgs = sorted(
        messages[:15],  # Cap at 15 messages
        key=lambda m: _PRIORITY_ORDER.get(m.get("priority", "NORMAL"), 2),
    )

    # Separate authority messages for special treatment
    authority_msgs = [m for m in sorted_msgs if m.get("priority") == "AUTHORITY" or m.get("from_agent") in ("tilotma", "vikram")]
    other_msgs = [m for m in sorted_msgs if m not in authority_msgs]

    lines: list[str] = []

    if authority_msgs:
        lines.append("## ⚠ AUTHORITY DIRECTIVES (MANDATORY — DO NOT IGNORE)")
        lines.append("The following messages are from Tilotma (Chief AI Official) or Vikram (Chief Architect).")
        lines.append("You MUST address every directive below before proceeding with your task.")
        lines.append("")
        for msg in authority_msgs:
            from_agent = msg.get("from_agent", "unknown")
            content = msg.get("question", "")[:500]
            lines.append(f"- **[AUTHORITY — {from_agent.upper()}]**: {content}")
        lines.append("")

    if other_msgs:
        lines.append("## Messages from Other Agents")
        for msg in other_msgs:
            from_agent = msg.get("from_agent", "unknown")
            priority = msg.get("priority", "NORMAL")
            content = msg.get("question", "")[:500]
            msg_type = "broadcast"
            try:
                import orjson
                ctx = orjson.loads(msg.get("context", "{}"))
                msg_type = ctx.get("type", "broadcast")
            except Exception:
                pass  # Expected: malformed context JSON — default to "broadcast"

            prefix = "**CRITICAL**" if priority == "CRITICAL" else ""
            lines.append(f"- [{msg_type}] from **{from_agent}** {prefix}: {content}")

    return "\n".join(lines)


# ── Structured Inbox Processing (Phase 6B) ──────────────────────────


@dataclass
class ProcessedInbox:
    """Structured result of processing an agent's inbox.

    Instead of just injecting raw text into prompts, this separates messages
    by type so agents can handle them appropriately:
    - authority_directives: MUST be injected into system prompt as mandatory
    - pending_questions: Should be answered before proceeding (tool_result format)
    - feedback: Must be addressed in the agent's work
    - broadcasts: Informational context (optional to address)
    """

    authority_directives: list[dict[str, Any]] = field(default_factory=list)
    pending_questions: list[dict[str, Any]] = field(default_factory=list)
    feedback: list[dict[str, Any]] = field(default_factory=list)
    broadcasts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_authority(self) -> bool:
        return len(self.authority_directives) > 0

    @property
    def has_questions(self) -> bool:
        return len(self.pending_questions) > 0

    def to_system_prompt_section(self) -> str:
        """Build a system prompt section from authority directives."""
        if not self.authority_directives:
            return ""

        lines = [
            "\n## MANDATORY DIRECTIVES (from project leadership)",
            "You MUST follow ALL directives below. Non-compliance will trigger pipeline rejection.",
            "",
        ]
        for d in self.authority_directives:
            from_agent = d.get("from_agent", "unknown")
            content = d.get("question", d.get("content", ""))[:500]
            lines.append(f"[{from_agent.upper()}]: {content}")

        return "\n".join(lines)

    def to_feedback_prompt(self) -> str:
        """Build a feedback section that agents must address."""
        if not self.feedback:
            return ""

        lines = [
            "\n## FEEDBACK TO ADDRESS",
            "The following feedback was provided by other agents. You MUST address each item.",
            "",
        ]
        for f in self.feedback:
            from_agent = f.get("from_agent", "unknown")
            content = f.get("question", f.get("content", ""))[:500]
            lines.append(f"- [{from_agent}]: {content}")

        return "\n".join(lines)

    def format_questions_as_tool_results(self) -> list[dict[str, str]]:
        """Format pending questions as tool_result messages for the AI."""
        results = []
        for q in self.pending_questions:
            from_agent = q.get("from_agent", "unknown")
            question = q.get("question", "")[:500]
            results.append({
                "role": "user",
                "content": (
                    f"[QUESTION from {from_agent}]: {question}\n"
                    "Please answer this question before continuing your work."
                ),
            })
        return results


def process_inbox_messages(messages: list[dict[str, Any]]) -> ProcessedInbox:
    """Process raw inbox messages into structured categories.

    Phase 6B: Instead of just formatting messages as text, this categorizes
    them so agents can handle each type appropriately:
    - AUTHORITY: From tilotma/vikram → mandatory system prompt injection
    - QUESTION: Direct questions → must answer via tool_result
    - FEEDBACK: Review feedback → must address in output
    - BROADCAST: General info → optional context
    """
    inbox = ProcessedInbox()

    for msg in messages[:15]:  # Cap at 15
        priority = msg.get("priority", "NORMAL")
        from_agent = msg.get("from_agent", "")
        msg_type = "broadcast"

        # Try to extract message type from context
        try:
            import json as _json
            ctx = _json.loads(msg.get("context", "{}"))
            msg_type = ctx.get("type", "broadcast")
        except Exception:
            pass

        # Categorize
        if priority == "AUTHORITY" or from_agent in ("tilotma", "vikram"):
            inbox.authority_directives.append(msg)
        elif msg_type == "question" or priority == "CRITICAL":
            inbox.pending_questions.append(msg)
        elif msg_type == "feedback" or msg_type == "review":
            inbox.feedback.append(msg)
        else:
            inbox.broadcasts.append(msg)

    return inbox


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
                    pass  # Expected: source file has syntax errors — can't AST-parse imports

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
            except Exception as _rule_exc:
                logger.debug("verification_rule_failed", rule=str(rule)[:100], error=str(_rule_exc)[:200])

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
            except Exception as _rule_exc:
                logger.debug("css_verification_rule_failed", rule=str(rule)[:100], error=str(_rule_exc)[:200])

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

        PHASE-9: Zero Trust scan of generated code before acceptance.
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

        # PHASE-9: Zero Trust scan of generated code
        try:
            from app.services.zero_trust import get_zero_trust_gate
            import asyncio
            _zt = get_zero_trust_gate()
            # Run scan synchronously since verify() is sync
            try:
                loop = asyncio.get_running_loop()
                # Already in async context — schedule as task
                import concurrent.futures
                _zt_result = loop.run_until_complete(_zt.scan_generated_code(path, content, "unknown"))
            except RuntimeError:
                # No event loop — create one for the scan
                _zt_result = asyncio.run(_zt.scan_generated_code(path, content, "unknown"))

            if _zt_result.is_blocked():
                result.failures.insert(0, f"SECURITY BLOCK: {'; '.join(_zt_result.findings[:3])}")
                result.passed = False
            elif _zt_result.findings:
                for f in _zt_result.findings[:3]:
                    result.warnings.append(f"SECURITY: {f}")
        except Exception as _zt_exc:
            logger.debug("zero_trust_scan_failed", path=path, error=str(_zt_exc)[:200])

        # Phase 1B: Code Authenticity Validation (Directive 4)
        # Zero-tolerance enforcement: NO stubs, NO facades, NO dead code, NO vaporware.
        # Critical findings (stubs, facades) BLOCK the write. Agents must implement
        # real logic. Applied to NexSidi's own generated code too.
        try:
            from app.services.code_authenticity import get_code_authenticity_validator
            _lang = "python" if path.endswith(".py") else "other"
            _auth_validator = get_code_authenticity_validator()
            _auth_report = _auth_validator.validate_file(path, content, _lang)
            if _auth_report.critical_count > 0:
                for _af in _auth_report.findings:
                    if _af.severity.value == "critical":
                        result.failures.append(
                            f"AUTHENTICITY: {_af.description} — {_af.fix_hint}"
                        )
                result.passed = False
            # Non-critical authenticity findings as warnings
            for _af in _auth_report.findings:
                if _af.severity.value in ("high", "medium"):
                    result.warnings.append(
                        f"AUTHENTICITY: {_af.description}"
                    )
        except Exception as _auth_exc:
            logger.debug("authenticity_scan_failed", path=path, error=str(_auth_exc)[:200])

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
            task_type="reflection",
        ))

        from app.utils.json_parser import parse_json
        result = parse_json(response.content, fallback={})
        if isinstance(result, dict):
            return result
        return {}
    except Exception:
        return {}
