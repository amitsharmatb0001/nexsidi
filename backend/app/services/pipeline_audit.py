"""Pipeline Audit Service: records the full lifecycle of every pipeline run.

Captures structured audit events for:
- Pipeline creation, start, completion, failure
- Stage transitions (agent start/complete/fail)
- Checkpoint pauses and approvals
- Fixer iterations
- Deployment outcomes
- Delivery package creation

These events are stored in-memory for now (list of AuditEvent dataclasses).
In production, they write to audit.logs via the audit service + Valkey streams.

Design:
- Every event has: timestamp, event_type, pipeline_run_id, metadata
- Events are append-only (immutable)
- Used by CHECKPOINT 2 to present a full pipeline history to the user
- Used by the delivery package as part of the pipeline report
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class AuditEventType(str, Enum):
    """Types of pipeline audit events."""

    PIPELINE_CREATED = "pipeline.created"
    PIPELINE_STARTED = "pipeline.started"
    PIPELINE_COMPLETED = "pipeline.completed"
    PIPELINE_FAILED = "pipeline.failed"
    PIPELINE_CANCELLED = "pipeline.cancelled"

    STAGE_STARTED = "stage.started"
    STAGE_COMPLETED = "stage.completed"
    STAGE_FAILED = "stage.failed"
    STAGE_SKIPPED = "stage.skipped"

    CHECKPOINT_PAUSED = "checkpoint.paused"
    CHECKPOINT_APPROVED = "checkpoint.approved"
    CHECKPOINT_REJECTED = "checkpoint.rejected"

    AGENT_STARTED = "agent.started"
    AGENT_COMPLETED = "agent.completed"
    AGENT_FAILED = "agent.failed"

    FIXER_ITERATION = "fixer.iteration"
    FIXER_COMPLETED = "fixer.completed"

    DEPLOY_STARTED = "deploy.started"
    DEPLOY_COMPLETED = "deploy.completed"
    DEPLOY_FAILED = "deploy.failed"

    DELIVERY_CREATED = "delivery.created"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """A single pipeline audit event (immutable)."""

    event_type: AuditEventType
    pipeline_run_id: str
    timestamp: str
    agent_name: str = ""
    stage: str = ""
    duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "event_type": self.event_type.value,
            "pipeline_run_id": self.pipeline_run_id,
            "timestamp": self.timestamp,
        }
        if self.agent_name:
            result["agent_name"] = self.agent_name
        if self.stage:
            result["stage"] = self.stage
        if self.duration_ms > 0:
            result["duration_ms"] = round(self.duration_ms, 1)
        if self.metadata:
            result["metadata"] = self.metadata
        return result


class PipelineAuditService:
    """Records and retrieves pipeline audit events.

    Thread-safe append-only log. In production, events are also
    pushed to audit.logs table and Valkey streams for real-time
    notification.
    """

    def __init__(self) -> None:
        self._events: dict[str, list[AuditEvent]] = {}
        self._run_timers: dict[str, float] = {}

    def record(
        self,
        event_type: AuditEventType,
        pipeline_run_id: str,
        agent_name: str = "",
        stage: str = "",
        duration_ms: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """Record an audit event."""
        event = AuditEvent(
            event_type=event_type,
            pipeline_run_id=pipeline_run_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            agent_name=agent_name,
            stage=stage,
            duration_ms=duration_ms,
            metadata=metadata or {},
        )

        if pipeline_run_id not in self._events:
            self._events[pipeline_run_id] = []
        self._events[pipeline_run_id].append(event)

        logger.info(
            "pipeline_audit",
            event_type=event_type.value,
            run_id=pipeline_run_id,
            agent=agent_name or None,
            stage=stage or None,
        )

        return event

    def pipeline_created(
        self,
        pipeline_run_id: str,
        project_id: str,
        organization_id: str,
        user_id: str,
        execution_mode: str,
    ) -> AuditEvent:
        """Record pipeline creation."""
        self._run_timers[pipeline_run_id] = time.monotonic()
        return self.record(
            AuditEventType.PIPELINE_CREATED,
            pipeline_run_id,
            metadata={
                "project_id": project_id,
                "organization_id": organization_id,
                "user_id": user_id,
                "execution_mode": execution_mode,
            },
        )

    def pipeline_started(self, pipeline_run_id: str) -> AuditEvent:
        """Record pipeline execution start."""
        return self.record(AuditEventType.PIPELINE_STARTED, pipeline_run_id)

    def pipeline_completed(self, pipeline_run_id: str) -> AuditEvent:
        """Record pipeline successful completion."""
        elapsed = self._elapsed(pipeline_run_id)
        return self.record(
            AuditEventType.PIPELINE_COMPLETED,
            pipeline_run_id,
            duration_ms=elapsed,
        )

    def pipeline_failed(self, pipeline_run_id: str, error: str) -> AuditEvent:
        """Record pipeline failure."""
        elapsed = self._elapsed(pipeline_run_id)
        return self.record(
            AuditEventType.PIPELINE_FAILED,
            pipeline_run_id,
            duration_ms=elapsed,
            metadata={"error": error[:500]},
        )

    def stage_started(self, pipeline_run_id: str, stage: str, agent_name: str) -> AuditEvent:
        """Record a stage beginning execution."""
        return self.record(
            AuditEventType.STAGE_STARTED,
            pipeline_run_id,
            agent_name=agent_name,
            stage=stage,
        )

    def stage_completed(
        self,
        pipeline_run_id: str,
        stage: str,
        agent_name: str,
        duration_ms: float = 0.0,
    ) -> AuditEvent:
        """Record a stage completing."""
        return self.record(
            AuditEventType.STAGE_COMPLETED,
            pipeline_run_id,
            agent_name=agent_name,
            stage=stage,
            duration_ms=duration_ms,
        )

    def stage_failed(
        self,
        pipeline_run_id: str,
        stage: str,
        agent_name: str,
        error: str,
    ) -> AuditEvent:
        """Record a stage failure."""
        return self.record(
            AuditEventType.STAGE_FAILED,
            pipeline_run_id,
            agent_name=agent_name,
            stage=stage,
            metadata={"error": error[:500]},
        )

    def checkpoint_paused(self, pipeline_run_id: str, stage: str) -> AuditEvent:
        """Record pipeline pausing at checkpoint."""
        return self.record(
            AuditEventType.CHECKPOINT_PAUSED,
            pipeline_run_id,
            stage=stage,
        )

    def checkpoint_approved(self, pipeline_run_id: str, stage: str, user_id: str) -> AuditEvent:
        """Record checkpoint approval."""
        return self.record(
            AuditEventType.CHECKPOINT_APPROVED,
            pipeline_run_id,
            stage=stage,
            metadata={"approved_by": user_id},
        )

    def checkpoint_rejected(self, pipeline_run_id: str, stage: str, user_id: str, reason: str = "") -> AuditEvent:
        """Record checkpoint rejection."""
        return self.record(
            AuditEventType.CHECKPOINT_REJECTED,
            pipeline_run_id,
            stage=stage,
            metadata={"rejected_by": user_id, "reason": reason[:500]},
        )

    def deploy_started(self, pipeline_run_id: str, provider: str) -> AuditEvent:
        """Record deployment start."""
        return self.record(
            AuditEventType.DEPLOY_STARTED,
            pipeline_run_id,
            agent_name="pranav",
            metadata={"provider": provider},
        )

    def deploy_completed(self, pipeline_run_id: str, url: str, provider: str) -> AuditEvent:
        """Record deployment success."""
        return self.record(
            AuditEventType.DEPLOY_COMPLETED,
            pipeline_run_id,
            agent_name="pranav",
            metadata={"deployment_url": url, "provider": provider},
        )

    def deploy_failed(self, pipeline_run_id: str, error: str) -> AuditEvent:
        """Record deployment failure."""
        return self.record(
            AuditEventType.DEPLOY_FAILED,
            pipeline_run_id,
            agent_name="pranav",
            metadata={"error": error[:500]},
        )

    def delivery_created(
        self,
        pipeline_run_id: str,
        total_files: int,
        zip_size_kb: int,
    ) -> AuditEvent:
        """Record delivery package creation."""
        return self.record(
            AuditEventType.DELIVERY_CREATED,
            pipeline_run_id,
            metadata={"total_files": total_files, "zip_size_kb": zip_size_kb},
        )

    # ── Query ─────────────────────────────────────────────────────

    def get_events(self, pipeline_run_id: str) -> list[AuditEvent]:
        """Get all audit events for a pipeline run."""
        return list(self._events.get(pipeline_run_id, []))

    def get_events_by_type(
        self,
        pipeline_run_id: str,
        event_type: AuditEventType,
    ) -> list[AuditEvent]:
        """Get events of a specific type for a pipeline run."""
        return [
            e for e in self._events.get(pipeline_run_id, [])
            if e.event_type == event_type
        ]

    def get_timeline(self, pipeline_run_id: str) -> list[dict[str, Any]]:
        """Get a serializable timeline for a pipeline run."""
        return [e.to_dict() for e in self.get_events(pipeline_run_id)]

    def event_count(self, pipeline_run_id: str) -> int:
        """Total events recorded for a pipeline run."""
        return len(self._events.get(pipeline_run_id, []))

    # ── Internal ──────────────────────────────────────────────────

    def _elapsed(self, pipeline_run_id: str) -> float:
        """Milliseconds since pipeline started."""
        start = self._run_timers.get(pipeline_run_id)
        if start is None:
            return 0.0
        return (time.monotonic() - start) * 1000


# ── Singleton ───────────────────────────────────────────────────

_service: PipelineAuditService | None = None


def get_pipeline_audit() -> PipelineAuditService:
    """Get or create the pipeline audit service singleton."""
    global _service
    if _service is None:
        _service = PipelineAuditService()
    return _service
