"""Checkpoint service: pause pipeline for user approval.

Checkpoints are the human-in-the-loop gates in the NexSidi pipeline.
When a checkpoint is reached, the pipeline pauses and presents the
accumulated context to the user for review.

CHECKPOINT 1 (after design): Shows architecture contract, DB schema,
    UI/UX design specs, compliance flags. User can approve, request
    changes, or partially approve.

CHECKPOINT 2 (after testing): Shows test results, live preview URL,
    security audit report, compliance check. User approves for deploy.

Timeout: 7 days. After that, the pipeline is cancelled.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class CheckpointType(str, Enum):
    """Types of checkpoints in the pipeline."""

    DESIGN = "design"       # After Vikram + Dhruv + Vanya
    TESTING = "testing"     # After Aarav + Karan + compliance


class ApprovalStatus(str, Enum):
    """Status of a checkpoint approval."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"
    PARTIALLY_APPROVED = "partially_approved"
    EXPIRED = "expired"


@dataclass(slots=True)
class CheckpointData:
    """Data presented to the user at a checkpoint."""

    checkpoint_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    checkpoint_type: CheckpointType = CheckpointType.DESIGN
    pipeline_run_id: str = ""
    project_id: str = ""
    organization_id: str = ""
    user_id: str = ""

    # Accumulated data for review
    summary: dict[str, Any] = field(default_factory=dict)
    architecture_contract: dict[str, Any] | None = None
    database_schema: str | None = None
    design_specs: dict[str, Any] | None = None
    test_results: dict[str, Any] | None = None
    security_report: dict[str, Any] | None = None

    # Approval tracking
    status: ApprovalStatus = ApprovalStatus.PENDING
    reviewer_comments: str | None = None
    change_requests: list[str] = field(default_factory=list)
    approved_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # 7-day timeout
    expires_at: datetime | None = None


class CheckpointService:
    """Manages checkpoint creation, presentation, and approval."""

    # R32-FIX-MEM: Maximum checkpoints in memory. Once exceeded, oldest
    # terminal (approved/rejected/expired) checkpoints are evicted.
    _MAX_CHECKPOINTS = 500

    def __init__(self) -> None:
        self._checkpoints: dict[str, CheckpointData] = {}

    def create_design_checkpoint(
        self,
        pipeline_run_id: str,
        project_id: str,
        organization_id: str,
        user_id: str,
        context: dict[str, Any],
    ) -> CheckpointData:
        """Create CHECKPOINT 1 from accumulated design context.

        Presents: architecture contract, DB schema, UI/UX specs.
        """
        vikram = context.get("vikram", {})
        dhruv = context.get("dhruv", {})
        vanya = context.get("vanya", {})
        saanvi = context.get("saanvi", {})
        tilotma = context.get("tilotma", {})

        # Build summary for quick review
        contract = vikram.get("contract", {})
        summary = {
            "project_name": contract.get("project_name", "Unknown"),
            "tables": vikram.get("stats", {}).get("tables", 0),
            "endpoints": vikram.get("stats", {}).get("endpoints", 0),
            "pages": vikram.get("stats", {}).get("pages", 0),
            "complexity": "See Saanvi's analysis",
            "compliance_flags": tilotma.get("compliance_auto_detected", []),
            "contract_valid": vikram.get("is_valid", False),
            "validation_errors": vikram.get("validation_errors", []),
        }

        from datetime import timedelta

        checkpoint = CheckpointData(
            checkpoint_type=CheckpointType.DESIGN,
            pipeline_run_id=pipeline_run_id,
            project_id=project_id,
            organization_id=organization_id,
            user_id=user_id,
            summary=summary,
            architecture_contract=contract,
            database_schema=dhruv.get("database_artifacts"),
            design_specs=vanya.get("design_spec"),
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )

        self._checkpoints[checkpoint.checkpoint_id] = checkpoint
        self._evict_terminal()

        logger.info(
            "checkpoint_created",
            type=checkpoint.checkpoint_type.value,
            checkpoint_id=checkpoint.checkpoint_id,
            pipeline_run_id=pipeline_run_id,
        )

        return checkpoint

    def create_testing_checkpoint(
        self,
        pipeline_run_id: str,
        project_id: str,
        organization_id: str,
        user_id: str,
        context: dict[str, Any],
    ) -> CheckpointData:
        """Create CHECKPOINT 2 from test and security context."""
        from datetime import timedelta

        checkpoint = CheckpointData(
            checkpoint_type=CheckpointType.TESTING,
            pipeline_run_id=pipeline_run_id,
            project_id=project_id,
            organization_id=organization_id,
            user_id=user_id,
            summary={
                "test_status": "See test results",
                "security_status": "See security report",
            },
            test_results=context.get("aarav", {}),
            security_report=context.get("karan", {}),
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )

        self._checkpoints[checkpoint.checkpoint_id] = checkpoint
        self._evict_terminal()

        logger.info(
            "checkpoint_created",
            type=checkpoint.checkpoint_type.value,
            checkpoint_id=checkpoint.checkpoint_id,
            pipeline_run_id=pipeline_run_id,
        )

        return checkpoint

    def _evict_terminal(self) -> None:
        """R32-FIX-MEM: Remove oldest terminal checkpoints when over capacity."""
        if len(self._checkpoints) <= self._MAX_CHECKPOINTS:
            return
        terminal = {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED}
        candidates = [
            (k, v) for k, v in self._checkpoints.items()
            if v.status in terminal
        ]
        # Sort by created_at ascending (oldest first)
        candidates.sort(key=lambda kv: kv[1].created_at)
        to_remove = len(self._checkpoints) - self._MAX_CHECKPOINTS
        for k, _ in candidates[:to_remove]:
            del self._checkpoints[k]

    def get_checkpoint(self, checkpoint_id: str) -> CheckpointData | None:
        """Get a checkpoint by ID."""
        return self._checkpoints.get(checkpoint_id)

    def get_pipeline_checkpoints(self, pipeline_run_id: str) -> list[CheckpointData]:
        """Get all checkpoints for a pipeline run."""
        return [
            cp for cp in self._checkpoints.values()
            if cp.pipeline_run_id == pipeline_run_id
        ]

    def approve(
        self,
        checkpoint_id: str,
        organization_id: str,
        comments: str | None = None,
    ) -> CheckpointData:
        """Approve a checkpoint."""
        cp = self._checkpoints.get(checkpoint_id)
        if cp is None:
            raise KeyError(f"Checkpoint not found: {checkpoint_id}")

        # R37-FIX: Authorization — verify caller belongs to same org
        if cp.organization_id != organization_id:
            raise PermissionError(f"Not authorized to approve checkpoint {checkpoint_id}")

        if cp.status != ApprovalStatus.PENDING:
            raise ValueError(f"Checkpoint {checkpoint_id} is not pending (status: {cp.status})")

        # Check expiry
        if cp.expires_at and datetime.now(timezone.utc) > cp.expires_at:
            cp.status = ApprovalStatus.EXPIRED
            raise ValueError(f"Checkpoint {checkpoint_id} has expired")

        cp.status = ApprovalStatus.APPROVED
        cp.reviewer_comments = comments
        cp.approved_at = datetime.now(timezone.utc)

        logger.info("checkpoint_approved", checkpoint_id=checkpoint_id)
        return cp

    def reject(
        self,
        checkpoint_id: str,
        organization_id: str,
        comments: str | None = None,
    ) -> CheckpointData:
        """Reject a checkpoint."""
        cp = self._checkpoints.get(checkpoint_id)
        if cp is None:
            raise KeyError(f"Checkpoint not found: {checkpoint_id}")

        # R37-FIX: Authorization — verify caller belongs to same org
        if cp.organization_id != organization_id:
            raise PermissionError(f"Not authorized to reject checkpoint {checkpoint_id}")

        # R35-FIX: Add same status + expiry guards that approve() has.
        # Without this, an expired or already-approved checkpoint can be
        # rejected, reverting its status — potentially re-activating a
        # completed pipeline or overwriting an earlier decision.
        if cp.status != ApprovalStatus.PENDING:
            raise ValueError(f"Checkpoint {checkpoint_id} is not pending (status: {cp.status})")

        if cp.expires_at and datetime.now(timezone.utc) > cp.expires_at:
            cp.status = ApprovalStatus.EXPIRED
            raise ValueError(f"Checkpoint {checkpoint_id} has expired")

        cp.status = ApprovalStatus.REJECTED
        cp.reviewer_comments = comments

        logger.info("checkpoint_rejected", checkpoint_id=checkpoint_id)
        return cp

    def request_changes(
        self,
        checkpoint_id: str,
        organization_id: str,
        changes: list[str],
        comments: str | None = None,
    ) -> CheckpointData:
        """Request changes at a checkpoint."""
        cp = self._checkpoints.get(checkpoint_id)
        if cp is None:
            raise KeyError(f"Checkpoint not found: {checkpoint_id}")

        # R37-FIX: Authorization — verify caller belongs to same org
        if cp.organization_id != organization_id:
            raise PermissionError(f"Not authorized to request changes on checkpoint {checkpoint_id}")

        # R35-FIX: Same status + expiry guards as approve() and reject().
        if cp.status != ApprovalStatus.PENDING:
            raise ValueError(f"Checkpoint {checkpoint_id} is not pending (status: {cp.status})")

        if cp.expires_at and datetime.now(timezone.utc) > cp.expires_at:
            cp.status = ApprovalStatus.EXPIRED
            raise ValueError(f"Checkpoint {checkpoint_id} has expired")

        cp.status = ApprovalStatus.CHANGES_REQUESTED
        cp.change_requests = changes
        cp.reviewer_comments = comments

        logger.info(
            "checkpoint_changes_requested",
            checkpoint_id=checkpoint_id,
            change_count=len(changes),
        )
        return cp


# ── Singleton ───────────────────────────────────────────────────────

import threading as _threading
_checkpoint_lock = _threading.Lock()
_service: CheckpointService | None = None


def get_checkpoint_service() -> CheckpointService:
    """Get or create the checkpoint service singleton."""
    global _service
    if _service is not None:
        return _service
    with _checkpoint_lock:
        if _service is None:
            _service = CheckpointService()
        return _service
