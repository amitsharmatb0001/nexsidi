"""Decision Ledger — Audit trail for technical decisions.

Records what was decided, by whom, why, and what alternatives were
considered. Enables post-launch support: "Why was React chosen over Vue?"

Storage: In-memory + context_engine persistence per pipeline run.
Query: By project_id, agent_name, or decision_type.

Integration:
- Vikram: records tech_choice and architecture decisions
- Shubham: records framework and pattern choices
- Karan: records security trade-offs
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# In-memory ledger: pipeline_run_id -> [decisions]
_LEDGER: dict[str, list[dict[str, Any]]] = {}


class DecisionLedger:
    """Audit trail: what was decided, by whom, why.

    Usage:
        from app.services.decision_ledger import decision_ledger

        # Record a decision
        decision_id = decision_ledger.record_decision(
            project_id="run-123",
            agent_name="vikram",
            decision_type="tech_choice",
            decision="Use React with TypeScript for frontend",
            reasoning="User requested TypeScript; React has largest ecosystem",
            alternatives=["Vue 3", "Angular 17", "Svelte"],
            confidence=0.85,
        )

        # Query later for support
        decisions = decision_ledger.get_decisions("run-123", agent_name="vikram")
    """

    def record_decision(
        self,
        project_id: str,
        agent_name: str,
        decision_type: str,
        decision: str,
        reasoning: str,
        alternatives: list[str] | None = None,
        confidence: float = 0.0,
        context_snapshot: dict[str, Any] | None = None,
    ) -> str:
        """Record a decision. Returns decision_id."""
        decision_id = str(uuid.uuid4())[:8]

        entry = {
            "decision_id": decision_id,
            "project_id": project_id,
            "agent_name": agent_name,
            "decision_type": decision_type,
            "decision": decision,
            "reasoning": reasoning,
            "alternatives": alternatives or [],
            "confidence": confidence,
            "timestamp": time.time(),
            "context_snapshot": context_snapshot,
        }

        if project_id not in _LEDGER:
            _LEDGER[project_id] = []
        _LEDGER[project_id].append(entry)

        logger.info(
            "decision_recorded",
            decision_id=decision_id,
            agent=agent_name,
            decision_type=decision_type,
            decision=decision[:100],
        )

        return decision_id

    def get_decisions(
        self,
        project_id: str,
        agent_name: str | None = None,
        decision_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query decisions for a project."""
        entries = _LEDGER.get(project_id, [])

        if agent_name:
            entries = [e for e in entries if e["agent_name"] == agent_name]
        if decision_type:
            entries = [e for e in entries if e["decision_type"] == decision_type]

        return entries

    def explain_decision(self, project_id: str, decision_id: str) -> dict[str, Any] | None:
        """Get full decision record with context."""
        for entry in _LEDGER.get(project_id, []):
            if entry["decision_id"] == decision_id:
                return entry
        return None

    def get_support_summary(self, project_id: str) -> str:
        """Human-readable decision history for support queries."""
        entries = _LEDGER.get(project_id, [])
        if not entries:
            return "No decisions recorded for this project."

        lines = [f"Decision History ({len(entries)} decisions):\n"]
        for e in entries:
            alts = ", ".join(e["alternatives"]) if e["alternatives"] else "none"
            lines.append(
                f"- [{e['agent_name']}] {e['decision_type']}: {e['decision']}\n"
                f"  Reasoning: {e['reasoning'][:200]}\n"
                f"  Alternatives considered: {alts}\n"
                f"  Confidence: {e['confidence']:.0%}\n"
            )
        return "\n".join(lines)

    async def persist_to_context(self, project_id: str) -> None:
        """Persist decisions to context_engine for crash recovery."""
        entries = _LEDGER.get(project_id, [])
        if not entries:
            return

        try:
            from app.services.context_engine import get_context_engine
            engine = get_context_engine()
            # Strip context_snapshot to save space (can be large)
            stripped = [
                {k: v for k, v in e.items() if k != "context_snapshot"}
                for e in entries
            ]
            await engine.store(project_id, "decision_ledger", {"decisions": stripped})
        except Exception as exc:
            logger.warning("decision_ledger_persist_failed", error=str(exc)[:200])


# Module-level singleton
decision_ledger = DecisionLedger()
