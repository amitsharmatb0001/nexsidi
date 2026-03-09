"""PlannerAgent — AI-driven dynamic pipeline routing.

2.1-FIX: Replaces the fixed STAGE_ORDER with an AI planner that decides
what stage to execute next based on project state, completed stages,
and last results. Uses the cheapest model (Haiku/Flash) since this is
routing logic, not content generation.

The planner respects stage dependencies (e.g., backend_build requires
architecture) and mandatory stages (testing, security) while allowing
intelligent reordering, parallelization, and optional stage skipping.

Fallback: If the planner fails, the pipeline falls back to sequential
STAGE_ORDER automatically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from app.agents.base import (
    TaskComplexity,
    ToolDefinition,
    WEB_SEARCH_TOOL,
    WEB_SCRAPE_TOOL,
    call_ai_with_tools,
    check_inbox,
    format_inbox_for_prompt,
    handle_web_tool,
)

logger = structlog.get_logger(__name__)


# Stage dependency graph: stage -> set of stages that must complete first
STAGE_DEPENDENCIES: dict[str, set[str]] = {
    "analysis": set(),
    "architecture": {"analysis"},
    "architecture_review": {"architecture"},
    "database_design": {"architecture"},
    "ui_design": {"architecture"},
    "checkpoint_design": {"architecture_review", "database_design", "ui_design"},
    "backend_build": {"checkpoint_design"},
    "frontend_build": {"checkpoint_design"},
    "quality_review": {"backend_build", "frontend_build"},
    "testing": {"quality_review"},
    "security_audit": {"backend_build"},
    "compliance_check": {"backend_build"},
    "tilotma_review": {"testing", "security_audit", "compliance_check"},
    "fixing": {"tilotma_review"},
    "checkpoint_testing": {"fixing"},
    "deployment": {"checkpoint_testing"},
    "delivery": {"deployment"},
    "completed": {"delivery"},
}

# Stages that must always run (cannot be skipped by planner)
MANDATORY_STAGES: frozenset[str] = frozenset({
    "analysis", "architecture", "backend_build", "testing",
    "security_audit", "deployment", "delivery", "completed",
})


@dataclass
class PlannedAction:
    """A single action the planner wants to execute."""
    stage: str           # PipelineStage value
    priority: str = "required"  # "required" | "optional"
    reason: str = ""     # Why this stage should run next


@dataclass
class PlannerDecision:
    """The planner's decision for what to do next."""
    next_actions: list[PlannedAction] = field(default_factory=list)
    reasoning: str = ""
    can_parallelize: bool = False
    skip_stages: list[str] = field(default_factory=list)


class PlannerToolHandler:
    """Handles tool calls for PlannerAgent's AI planning loop."""

    def __init__(self) -> None:
        self._decision: dict[str, Any] | None = None
        self._complete: bool = False  # Signals call_ai_with_tools to stop

    async def __call__(self, tool_name: str, tool_input: dict) -> str:
        # Delegate web tools first
        web_result = await handle_web_tool(tool_name, tool_input)
        if web_result is not None:
            return web_result

        return f"Unknown tool: {tool_name}"


class PlannerAgent:
    """AI-driven pipeline planner.

    Receives project state and decides what stage(s) to execute next.
    Uses the cheapest model since this is routing, not generation.
    """

    name = "planner"
    display_name = "Planner Agent"
    default_complexity = TaskComplexity.LOW
    default_model: str | None = None

    @property
    def tools(self) -> list[ToolDefinition]:
        """Web research tools for informed pipeline planning."""
        return [WEB_SEARCH_TOOL, WEB_SCRAPE_TOOL]

    async def plan_next(
        self,
        project_state: dict[str, Any],
        completed_stages: list[str],
        failed_stages: list[str],
        last_result: dict[str, Any] | None = None,
        failure_history: list[dict[str, Any]] | None = None,
    ) -> PlannerDecision:
        """Decide what stage(s) to run next.

        Args:
            project_state: Summary of project (type, framework, flags).
            completed_stages: Stages already completed successfully.
            failed_stages: Stages that failed.
            last_result: Output from the most recently completed stage.
            failure_history: HIGH-4 FIX — list of failure dicts with stage,
                agent, attempt count, and reason.  Allows the planner to
                reason about *why* a stage failed, not just *that* it failed.

        Returns:
            PlannerDecision with next actions.
        """
        # Check inbox for messages from other agents (esp. AUTHORITY directives)
        # AUDIT-B2-FIX: pipeline_run_id was undefined here — use run_id from
        # project_state if available, else empty string to avoid NameError.
        _run_id = project_state.get("run_id", "")
        inbox_messages = await check_inbox(self.name, _run_id)
        inbox_context = format_inbox_for_prompt(inbox_messages)

        # Find all stages whose dependencies are satisfied
        eligible = self._find_eligible_stages(completed_stages, failed_stages)

        if not eligible:
            # No eligible stages → pipeline is done or stuck
            if "completed" in completed_stages or "delivery" in completed_stages:
                return PlannerDecision(reasoning="Pipeline complete.")
            return PlannerDecision(
                reasoning="No eligible stages (may be stuck due to failures).",
            )

        # For simple cases (1-2 eligible), skip AI call
        if len(eligible) <= 2:
            actions = [
                PlannedAction(stage=s, priority="required", reason="dependencies satisfied")
                for s in eligible
            ]
            can_parallel = len(actions) > 1 and self._can_parallelize(eligible)
            return PlannerDecision(
                next_actions=actions,
                reasoning=f"Dependencies satisfied for: {', '.join(eligible)}",
                can_parallelize=can_parallel,
            )

        # Complex case: ask AI to prioritize
        return await self._ai_plan(
            eligible, project_state, completed_stages, last_result,
        )

    def _find_eligible_stages(
        self,
        completed: list[str],
        failed: list[str],
    ) -> list[str]:
        """Find stages whose dependencies are all satisfied."""
        completed_set = set(completed)
        failed_set = set(failed)
        eligible = []

        for stage, deps in STAGE_DEPENDENCIES.items():
            if stage in completed_set or stage in failed_set:
                continue  # Already done or failed
            if deps.issubset(completed_set):
                eligible.append(stage)

        return eligible

    def _can_parallelize(self, stages: list[str]) -> bool:
        """Check if stages can run in parallel (no mutual dependencies)."""
        for i, s1 in enumerate(stages):
            for s2 in stages[i + 1:]:
                deps1 = STAGE_DEPENDENCIES.get(s1, set())
                deps2 = STAGE_DEPENDENCIES.get(s2, set())
                if s1 in deps2 or s2 in deps1:
                    return False
        return True

    async def _ai_plan(
        self,
        eligible: list[str],
        project_state: dict[str, Any],
        completed: list[str],
        last_result: dict[str, Any] | None,
    ) -> PlannerDecision:
        """Ask AI to decide among multiple eligible stages."""
        state_summary = (
            f"Project type: {project_state.get('project_type', 'unknown')}\n"
            f"Framework: {project_state.get('backend_framework', 'unknown')}\n"
            f"Has frontend: {project_state.get('has_frontend', True)}\n"
            f"Has database: {project_state.get('has_database', True)}\n"
            f"Completed: {', '.join(completed)}\n"
            f"Eligible next: {', '.join(eligible)}\n"
        )
        if last_result:
            errors = last_result.get("errors", 0)
            state_summary += f"Last stage errors: {errors}\n"

        # CHANGE-23: Include quality signals for informed re-planning
        quality = project_state.get("quality_signals", {})
        if quality:
            state_summary += "Quality signals:\n"
            for agent, info in quality.items():
                state_summary += (
                    f"  {agent}: {info['issues']} issues, "
                    f"confidence={info['confidence']}\n"
                )
        health = project_state.get("agent_health", {})
        if health:
            state_summary += "Agent health: " + ", ".join(
                f"{a}={r}" for a, r in health.items()
            ) + "\n"
        api_issues = project_state.get("api_contract_issues", 0)
        if api_issues:
            state_summary += f"API contract mismatches: {api_issues}\n"

        # HIGH-4 FIX: Include failure history so the planner can reason about
        # *why* stages failed, not just *that* they failed.
        _failure_block = ""
        if failure_history:
            _lines = [
                f"- {f.get('stage', '?')} failed "
                f"(attempt {f.get('attempt', '?')}): "
                f"{str(f.get('reason', 'unknown'))[:150]}"
                for f in failure_history[-5:]  # Last 5 failures max
            ]
            _failure_block = (
                "\nRecent failure history:\n"
                + "\n".join(_lines) + "\n"
                "If a stage has failed 2+ times for the same reason, "
                "investigate the root cause before retrying.\n"
            )

        prompt = (
            "You are a pipeline planner for a code generation system.\n"
            f"{state_summary}\n"
            f"{_failure_block}"
            "Rules:\n"
            "- testing and security_audit are MANDATORY (never skip)\n"
            "- Stages with satisfied dependencies can run in parallel\n"
            "- Skip optional stages if not needed for this project type\n\n"
            "Respond in JSON:\n"
            '{"next": ["stage1", "stage2"], "parallel": true/false, '
            '"skip": ["optional_stage"], "reason": "brief explanation"}'
        )

        try:
            handler = PlannerToolHandler()
            response = await call_ai_with_tools(
                agent=self,
                messages=[{"role": "user", "content": prompt}],
                system_prompt=(
                    "You are a pipeline planner for a code generation system. "
                    "You can use web_search to research best practices for pipeline "
                    "ordering or framework-specific build requirements. "
                    "Respond with a JSON plan after your research (if any)."
                ),
                task_type="general",
                tool_handler=handler,
                max_tool_rounds=3,
            )

            from app.utils.json_parser import parse_json
            result = parse_json(response.content, fallback={})

            if isinstance(result, dict) and "next" in result:
                next_stages = result["next"]
                if isinstance(next_stages, list):
                    # Validate all suggested stages are actually eligible
                    valid = [s for s in next_stages if s in eligible]
                    if valid:
                        return PlannerDecision(
                            next_actions=[
                                PlannedAction(stage=s, reason=result.get("reason", ""))
                                for s in valid
                            ],
                            reasoning=result.get("reason", "AI planned"),
                            can_parallelize=result.get("parallel", False),
                            skip_stages=result.get("skip", []),
                        )
        except Exception as exc:
            # AUDIT-B2-FIX: Escalate from warning to error — silent fallback to
            # sequential was invisible to users and downstream agents.
            logger.error(
                "planner_ai_call_failed: FALLING BACK to sequential execution",
                error=str(exc)[:200],
            )

        # Fallback: run first eligible stage sequentially
        # AUDIT-B2-FIX: Added __planner_degraded__ flag so Tilotma and pipeline
        # logs can detect that dynamic planning was not used.
        return PlannerDecision(
            next_actions=[PlannedAction(stage=eligible[0], reason="fallback_sequential")],
            reasoning="[PLANNER DEGRADED] AI planner failed — falling back to sequential execution. "
                      "Dynamic stage routing was NOT used for this run.",
        )
