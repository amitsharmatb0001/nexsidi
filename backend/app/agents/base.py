"""Base agent framework: abstract base class, tool registry, agent result model.

Every NexSidi agent inherits from BaseAgent and implements execute().
Tools are registered per-agent via the @tool decorator.

Design:
- Agents are stateless — all state flows through context_engine
- Each agent receives input from the previous step's context output
- Each agent stores its output in context_engine for the next step
- AI calls go through ai_router (unified Claude + Gemini interface)
- Prompts are served by prompt_engine (just-in-time, cached)
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

from app.services.ai_router import AIMessage, AIRequest, AIResponse, TaskComplexity

logger = structlog.get_logger(__name__)


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


# ── Base Agent ──────────────────────────────────────────────────────


class BaseAgent(ABC):
    """Abstract base class for all NexSidi pipeline agents.

    Subclasses must implement:
    - execute(): the main agent logic
    - name: agent identifier (e.g., "tilotma")
    - display_name: human-readable name (e.g., "Tilotma — Project Manager")

    Provides:
    - AI call helpers (call_ai, call_ai_with_tools)
    - Tool registration
    - Structured logging
    - Context read/write via context_engine
    """

    name: str = ""
    display_name: str = ""
    default_complexity: TaskComplexity = TaskComplexity.MEDIUM
    default_model: str | None = None  # Override auto-routing

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register_tool(self, tool: ToolDefinition) -> None:
        """Register a tool available to this agent."""
        self._tools[tool.name] = tool

    @property
    def tools(self) -> list[ToolDefinition]:
        """All registered tools."""
        return list(self._tools.values())

    @abstractmethod
    async def execute(
        self,
        pipeline_run_id: str,
        context: dict[str, Any],
    ) -> AgentResult:
        """Execute the agent's task.

        Args:
            pipeline_run_id: UUID of the current pipeline run.
            context: Accumulated context from prior steps.
                Keys are step names, values are step outputs.

        Returns:
            AgentResult with output stored in context_engine.
        """
        ...

    async def run(
        self,
        pipeline_run_id: str,
        context: dict[str, Any],
    ) -> AgentResult:
        """Wrapper around execute() with timing, logging, error handling.

        This is what the pipeline orchestrator calls. It delegates to
        the agent's execute() method.
        """
        execution_id = str(uuid.uuid4())
        started_at = time.monotonic()

        logger.info(
            "agent_start",
            agent=self.name,
            pipeline_run_id=pipeline_run_id,
            execution_id=execution_id,
        )

        try:
            result = await self.execute(pipeline_run_id, context)
            result.execution_id = execution_id
            result.started_at = started_at
            result.completed_at = time.monotonic()

            logger.info(
                "agent_complete",
                agent=self.name,
                status=result.status.value,
                duration_ms=round(result.duration_ms, 1),
                pipeline_run_id=pipeline_run_id,
            )
            return result

        except Exception as exc:
            completed_at = time.monotonic()
            logger.error(
                "agent_failed",
                agent=self.name,
                error=str(exc),
                pipeline_run_id=pipeline_run_id,
            )
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error=str(exc),
                execution_id=execution_id,
                started_at=started_at,
                completed_at=completed_at,
            )

    # ── AI Call Helpers ─────────────────────────────────────────────

    async def call_ai(
        self,
        messages: list[dict[str, str]],
        system_prompt: str | None = None,
        task_type: str = "general",
        complexity: TaskComplexity | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        enable_thinking: bool = False,
    ) -> AIResponse:
        """Make an AI call through the AI Router.

        Convenience wrapper that converts simple dicts to AIMessage objects.
        """
        from app.services.ai_router import get_ai_router

        router = get_ai_router()
        ai_messages = [AIMessage(role=m["role"], content=m["content"]) for m in messages]

        request = AIRequest(
            messages=ai_messages,
            system_prompt=system_prompt,
            task_type=task_type,
            complexity=complexity or self.default_complexity,
            model_override=self.default_model,
            temperature=temperature,
            max_tokens=max_tokens,
            enable_thinking=enable_thinking,
        )

        return await router.call(request)

    async def call_ai_with_tools(
        self,
        messages: list[dict[str, str]],
        system_prompt: str | None = None,
        task_type: str = "general",
        complexity: TaskComplexity | None = None,
        tool_handler: Any = None,
        max_tool_rounds: int = 10,
    ) -> AIResponse:
        """Make an AI call with tool use loop.

        Sends tools to the model, handles tool_use responses, calls the
        handler, sends results back, and loops until the model returns
        a final text response (no more tool calls).

        Args:
            messages: Conversation messages.
            system_prompt: System prompt.
            task_type: For model routing.
            complexity: For model routing.
            tool_handler: Callable(tool_name, tool_input) -> dict.
                Called when the model wants to use a tool.
            max_tool_rounds: Maximum tool use iterations.

        Returns:
            Final AIResponse after all tool calls are resolved.
        """
        from app.services.ai_router import get_ai_router

        router = get_ai_router()
        ai_messages = [AIMessage(role=m["role"], content=m["content"]) for m in messages]
        tool_defs = [t.to_anthropic_format() for t in self.tools] if self.tools else None

        request = AIRequest(
            messages=ai_messages,
            system_prompt=system_prompt,
            task_type=task_type,
            complexity=complexity or self.default_complexity,
            model_override=self.default_model,
            tools=tool_defs,
        )

        response = await router.call(request)

        # Tool use loop
        rounds = 0
        while response.tool_calls and rounds < max_tool_rounds:
            rounds += 1

            # Process each tool call
            for tc in response.tool_calls:
                if tool_handler is None:
                    logger.warning("no_tool_handler", tool=tc["name"])
                    continue

                tool_result = await tool_handler(tc["name"], tc["input"])
                # Add tool result to conversation
                ai_messages.append(AIMessage(
                    role="user",
                    content=f"Tool '{tc['name']}' result: {tool_result}",
                ))

            # Call AI again with tool results
            request = AIRequest(
                messages=ai_messages,
                system_prompt=system_prompt,
                task_type=task_type,
                complexity=complexity or self.default_complexity,
                model_override=self.default_model,
                tools=tool_defs,
            )
            response = await router.call(request)

        return response

    # ── Context Helpers ─────────────────────────────────────────────

    async def store_output(
        self,
        pipeline_run_id: str,
        output: dict[str, Any],
    ) -> None:
        """Store this agent's output in the context engine."""
        from app.services.context_engine import get_context_engine

        engine = get_context_engine()
        await engine.store(pipeline_run_id, self.name, output)

    async def get_step_context(
        self,
        pipeline_run_id: str,
        step_name: str,
    ) -> dict[str, Any] | None:
        """Retrieve a specific step's output from context."""
        from app.services.context_engine import get_context_engine

        engine = get_context_engine()
        return await engine.get_step(pipeline_run_id, step_name)


# ── Agent Registry ──────────────────────────────────────────────────

_agents: dict[str, BaseAgent] = {}


def register_agent(agent: BaseAgent) -> None:
    """Register an agent instance globally."""
    _agents[agent.name] = agent
    logger.info("agent_registered", agent=agent.name, display_name=agent.display_name)


def get_agent(name: str) -> BaseAgent:
    """Get a registered agent by name."""
    if name not in _agents:
        raise KeyError(f"Agent not registered: {name}")
    return _agents[name]


def list_agents() -> list[str]:
    """List all registered agent names."""
    return sorted(_agents.keys())
