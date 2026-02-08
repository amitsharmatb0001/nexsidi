
import time
import json
import logging
from typing import Dict, Any, List, Optional
from uuid import uuid4


class DecisionLedger:
    """
    Records every agent decision with full reasoning.
    
    For after-sales support:
    - Why was React chosen over Vue?
    - Why was this bug marked critical vs medium?
    - What alternatives were considered?
    - What was the state when decision was made?
    """
    
    def __init__(self):
        self.logger = logging.getLogger("decision_ledger")
        self._decisions = {}  # In-memory for v1, DB for v2
    
    def record_decision(
        self,
        project_id: str,
        agent_name: str,
        decision_type: str,
        decision: str,
        reasoning: str,
        alternatives: List[str] = None,
        confidence: float = 0.0,
        context_snapshot: Dict = None
    ) -> str:
        """
        Record a decision with full context.
        
        Args:
            project_id: Project UUID
            agent_name: Who made the decision
            decision_type: Category (tech_choice, bug_severity, architecture, etc.)
            decision: What was decided
            reasoning: WHY it was decided
            alternatives: What other options existed
            confidence: How confident (0-1)
            context_snapshot: State at time of decision
        
        Returns:
            decision_id for reference
        """
        decision_id = str(uuid4())[:8]
        
        entry = {
            "id": decision_id,
            "project_id": project_id,
            "agent": agent_name,
            "type": decision_type,
            "decision": decision,
            "reasoning": reasoning,
            "alternatives": alternatives or [],
            "confidence": confidence,
            "context_snapshot": context_snapshot or {},
            "timestamp": time.time()
        }
        
        # Store in memory (keyed by project)
        if project_id not in self._decisions:
            self._decisions[project_id] = []
        self._decisions[project_id].append(entry)
        
        # Also persist to context engine
        try:
            from app.services.context_engine import context_engine
            all_decisions = self._decisions[project_id]
            context_engine.store_context(
                project_id, "decision_ledger", {"decisions": all_decisions}
            )
        except Exception as e:
            self.logger.warning(f"Could not persist decision to context_engine: {e}")
        
        self.logger.info(
            f"📋 Decision recorded: [{agent_name}] {decision_type}: {decision}"
        )
        
        return decision_id
    
    def get_decisions(
        self,
        project_id: str,
        agent_name: str = None,
        decision_type: str = None
    ) -> List[Dict]:
        """Get decisions, optionally filtered."""
        decisions = self._decisions.get(project_id, [])
        
        if agent_name:
            decisions = [d for d in decisions if d["agent"] == agent_name]
        if decision_type:
            decisions = [d for d in decisions if d["type"] == decision_type]
        
        return decisions
    
    def explain_decision(self, project_id: str, decision_id: str) -> Optional[Dict]:
        """Get full explanation for a specific decision."""
        for d in self._decisions.get(project_id, []):
            if d["id"] == decision_id:
                return d
        return None
    
    def get_support_summary(self, project_id: str) -> str:
        """Generate human-readable summary for support team."""
        decisions = self._decisions.get(project_id, [])
        
        if not decisions:
            return "No decisions recorded for this project."
        
        summary_lines = [f"# Decision History — Project {project_id}\n"]
        summary_lines.append(f"Total decisions: {len(decisions)}\n")
        
        for d in decisions:
            summary_lines.append(
                f"## [{d['agent'].upper()}] {d['type']}\n"
                f"**Decision:** {d['decision']}\n"
                f"**Why:** {d['reasoning']}\n"
                f"**Alternatives considered:** {', '.join(d['alternatives']) if d['alternatives'] else 'None'}\n"
                f"**Confidence:** {d['confidence']:.0%}\n"
            )
        
        return "\n".join(summary_lines)


decision_ledger = DecisionLedger()
