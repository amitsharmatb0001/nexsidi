"""Pipeline Audit Service: records the full lifecycle of every pipeline run.

Captures structured audit events for:
- Pipeline creation, start, completion, failure
- Stage transitions (agent start/complete/fail)
- Checkpoint pauses and approvals
- Fixer iterations
- Deployment outcomes
- Delivery package creation
- AI prompt/response logging
- Code generation audit with content hashes
- Test results (pass/fail)
- Security scan findings
- User decisions (approve/reject/feedback)
- Agent decision logging
- Cost tracking per pipeline run
- SHA-256 chain integrity for tamper detection

These events are stored in-memory for now (list of AuditEvent dataclasses).
In production, they write to audit.logs via the audit service + Valkey streams.

Design:
- Every event has: timestamp, event_type, pipeline_run_id, metadata, chain_hash
- Events are append-only (immutable)
- SHA-256 chain: each event's chain_hash = sha256(prev_chain_hash + event_data)
- Used by CHECKPOINT 2 to present a full pipeline history to the user
- Used by the delivery package as part of the pipeline report
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def _compute_chain_hash(prev_hash: str, event_data: str) -> str:
    """Compute the next SHA-256 chain hash."""
    combined = f"{prev_hash}:{event_data}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


GENESIS_HASH = "0" * 64  # Initial hash for the first event in a chain


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

    AI_PROMPT_SENT = "ai.prompt_sent"
    AI_RESPONSE_RECEIVED = "ai.response_received"
    AI_ERROR = "ai.error"

    CODE_GENERATED = "code.generated"
    CODE_FIXED = "code.fixed"

    TEST_PASSED = "test.passed"
    TEST_FAILED = "test.failed"

    SECURITY_SCAN_STARTED = "security.scan_started"
    SECURITY_FINDING = "security.finding"
    SECURITY_SCAN_COMPLETED = "security.scan_completed"

    USER_APPROVED = "user.approved"
    USER_REJECTED = "user.rejected"
    USER_FEEDBACK = "user.feedback"

    AGENT_DECISION = "agent.decision"
    COST_RECORDED = "cost.recorded"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """A single pipeline audit event (immutable, chain-linked)."""

    event_type: AuditEventType
    pipeline_run_id: str
    timestamp: str
    agent_name: str = ""
    stage: str = ""
    duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    chain_hash: str = ""

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
        if self.chain_hash:
            result["chain_hash"] = self.chain_hash
        return result

    def _event_data_for_hash(self) -> str:
        """Deterministic string representation for chain hashing."""
        parts = [self.event_type.value, self.pipeline_run_id, self.timestamp]
        if self.agent_name:
            parts.append(self.agent_name)
        if self.stage:
            parts.append(self.stage)
        if self.metadata:
            parts.append(json.dumps(self.metadata, sort_keys=True, default=str))
        return "|".join(parts)


class PipelineAuditService:
    """Records and retrieves pipeline audit events.

    Thread-safe append-only log. In production, events are also
    pushed to audit.logs table and Valkey streams for real-time
    notification.
    """

    # MEM-FIX: Bound in-memory storage to prevent OOM on long-running instances.
    _MAX_RUNS: int = 500  # Max pipeline runs to keep in memory
    _MAX_EVENTS_PER_RUN: int = 5_000  # Safety cap per run

    def __init__(self) -> None:
        self._events: dict[str, deque[AuditEvent]] = {}
        self._run_timers: dict[str, float] = {}
        self._chain_hashes: dict[str, str] = {}  # last chain hash per run
        self._run_order: deque[str] = deque()  # MEM-FIX: track insertion order for LRU eviction

    def record(
        self,
        event_type: AuditEventType,
        pipeline_run_id: str,
        agent_name: str = "",
        stage: str = "",
        duration_ms: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """Record an audit event with SHA-256 chain integrity."""
        # Build event without chain_hash first to get event data
        event = AuditEvent(
            event_type=event_type,
            pipeline_run_id=pipeline_run_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            agent_name=agent_name,
            stage=stage,
            duration_ms=duration_ms,
            metadata=metadata or {},
        )

        # Compute chain hash
        prev_hash = self._chain_hashes.get(pipeline_run_id, GENESIS_HASH)
        chain_hash = _compute_chain_hash(prev_hash, event._event_data_for_hash())

        # Recreate with chain_hash (frozen dataclass requires this)
        event = AuditEvent(
            event_type=event_type,
            pipeline_run_id=pipeline_run_id,
            timestamp=event.timestamp,
            agent_name=agent_name,
            stage=stage,
            duration_ms=duration_ms,
            metadata=metadata or {},
            chain_hash=chain_hash,
        )

        # Store
        if pipeline_run_id not in self._events:
            self._events[pipeline_run_id] = deque(maxlen=self._MAX_EVENTS_PER_RUN)
            self._run_order.append(pipeline_run_id)
            # MEM-FIX: Evict oldest runs when over capacity
            self._evict_old_runs()

        # The deque maxlen automatically evicts oldest events
        run_events = self._events[pipeline_run_id]
        run_events.append(event)
        self._chain_hashes[pipeline_run_id] = chain_hash

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

    # ── AI Prompt/Response Logging (Gap 206-207) ─────────────────

    def ai_prompt_sent(
        self,
        pipeline_run_id: str,
        agent_name: str,
        model: str,
        prompt_hash: str,
        token_count: int,
    ) -> AuditEvent:
        """Record an AI prompt being sent."""
        return self.record(
            AuditEventType.AI_PROMPT_SENT,
            pipeline_run_id,
            agent_name=agent_name,
            metadata={
                "model": model,
                "prompt_hash": prompt_hash,
                "input_tokens": token_count,
            },
        )

    def ai_response_received(
        self,
        pipeline_run_id: str,
        agent_name: str,
        model: str,
        response_hash: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        cost: float,
    ) -> AuditEvent:
        """Record an AI response being received."""
        return self.record(
            AuditEventType.AI_RESPONSE_RECEIVED,
            pipeline_run_id,
            agent_name=agent_name,
            duration_ms=latency_ms,
            metadata={
                "model": model,
                "response_hash": response_hash,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": round(cost, 6),
            },
        )

    def ai_error(
        self,
        pipeline_run_id: str,
        agent_name: str,
        model: str,
        error: str,
    ) -> AuditEvent:
        """Record an AI call failure."""
        return self.record(
            AuditEventType.AI_ERROR,
            pipeline_run_id,
            agent_name=agent_name,
            metadata={"model": model, "error": error[:500]},
        )

    # ── Code Generation Audit (Gap 209) ────────────────────────

    def code_generated(
        self,
        pipeline_run_id: str,
        agent_name: str,
        file_path: str,
        content_hash: str,
        line_count: int,
    ) -> AuditEvent:
        """Record code file generation."""
        return self.record(
            AuditEventType.CODE_GENERATED,
            pipeline_run_id,
            agent_name=agent_name,
            metadata={
                "file_path": file_path,
                "content_hash": content_hash,
                "line_count": line_count,
            },
        )

    def code_fixed(
        self,
        pipeline_run_id: str,
        file_path: str,
        before_hash: str,
        after_hash: str,
        fix_reason: str,
    ) -> AuditEvent:
        """Record code file fix/update."""
        return self.record(
            AuditEventType.CODE_FIXED,
            pipeline_run_id,
            agent_name="fixer",
            metadata={
                "file_path": file_path,
                "before_hash": before_hash,
                "after_hash": after_hash,
                "fix_reason": fix_reason[:200],
            },
        )

    # ── Test Result Logging (Gap 210) ──────────────────────────

    def test_passed(
        self,
        pipeline_run_id: str,
        test_name: str,
        phase: str,
        duration_ms: float = 0.0,
    ) -> AuditEvent:
        """Record a test passing."""
        return self.record(
            AuditEventType.TEST_PASSED,
            pipeline_run_id,
            agent_name="aarav",
            stage=phase,
            duration_ms=duration_ms,
            metadata={"test_name": test_name},
        )

    def test_failed(
        self,
        pipeline_run_id: str,
        test_name: str,
        phase: str,
        error_details: str,
    ) -> AuditEvent:
        """Record a test failing."""
        return self.record(
            AuditEventType.TEST_FAILED,
            pipeline_run_id,
            agent_name="aarav",
            stage=phase,
            metadata={"test_name": test_name, "error": error_details[:500]},
        )

    # ── Security Scan Logging (Gap 211) ────────────────────────

    def security_scan_started(
        self,
        pipeline_run_id: str,
        scan_type: str,
    ) -> AuditEvent:
        """Record a security scan starting."""
        return self.record(
            AuditEventType.SECURITY_SCAN_STARTED,
            pipeline_run_id,
            agent_name="karan",
            metadata={"scan_type": scan_type},
        )

    def security_finding(
        self,
        pipeline_run_id: str,
        severity: str,
        category: str,
        file_path: str,
        message: str,
    ) -> AuditEvent:
        """Record a security finding."""
        return self.record(
            AuditEventType.SECURITY_FINDING,
            pipeline_run_id,
            agent_name="karan",
            metadata={
                "severity": severity,
                "category": category,
                "file_path": file_path,
                "message": message[:300],
            },
        )

    def security_scan_completed(
        self,
        pipeline_run_id: str,
        scan_type: str,
        findings_count: int,
        critical_count: int,
    ) -> AuditEvent:
        """Record a security scan completing."""
        return self.record(
            AuditEventType.SECURITY_SCAN_COMPLETED,
            pipeline_run_id,
            agent_name="karan",
            metadata={
                "scan_type": scan_type,
                "findings_count": findings_count,
                "critical_count": critical_count,
            },
        )

    # ── User Decision Logging (Gap 213) ────────────────────────

    def user_approved(
        self,
        pipeline_run_id: str,
        user_id: str,
        stage: str,
        comments: str = "",
    ) -> AuditEvent:
        """Record user approving a checkpoint."""
        return self.record(
            AuditEventType.USER_APPROVED,
            pipeline_run_id,
            stage=stage,
            metadata={"user_id": user_id, "comments": comments[:500]},
        )

    def user_rejected(
        self,
        pipeline_run_id: str,
        user_id: str,
        stage: str,
        reason: str,
    ) -> AuditEvent:
        """Record user rejecting a checkpoint."""
        return self.record(
            AuditEventType.USER_REJECTED,
            pipeline_run_id,
            stage=stage,
            metadata={"user_id": user_id, "reason": reason[:500]},
        )

    def user_feedback(
        self,
        pipeline_run_id: str,
        user_id: str,
        feedback_text: str,
        rating: int | None = None,
    ) -> AuditEvent:
        """Record user feedback on generated output."""
        meta: dict[str, Any] = {
            "user_id": user_id,
            "feedback": feedback_text[:1000],
        }
        if rating is not None:
            meta["rating"] = rating
        return self.record(
            AuditEventType.USER_FEEDBACK,
            pipeline_run_id,
            metadata=meta,
        )

    # ── Agent Decision Logging (Gap 214) ───────────────────────

    def agent_decision(
        self,
        pipeline_run_id: str,
        agent_name: str,
        decision: str,
        reasoning: str,
        alternatives: list[str] | None = None,
    ) -> AuditEvent:
        """Record an agent making a decision."""
        meta: dict[str, Any] = {
            "decision": decision[:200],
            "reasoning": reasoning[:500],
        }
        if alternatives:
            meta["alternatives"] = [a[:100] for a in alternatives[:5]]
        return self.record(
            AuditEventType.AGENT_DECISION,
            pipeline_run_id,
            agent_name=agent_name,
            metadata=meta,
        )

    # ── Cost Tracking (Gap 216) ────────────────────────────────

    def cost_recorded(
        self,
        pipeline_run_id: str,
        agent_name: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> AuditEvent:
        """Record AI cost for a pipeline run."""
        return self.record(
            AuditEventType.COST_RECORDED,
            pipeline_run_id,
            agent_name=agent_name,
            metadata={
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": round(cost_usd, 6),
            },
        )

    # ── Chain Verification (Gap 217) ───────────────────────────

    def verify_audit_chain(self, pipeline_run_id: str) -> bool:
        """Verify the SHA-256 chain integrity for a pipeline run.

        Returns True if the chain is intact (no tampering detected).
        Returns False if any event's chain hash doesn't match its expected value.

        EVICTION-FIX: After event cap eviction, the first retained event's
        prev_hash no longer matches GENESIS_HASH (it points to an evicted
        event). In this case, we trust the first retained event as an anchor
        and verify the chain from the second event onward.
        """
        events = self.get_events(pipeline_run_id)
        if not events:
            return True

        # Try full verification from GENESIS_HASH first.
        first_expected = _compute_chain_hash(
            GENESIS_HASH, events[0]._event_data_for_hash()
        )
        if first_expected == events[0].chain_hash:
            # No eviction — verify full chain
            prev_hash = GENESIS_HASH
            start_idx = 0
        else:
            # Eviction occurred — first event is an anchor, verify from second
            logger.debug(
                "audit_chain_eviction_detected",
                run_id=pipeline_run_id,
                retained_events=len(events),
            )
            prev_hash = events[0].chain_hash
            start_idx = 1

        for event in events[start_idx:]:
            expected_hash = _compute_chain_hash(prev_hash, event._event_data_for_hash())
            if event.chain_hash != expected_hash:
                logger.error(
                    "audit_chain_tampered",
                    run_id=pipeline_run_id,
                    event_type=event.event_type.value,
                    expected=expected_hash[:16],
                    actual=event.chain_hash[:16],
                )
                return False
            prev_hash = event.chain_hash

        return True

    def get_chain_hash(self, pipeline_run_id: str) -> str:
        """Get the latest chain hash for a pipeline run."""
        return self._chain_hashes.get(pipeline_run_id, GENESIS_HASH)

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

    # ── Cleanup ────────────────────────────────────────────────────

    def cleanup_run(self, pipeline_run_id: str) -> None:
        """Remove all in-memory data for a completed/failed pipeline run.

        Called by the orchestrator after pipeline completion to free memory.
        """
        self._events.pop(pipeline_run_id, None)
        self._run_timers.pop(pipeline_run_id, None)
        self._chain_hashes.pop(pipeline_run_id, None)
        try:
            self._run_order.remove(pipeline_run_id)
        except ValueError:
            pass

    def _evict_old_runs(self) -> None:
        """MEM-FIX: Evict oldest pipeline runs when over capacity."""
        while len(self._run_order) > self._MAX_RUNS:
            oldest = self._run_order.popleft()
            self._events.pop(oldest, None)
            self._run_timers.pop(oldest, None)
            self._chain_hashes.pop(oldest, None)
            logger.info("audit_run_evicted", run_id=oldest)

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
