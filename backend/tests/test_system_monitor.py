"""Tests for System Monitor: metrics, health status, bug tracking."""

from __future__ import annotations

import pytest

from app.agents.system_monitor import (
    SystemMonitor,
    MetricType,
    HealthStatus,
    MetricSnapshot,
    SystemHealthReport,
    BugSeverity,
    BugStatus,
    BugReport,
    METRIC_THRESHOLDS,
    classify_bug_severity,
    _error_fingerprint,
)


class TestMetricSnapshots:
    """Test metric recording and health classification."""

    def test_healthy_response_time(self):
        m = MetricSnapshot(metric_type=MetricType.RESPONSE_TIME, value=100, unit="ms")
        assert m.health == HealthStatus.HEALTHY

    def test_degraded_response_time(self):
        m = MetricSnapshot(metric_type=MetricType.RESPONSE_TIME, value=400, unit="ms")
        assert m.health == HealthStatus.DEGRADED

    def test_critical_response_time(self):
        m = MetricSnapshot(metric_type=MetricType.RESPONSE_TIME, value=3000, unit="ms")
        assert m.health == HealthStatus.CRITICAL

    def test_healthy_cpu(self):
        m = MetricSnapshot(metric_type=MetricType.CPU_USAGE, value=30, unit="%")
        assert m.health == HealthStatus.HEALTHY

    def test_critical_cpu(self):
        m = MetricSnapshot(metric_type=MetricType.CPU_USAGE, value=96, unit="%")
        assert m.health == HealthStatus.CRITICAL

    def test_healthy_memory(self):
        m = MetricSnapshot(metric_type=MetricType.MEMORY_USAGE, value=50, unit="%")
        assert m.health == HealthStatus.HEALTHY

    def test_critical_memory(self):
        m = MetricSnapshot(metric_type=MetricType.MEMORY_USAGE, value=97, unit="%")
        assert m.health == HealthStatus.CRITICAL

    def test_healthy_error_rate(self):
        m = MetricSnapshot(metric_type=MetricType.ERROR_RATE, value=0.5, unit="%")
        assert m.health == HealthStatus.HEALTHY

    def test_unknown_metric_type(self):
        m = MetricSnapshot(metric_type=MetricType.THROUGHPUT, value=1000, unit="rps")
        assert m.health == HealthStatus.UNKNOWN

    def test_to_dict(self):
        m = MetricSnapshot(metric_type=MetricType.CPU_USAGE, value=45.678, unit="%")
        d = m.to_dict()
        assert d["metric_type"] == "cpu_usage"
        assert d["value"] == 45.68  # Rounded
        assert d["health"] == "healthy"


class TestSystemHealthReport:
    """Test overall system health computation."""

    def test_critical_override(self):
        metrics = [
            MetricSnapshot(metric_type=MetricType.RESPONSE_TIME, value=50, unit="ms"),
            MetricSnapshot(metric_type=MetricType.CPU_USAGE, value=99, unit="%"),
        ]
        report = SystemHealthReport(metrics=metrics)
        report.compute_overall_status()
        assert report.overall_status == HealthStatus.CRITICAL

    def test_all_healthy(self):
        metrics = [
            MetricSnapshot(metric_type=MetricType.RESPONSE_TIME, value=50, unit="ms"),
            MetricSnapshot(metric_type=MetricType.CPU_USAGE, value=30, unit="%"),
        ]
        report = SystemHealthReport(metrics=metrics)
        report.compute_overall_status()
        assert report.overall_status == HealthStatus.HEALTHY

    def test_degraded_status(self):
        metrics = [
            MetricSnapshot(metric_type=MetricType.RESPONSE_TIME, value=400, unit="ms"),
        ]
        report = SystemHealthReport(metrics=metrics)
        report.compute_overall_status()
        assert report.overall_status == HealthStatus.DEGRADED

    def test_empty_is_unknown(self):
        report = SystemHealthReport()
        report.compute_overall_status()
        assert report.overall_status == HealthStatus.UNKNOWN

    def test_to_dict(self):
        report = SystemHealthReport(
            metrics=[MetricSnapshot(metric_type=MetricType.CPU_USAGE, value=50, unit="%")],
            recommendations=["Scale up workers"],
        )
        report.compute_overall_status()
        d = report.to_dict()
        assert d["overall_status"] == "healthy"
        assert len(d["metrics"]) == 1
        assert "Scale up workers" in d["recommendations"]


class TestBugTracker:
    """Test bug capture, deduplication, and lifecycle."""

    def setup_method(self):
        self.sm = SystemMonitor()

    def test_capture_new_bug(self):
        bug = self.sm.capture_error("TypeError", "Expected str got int")
        assert isinstance(bug, BugReport)
        assert bug.error_type == "TypeError"
        assert bug.status == BugStatus.OPEN
        assert bug.occurrence_count == 1

    def test_deduplication(self):
        self.sm.capture_error("ValueError", "Invalid input")
        bug2 = self.sm.capture_error("ValueError", "Invalid input")
        assert bug2.occurrence_count == 2
        assert self.sm.bug_count == 1

    def test_different_errors_are_separate(self):
        self.sm.capture_error("TypeError", "Bad type")
        self.sm.capture_error("ValueError", "Bad value")
        assert self.sm.bug_count == 2

    def test_severity_classification(self):
        assert classify_bug_severity("MemoryError", 1) == BugSeverity.CRITICAL
        assert classify_bug_severity("DatabaseError", 1) == BugSeverity.CRITICAL
        assert classify_bug_severity("ConnectionError", 1) == BugSeverity.HIGH
        assert classify_bug_severity("TimeoutError", 1) == BugSeverity.HIGH
        assert classify_bug_severity("ValueError", 100) == BugSeverity.HIGH
        assert classify_bug_severity("ValueError", 10) == BugSeverity.MEDIUM
        assert classify_bug_severity("ValueError", 1) == BugSeverity.LOW

    def test_resolve_bug(self):
        bug = self.sm.capture_error("TestError", "test")
        assert self.sm.resolve_bug(bug.id) is True
        assert self.sm.open_bug_count == 0

    def test_resolve_nonexistent(self):
        assert self.sm.resolve_bug("BUG-9999") is False

    def test_get_bugs_by_severity(self):
        self.sm.capture_error("MemoryError", "OOM")  # CRITICAL
        self.sm.capture_error("ValueError", "bad")   # LOW
        critical = self.sm.get_bugs(severity=BugSeverity.CRITICAL)
        assert len(critical) == 1
        assert critical[0].error_type == "MemoryError"

    def test_bug_report_to_dict(self):
        bug = self.sm.capture_error("TestError", "X" * 1000)
        d = bug.to_dict()
        assert "error_type" in d
        assert "severity" in d
        assert len(d["message"]) <= 500  # Truncated

    def test_error_fingerprint_deterministic(self):
        fp1 = _error_fingerprint("TypeError", "test")
        fp2 = _error_fingerprint("TypeError", "test")
        assert fp1 == fp2

    def test_error_fingerprint_unique(self):
        fp1 = _error_fingerprint("TypeError", "test")
        fp2 = _error_fingerprint("TypeError", "different")
        assert fp1 != fp2
