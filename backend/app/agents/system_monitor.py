"""System Monitor: System Optimizer + Bug Tracker for self-maintaining platform.

System Optimizer:
1. Monitors API response times and error rates
2. Tracks database query performance (slow queries)
3. Monitors memory and CPU usage patterns
4. Detects performance regressions
5. Suggests optimization actions

Bug Tracker:
1. Captures unhandled exceptions automatically
2. Groups similar errors (deduplication)
3. Tracks error frequency and impact
4. Assigns severity based on error type and frequency
5. Creates internal issue tickets for resolution
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog

from app.agents.base import (
    AgentResult,
    AgentStatus,
    BaseAgent,
    ToolDefinition,
    register_agent,
)
from app.services.ai_router import TaskComplexity

logger = structlog.get_logger(__name__)


# ── System Optimizer ────────────────────────────────────────────

class MetricType(str, Enum):
    RESPONSE_TIME = "response_time"
    ERROR_RATE = "error_rate"
    QUERY_TIME = "query_time"
    MEMORY_USAGE = "memory_usage"
    CPU_USAGE = "cpu_usage"
    THROUGHPUT = "throughput"


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


# Thresholds for health classification
METRIC_THRESHOLDS: dict[MetricType, dict[str, float]] = {
    MetricType.RESPONSE_TIME: {"healthy": 200, "degraded": 500, "critical": 2000},  # ms
    MetricType.ERROR_RATE: {"healthy": 1.0, "degraded": 5.0, "critical": 10.0},     # %
    MetricType.QUERY_TIME: {"healthy": 100, "degraded": 500, "critical": 2000},      # ms
    MetricType.MEMORY_USAGE: {"healthy": 70, "degraded": 85, "critical": 95},        # %
    MetricType.CPU_USAGE: {"healthy": 60, "degraded": 80, "critical": 95},           # %
}


@dataclass(slots=True)
class MetricSnapshot:
    """A single metric measurement."""

    metric_type: MetricType
    value: float
    unit: str
    component: str = ""
    recorded_at: str = ""

    def __post_init__(self) -> None:
        if not self.recorded_at:
            self.recorded_at = datetime.now(timezone.utc).isoformat()

    @property
    def health(self) -> HealthStatus:
        thresholds = METRIC_THRESHOLDS.get(self.metric_type)
        if thresholds is None:
            return HealthStatus.UNKNOWN
        if self.value <= thresholds["healthy"]:
            return HealthStatus.HEALTHY
        if self.value <= thresholds["degraded"]:
            return HealthStatus.DEGRADED
        return HealthStatus.CRITICAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_type": self.metric_type.value,
            "value": round(self.value, 2),
            "unit": self.unit,
            "component": self.component,
            "health": self.health.value,
            "recorded_at": self.recorded_at,
        }


@dataclass(slots=True)
class SystemHealthReport:
    """Overall system health report."""

    metrics: list[MetricSnapshot] = field(default_factory=list)
    overall_status: HealthStatus = HealthStatus.UNKNOWN
    recommendations: list[str] = field(default_factory=list)
    generated_at: str = ""

    def __post_init__(self) -> None:
        if not self.generated_at:
            self.generated_at = datetime.now(timezone.utc).isoformat()

    def compute_overall_status(self) -> None:
        """Compute overall status from individual metrics."""
        if not self.metrics:
            self.overall_status = HealthStatus.UNKNOWN
            return
        statuses = [m.health for m in self.metrics]
        if HealthStatus.CRITICAL in statuses:
            self.overall_status = HealthStatus.CRITICAL
        elif HealthStatus.DEGRADED in statuses:
            self.overall_status = HealthStatus.DEGRADED
        else:
            self.overall_status = HealthStatus.HEALTHY

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_status": self.overall_status.value,
            "metrics": [m.to_dict() for m in self.metrics],
            "recommendations": self.recommendations,
            "generated_at": self.generated_at,
        }


# ── Bug Tracker ─────────────────────────────────────────────────

class BugSeverity(str, Enum):
    CRITICAL = "critical"   # System crash, data loss
    HIGH = "high"           # Major feature broken
    MEDIUM = "medium"       # Minor issue, workaround exists
    LOW = "low"             # Cosmetic, enhancement


class BugStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    WONT_FIX = "wont_fix"


@dataclass(slots=True)
class BugReport:
    """An automatically captured bug/error."""

    id: str
    error_type: str
    message: str
    stacktrace: str = ""
    component: str = ""
    severity: BugSeverity = BugSeverity.MEDIUM
    status: BugStatus = BugStatus.OPEN
    occurrence_count: int = 1
    first_seen: str = ""
    last_seen: str = ""
    fingerprint: str = ""     # Hash for deduplication

    def __post_init__(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if not self.first_seen:
            self.first_seen = now
        if not self.last_seen:
            self.last_seen = now
        if not self.fingerprint:
            self.fingerprint = _error_fingerprint(self.error_type, self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "error_type": self.error_type,
            "message": self.message[:500],
            "component": self.component,
            "severity": self.severity.value,
            "status": self.status.value,
            "occurrence_count": self.occurrence_count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


def _error_fingerprint(error_type: str, message: str) -> str:
    """Generate a deduplication fingerprint for an error."""
    key = f"{error_type}:{message[:100]}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def classify_bug_severity(error_type: str, occurrence_count: int) -> BugSeverity:
    """Classify bug severity based on error type and frequency."""
    critical_types = {"SystemExit", "MemoryError", "DatabaseError", "SecurityError"}
    high_types = {"ConnectionError", "TimeoutError", "PermissionError", "AuthenticationError"}

    if error_type in critical_types:
        return BugSeverity.CRITICAL
    if error_type in high_types:
        return BugSeverity.HIGH
    if occurrence_count >= 100:
        return BugSeverity.HIGH
    if occurrence_count >= 10:
        return BugSeverity.MEDIUM
    return BugSeverity.LOW


# ── System Monitor Agent ────────────────────────────────────────

class SystemMonitor(BaseAgent):
    """System Monitor -- optimizer + bug tracker.

    Combines system performance monitoring with automatic
    bug capture and tracking.
    """

    name = "system_monitor"
    display_name = "System Monitor"
    default_complexity = TaskComplexity.MEDIUM

    def __init__(self) -> None:
        super().__init__()
        self._bugs: dict[str, BugReport] = {}  # fingerprint -> report
        self._metrics_history: list[MetricSnapshot] = []

        self.register_tool(ToolDefinition(
            name="check_health",
            description="Check overall system health metrics.",
            parameters={
                "type": "object",
                "properties": {},
            },
        ))

        self.register_tool(ToolDefinition(
            name="report_bug",
            description="Report or update a bug from an exception.",
            parameters={
                "type": "object",
                "properties": {
                    "error_type": {"type": "string"},
                    "message": {"type": "string"},
                    "component": {"type": "string"},
                },
                "required": ["error_type", "message"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="list_bugs",
            description="List tracked bugs by severity.",
            parameters={
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                },
            },
        ))

    async def execute(
        self,
        pipeline_run_id: str,
        context: dict[str, Any],
    ) -> AgentResult:
        """Generate a system health report."""
        metrics = context.get("system_metrics", [])
        errors = context.get("captured_errors", [])

        # Process metrics
        snapshots: list[MetricSnapshot] = []
        for m in metrics:
            if isinstance(m, dict):
                try:
                    snap = MetricSnapshot(
                        metric_type=MetricType(m["type"]),
                        value=float(m["value"]),
                        unit=m.get("unit", ""),
                        component=m.get("component", ""),
                    )
                    snapshots.append(snap)
                except (KeyError, ValueError):
                    pass

        # Process errors
        for err in errors:
            if isinstance(err, dict):
                self.capture_error(
                    error_type=err.get("type", "UnknownError"),
                    message=err.get("message", ""),
                    stacktrace=err.get("stacktrace", ""),
                    component=err.get("component", ""),
                )

        report = SystemHealthReport(metrics=snapshots)
        report.compute_overall_status()

        # Add recommendations
        for snap in snapshots:
            if snap.health == HealthStatus.CRITICAL:
                report.recommendations.append(
                    f"CRITICAL: {snap.metric_type.value} at {snap.value}{snap.unit} "
                    f"in {snap.component or 'system'} -- immediate action needed"
                )
            elif snap.health == HealthStatus.DEGRADED:
                report.recommendations.append(
                    f"WARNING: {snap.metric_type.value} at {snap.value}{snap.unit} "
                    f"in {snap.component or 'system'} -- monitor closely"
                )

        output = report.to_dict()
        output["tracked_bugs"] = len(self._bugs)
        output["open_bugs"] = sum(1 for b in self._bugs.values() if b.status == BugStatus.OPEN)

        logger.info(
            "system_health_report",
            overall=report.overall_status.value,
            metrics=len(snapshots),
            bugs=len(self._bugs),
        )

        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.COMPLETED,
            output=output,
        )

    def capture_error(
        self,
        error_type: str,
        message: str,
        stacktrace: str = "",
        component: str = "",
    ) -> BugReport:
        """Capture an error, deduplicating by fingerprint."""
        fp = _error_fingerprint(error_type, message)
        now = datetime.now(timezone.utc).isoformat()

        if fp in self._bugs:
            bug = self._bugs[fp]
            bug.occurrence_count += 1
            bug.last_seen = now
            bug.severity = classify_bug_severity(error_type, bug.occurrence_count)
            return bug

        bug_id = f"BUG-{len(self._bugs) + 1:04d}"
        severity = classify_bug_severity(error_type, 1)

        bug = BugReport(
            id=bug_id,
            error_type=error_type,
            message=message,
            stacktrace=stacktrace,
            component=component,
            severity=severity,
            fingerprint=fp,
        )
        self._bugs[fp] = bug

        logger.info(
            "bug_captured",
            bug_id=bug_id,
            error_type=error_type,
            severity=severity.value,
        )

        return bug

    def get_bugs(
        self,
        severity: BugSeverity | None = None,
        status: BugStatus | None = None,
    ) -> list[BugReport]:
        """Get tracked bugs, optionally filtered."""
        bugs = list(self._bugs.values())
        if severity is not None:
            bugs = [b for b in bugs if b.severity == severity]
        if status is not None:
            bugs = [b for b in bugs if b.status == status]
        return sorted(bugs, key=lambda b: b.occurrence_count, reverse=True)

    def resolve_bug(self, bug_id: str) -> bool:
        """Mark a bug as resolved."""
        for bug in self._bugs.values():
            if bug.id == bug_id:
                bug.status = BugStatus.RESOLVED
                return True
        return False

    def record_metric(self, metric: MetricSnapshot) -> None:
        """Record a metric snapshot."""
        self._metrics_history.append(metric)

    @property
    def bug_count(self) -> int:
        return len(self._bugs)

    @property
    def open_bug_count(self) -> int:
        return sum(1 for b in self._bugs.values() if b.status == BugStatus.OPEN)


# Register
_system_monitor = SystemMonitor()
register_agent(_system_monitor)
