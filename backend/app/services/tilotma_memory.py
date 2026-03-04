"""Tilotma Memory — Dedicated memory system for the PM agent.

Tilotma's private memory space for deep user understanding, separate from
the shared pipeline context. This is HER evolving understanding of
conversations, projects, and quality concerns.

Persistence: Uses context_engine (Valkey-backed) for cross-process state.
Tilotma is a singleton agent — memory is keyed by pipeline_run_id so
concurrent pipeline runs don't interfere.

Features:
- Conversation history with user
- Project understanding evolution (confidence, features, missing info)
- Intervention history (shadow monitoring records)
- Quality concerns tracking
- Monitoring log (capped at 100 entries)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ── Data Models ────────────────────────────────────────────────────


@dataclass
class ConversationEntry:
    """Single conversation exchange in Tilotma's memory."""

    timestamp: str  # ISO format
    user_message: str
    tilotma_response: str
    understanding: str  # What Tilotma understood from this exchange
    concerns: list[str] = field(default_factory=list)
    decisions_made: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "user_message": self.user_message,
            "tilotma_response": self.tilotma_response,
            "understanding": self.understanding,
            "concerns": self.concerns,
            "decisions_made": self.decisions_made,
        }


@dataclass
class ProjectUnderstanding:
    """Tilotma's evolving understanding of the project."""

    initial_request: str
    current_understanding: str
    confidence_level: float  # 0.0 to 1.0
    project_type: str | None = None
    key_features: list[str] = field(default_factory=list)
    user_confirmed: bool = False
    missing_information: list[str] = field(default_factory=list)
    assumptions_made: list[str] = field(default_factory=list)
    risks_identified: list[str] = field(default_factory=list)
    quality_concerns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "initial_request": self.initial_request,
            "current_understanding": self.current_understanding,
            "confidence_level": self.confidence_level,
            "project_type": self.project_type,
            "key_features": self.key_features,
            "user_confirmed": self.user_confirmed,
            "missing_information": self.missing_information,
            "assumptions_made": self.assumptions_made,
            "risks_identified": self.risks_identified,
            "quality_concerns": self.quality_concerns,
        }


# ── Tilotma Memory ────────────────────────────────────────────────


class TilotmaMemory:
    """Tilotma's dedicated memory system.

    Separate from the shared pipeline context — this is Tilotma's private
    understanding of users and projects. Persisted via context_engine so
    it survives process restarts and Celery worker recycling.

    State is keyed by pipeline_run_id (one memory instance per pipeline run).
    """

    _CONTEXT_KEY = "tilotma_private_memory"
    _MAX_MONITORING_ENTRIES = 100

    def __init__(self, pipeline_run_id: str) -> None:
        self.pipeline_run_id = pipeline_run_id

        # In-memory state (loaded from / saved to context_engine)
        self.conversation_history: list[ConversationEntry] = []
        self.project_understanding: ProjectUnderstanding | None = None
        self.intervention_history: list[dict[str, Any]] = []
        self.monitoring_log: list[dict[str, Any]] = []

    # ── Persistence ────────────────────────────────────────────────

    async def load(self) -> None:
        """Load memory from context_engine (Valkey)."""
        from app.agents.base import get_step_context

        data = await get_step_context(self.pipeline_run_id, self._CONTEXT_KEY)
        if not data:
            return

        # Restore conversation history
        for entry_dict in data.get("conversation_history", []):
            self.conversation_history.append(ConversationEntry(**entry_dict))

        # Restore project understanding
        pu = data.get("project_understanding")
        if pu:
            self.project_understanding = ProjectUnderstanding(**pu)

        # Restore intervention history + monitoring log
        self.intervention_history = data.get("intervention_history", [])
        self.monitoring_log = data.get("monitoring_log", [])

        logger.info(
            "tilotma_memory_loaded",
            pipeline_run_id=self.pipeline_run_id,
            conversations=len(self.conversation_history),
            has_understanding=self.project_understanding is not None,
        )

    async def save(self) -> None:
        """Save memory to context_engine (Valkey)."""
        from app.services.context_engine import get_context_engine

        data = {
            "conversation_history": [e.to_dict() for e in self.conversation_history],
            "project_understanding": (
                self.project_understanding.to_dict()
                if self.project_understanding
                else None
            ),
            "intervention_history": self.intervention_history,
            "monitoring_log": self.monitoring_log,
            "last_updated": time.time(),
        }

        try:
            engine = get_context_engine()
            await engine.store(self.pipeline_run_id, self._CONTEXT_KEY, data)
        except Exception as exc:
            logger.warning(
                "tilotma_memory_save_failed",
                pipeline_run_id=self.pipeline_run_id,
                error=str(exc)[:200],
            )

    # ── Conversation ───────────────────────────────────────────────

    def add_conversation(
        self,
        user_message: str,
        tilotma_response: str,
        understanding: str,
        concerns: list[str] | None = None,
        decisions: list[str] | None = None,
    ) -> None:
        """Add a conversation exchange to memory."""
        entry = ConversationEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            user_message=user_message,
            tilotma_response=tilotma_response,
            understanding=understanding,
            concerns=concerns or [],
            decisions_made=decisions or [],
        )
        self.conversation_history.append(entry)

    def get_recent_conversations(self, count: int = 10) -> list[ConversationEntry]:
        """Get the most recent conversation entries."""
        return self.conversation_history[-count:]

    def get_conversation_context(self) -> str:
        """Build conversation context string for AI prompts."""
        if not self.conversation_history:
            return "No previous conversation."

        lines: list[str] = []
        for entry in self.conversation_history[-10:]:
            lines.append(f"User: {entry.user_message}")
            lines.append(f"Tilotma: {entry.tilotma_response}")
        return "\n".join(lines)

    # ── Project Understanding ──────────────────────────────────────

    def update_understanding(
        self,
        understanding: str,
        confidence: float,
        project_type: str | None = None,
        key_features: list[str] | None = None,
        user_confirmed: bool = False,
        missing_info: list[str] | None = None,
        assumptions: list[str] | None = None,
        risks: list[str] | None = None,
    ) -> None:
        """Update Tilotma's evolving understanding of the project."""
        if not self.project_understanding:
            self.project_understanding = ProjectUnderstanding(
                initial_request=understanding,
                current_understanding=understanding,
                confidence_level=confidence,
                project_type=project_type,
                key_features=key_features or [],
                user_confirmed=user_confirmed,
            )
        else:
            self.project_understanding.current_understanding = understanding
            self.project_understanding.confidence_level = confidence
            if project_type:
                self.project_understanding.project_type = project_type
            if key_features:
                self.project_understanding.key_features = key_features
            if user_confirmed:
                self.project_understanding.user_confirmed = user_confirmed

        if missing_info is not None:
            self.project_understanding.missing_information = missing_info
        if assumptions is not None:
            self.project_understanding.assumptions_made = assumptions
        if risks is not None:
            self.project_understanding.risks_identified = risks

    def is_requirements_complete(self) -> bool:
        """Check if we have enough information to start development."""
        if not self.project_understanding:
            return False
        pu = self.project_understanding
        return (
            pu.project_type is not None
            and len(pu.key_features) >= 3
            and pu.user_confirmed
            and pu.confidence_level >= 0.7
        )

    # ── Quality & Monitoring ───────────────────────────────────────

    def add_quality_concern(self, concern: str) -> None:
        """Add a quality concern detected during monitoring."""
        if self.project_understanding:
            self.project_understanding.quality_concerns.append(concern)

    def record_intervention(
        self,
        agent_name: str,
        issue: str,
        action_taken: str,
    ) -> None:
        """Record an intervention Tilotma made on an agent."""
        self.intervention_history.append({
            "timestamp": time.time(),
            "agent": agent_name,
            "issue": issue,
            "action": action_taken,
        })

    def add_monitoring_entry(self, agent_name: str, report: dict[str, Any]) -> None:
        """Add entry to shadow monitoring log (capped)."""
        self.monitoring_log.append({
            "timestamp": time.time(),
            "agent": agent_name,
            "report_summary": str(report)[:500],  # Cap report size
        })
        # Prevent unbounded growth
        if len(self.monitoring_log) > self._MAX_MONITORING_ENTRIES:
            self.monitoring_log = self.monitoring_log[-self._MAX_MONITORING_ENTRIES:]

    # ── Summary ────────────────────────────────────────────────────

    def get_context_summary(self) -> str:
        """Get a summary of Tilotma's understanding for sharing with AI."""
        if not self.project_understanding:
            return "No project understanding yet."

        pu = self.project_understanding
        missing = ", ".join(pu.missing_information) if pu.missing_information else "None"
        concerns = ", ".join(pu.quality_concerns) if pu.quality_concerns else "None"

        return (
            f"Project Understanding (Confidence: {pu.confidence_level:.0%}):\n"
            f"{pu.current_understanding}\n\n"
            f"Project Type: {pu.project_type or 'Unknown'}\n"
            f"Key Features: {', '.join(pu.key_features) if pu.key_features else 'None'}\n"
            f"Missing Information: {missing}\n"
            f"Quality Concerns: {concerns}\n"
            f"Interventions Made: {len(self.intervention_history)}"
        )
