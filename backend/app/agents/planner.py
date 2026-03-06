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

from app.agents.base import TaskComplexity

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


class PlannerAgent:
    """AI-driven pipeline planner.

    Receives project state and decides what stage(s) to execute next.
    Uses the cheapest model since this is routing, not generation.
    """

    name = "planner"

    async def plan_next(
        self,
        project_state: dict[str, Any],
        completed_stages: list[str],
        failed_stages: list[str],
        last_result: dict[str, Any] | None = None,
    ) -> PlannerDecision:
        """Decide what stage(s) to run next.

        Args:
            project_state: Summary of project (type, framework, flags).
            completed_stages: Stages already completed successfully.
            failed_stages: Stages that failed.
            last_result: Output from the most recently completed stage.

        Returns:
            PlannerDecision with next actions.
        """
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

        prompt = (
            "You are a pipeline planner for a code generation system.\n"
            f"{state_summary}\n"
            "Rules:\n"
            "- testing and security_audit are MANDATORY (never skip)\n"
            "- Stages with satisfied dependencies can run in parallel\n"
            "- Skip optional stages if not needed for this project type\n\n"
            "Respond in JSON:\n"
            '{"next": ["stage1", "stage2"], "parallel": true/false, '
            '"skip": ["optional_stage"], "reason": "brief explanation"}'
        )

        try:
            from app.services.ai_router import get_ai_router, AIRequest, AIMessage
            router = get_ai_router()  # CHANGE-13: Use singleton
            response = await router.call(AIRequest(
                messages=[AIMessage(role="user", content=prompt)],
                complexity=TaskComplexity.LOW,  # Cheapest model
                max_tokens=300,
                agent_name="planner",
            ))

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
            logger.warning("planner_ai_call_failed", error=str(exc)[:200])

        # Fallback: run first eligible stage sequentially
        return PlannerDecision(
            next_actions=[PlannedAction(stage=eligible[0], reason="fallback")],
            reasoning="AI planner failed — falling back to first eligible stage.",
        )
