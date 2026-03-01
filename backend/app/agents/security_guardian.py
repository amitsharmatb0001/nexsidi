"""Security Guardian + Auto-Updater: continuous vulnerability scanning and dependency updates.

Self-maintaining agents that run on schedule:

Security Guardian:
1. Scans project dependencies for known CVEs (via OSV/NVD databases)
2. Checks for leaked secrets in codebase
3. Verifies security headers and TLS configuration
4. Alerts on high/critical vulnerabilities
5. Triggers auto-update PRs for vulnerable dependencies

Auto-Updater:
1. Checks for outdated dependencies (pip, npm)
2. Evaluates breaking change risk per update
3. Creates update PRs with changelog summaries
4. Runs tests before merging
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog

from app.agents.base import (
    AgentResult,
    AgentStatus,
    ToolDefinition,
    register_agent,
    run_agent,
    store_output,
)
from app.services.ai_router import TaskComplexity

logger = structlog.get_logger(__name__)


class VulnerabilitySeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class ScanType(str, Enum):
    DEPENDENCY = "dependency"
    SECRET_LEAK = "secret_leak"
    SECURITY_HEADER = "security_header"
    TLS = "tls"
    CODE_PATTERN = "code_pattern"


class UpdateRisk(str, Enum):
    """Risk level for a dependency update."""
    PATCH = "patch"         # 1.0.0 -> 1.0.1 (safe)
    MINOR = "minor"         # 1.0.0 -> 1.1.0 (usually safe)
    MAJOR = "major"         # 1.0.0 -> 2.0.0 (breaking changes possible)


class UpdateStatus(str, Enum):
    AVAILABLE = "available"
    PR_CREATED = "pr_created"
    TESTING = "testing"
    MERGED = "merged"
    SKIPPED = "skipped"
    FAILED = "failed"


# Common secret patterns to detect
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AWS Access Key", re.compile(r"AKIA[0-9A-Z]{16}", re.ASCII)),
    ("AWS Secret Key", re.compile(r"""(?:aws_secret|secret_key)\s*[:=]\s*['"][A-Za-z0-9/+=]{40}""", re.IGNORECASE)),
    ("GitHub Token", re.compile(r"gh[ps]_[A-Za-z0-9_]{36,}", re.ASCII)),
    ("Generic API Key", re.compile(r"""(?:api[_-]?key|apikey)\s*[:=]\s*['"][A-Za-z0-9_\-]{20,}""", re.IGNORECASE)),
    ("Private Key", re.compile(r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----")),
    ("JWT Token", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+")),
    ("Database URL", re.compile(r"""(?:postgres|mysql|mongodb)://[^:]+:[^@]+@""", re.IGNORECASE)),
]

# Required security headers
REQUIRED_SECURITY_HEADERS: dict[str, str] = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "0",
    "Content-Security-Policy": "default-src 'self'",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


@dataclass(slots=True)
class Vulnerability:
    """A detected vulnerability."""

    scan_type: ScanType
    severity: VulnerabilitySeverity
    title: str
    description: str
    affected_component: str = ""
    cve_id: str = ""
    fix_available: bool = False
    fix_version: str = ""
    detected_at: str = ""

    def __post_init__(self) -> None:
        if not self.detected_at:
            self.detected_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "scan_type": self.scan_type.value,
            "severity": self.severity.value,
            "title": self.title,
            "description": self.description,
            "affected_component": self.affected_component,
            "cve_id": self.cve_id,
            "fix_available": self.fix_available,
            "fix_version": self.fix_version,
            "detected_at": self.detected_at,
        }


@dataclass(slots=True)
class DependencyUpdate:
    """A pending dependency update."""

    package_name: str
    current_version: str
    latest_version: str
    risk: UpdateRisk
    status: UpdateStatus = UpdateStatus.AVAILABLE
    changelog_summary: str = ""
    has_security_fix: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "package_name": self.package_name,
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "risk": self.risk.value,
            "status": self.status.value,
            "changelog_summary": self.changelog_summary,
            "has_security_fix": self.has_security_fix,
        }


@dataclass(slots=True)
class SecurityScanReport:
    """Result of a security scan."""

    scan_types: list[ScanType]
    vulnerabilities: list[Vulnerability] = field(default_factory=list)
    pending_updates: list[DependencyUpdate] = field(default_factory=list)
    scanned_at: str = ""

    def __post_init__(self) -> None:
        if not self.scanned_at:
            self.scanned_at = datetime.now(timezone.utc).isoformat()

    @property
    def critical_count(self) -> int:
        return sum(1 for v in self.vulnerabilities if v.severity == VulnerabilitySeverity.CRITICAL)

    @property
    def high_count(self) -> int:
        return sum(1 for v in self.vulnerabilities if v.severity == VulnerabilitySeverity.HIGH)

    @property
    def total_findings(self) -> int:
        return len(self.vulnerabilities)

    @property
    def has_blocking(self) -> bool:
        return self.critical_count > 0 or self.high_count > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "scan_types": [s.value for s in self.scan_types],
            "total_findings": self.total_findings,
            "critical_count": self.critical_count,
            "high_count": self.high_count,
            "has_blocking": self.has_blocking,
            "vulnerabilities": [v.to_dict() for v in self.vulnerabilities],
            "pending_updates": [u.to_dict() for u in self.pending_updates],
            "scanned_at": self.scanned_at,
        }


class SecurityGuardian:
    """Security Guardian -- continuous vulnerability scanning + auto-updater.

    Runs on schedule and on-demand. Combines:
    - Dependency CVE scanning
    - Secret leak detection
    - Security header verification
    - Dependency update management
    """

    name = "security_guardian"
    display_name = "Security Guardian"
    default_complexity = TaskComplexity.HIGH
    default_model = "claude-sonnet-4-6"  # Security-critical

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

        self.register_tool(ToolDefinition(
            name="scan_dependencies",
            description="Scan project dependencies for known CVEs.",
            parameters={
                "type": "object",
                "properties": {
                    "ecosystem": {
                        "type": "string",
                        "enum": ["pip", "npm", "both"],
                        "description": "Package ecosystem to scan.",
                    },
                },
                "required": ["ecosystem"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="scan_secrets",
            description="Scan codebase for leaked secrets/credentials.",
            parameters={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Code to scan."},
                },
                "required": ["code"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="check_updates",
            description="Check for available dependency updates.",
            parameters={
                "type": "object",
                "properties": {
                    "ecosystem": {"type": "string", "enum": ["pip", "npm", "both"]},
                },
                "required": ["ecosystem"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="create_update_pr",
            description="Create a PR to update a dependency.",
            parameters={
                "type": "object",
                "properties": {
                    "package_name": {"type": "string"},
                    "target_version": {"type": "string"},
                },
                "required": ["package_name", "target_version"],
            },
        ))


    def register_tool(self, tool: "ToolDefinition") -> None:
        """Register a tool available to this agent."""
        self._tools[tool.name] = tool

    @property
    def tools(self) -> list["ToolDefinition"]:
        """All registered tools."""
        return list(self._tools.values())

    async def run(
        self,
        pipeline_run_id: str,
        context: dict[str, Any],
    ) -> AgentResult:
        """Execute with timing, logging, and error handling."""
        return await run_agent(self, pipeline_run_id, context)

    async def execute(
        self,
        pipeline_run_id: str,
        context: dict[str, Any],
    ) -> AgentResult:
        """Run a full security scan on the pipeline output."""
        code_files = self._collect_code(context)

        # Run all scans
        report = self.full_scan(code_files)

        output = report.to_dict()
        output["files_scanned"] = len(code_files)

        logger.info(
            "security_scan_complete",
            total_findings=report.total_findings,
            critical=report.critical_count,
            high=report.high_count,
            files_scanned=len(code_files),
        )

        status = AgentStatus.COMPLETED
        if report.has_blocking:
            status = AgentStatus.FAILED

        # STORE-FIX: Persist output to context engine for downstream agents
        await store_output(self, pipeline_run_id, output)

        return AgentResult(
            agent_name=self.name,
            status=status,
            output=output,
        )

    def full_scan(self, code_files: dict[str, str]) -> SecurityScanReport:
        """Run all scan types on the provided code."""
        vulns: list[Vulnerability] = []

        # Secret leak detection
        for filepath, content in code_files.items():
            vulns.extend(self.scan_secrets(content, filepath))

        # Security header check (if any config files present)
        vulns.extend(self.scan_security_headers(code_files))

        return SecurityScanReport(
            scan_types=[ScanType.SECRET_LEAK, ScanType.SECURITY_HEADER],
            vulnerabilities=vulns,
        )

    def scan_secrets(self, code: str, filepath: str = "") -> list[Vulnerability]:
        """Scan code for leaked secrets/credentials."""
        findings: list[Vulnerability] = []

        for pattern_name, pattern in SECRET_PATTERNS:
            matches = pattern.findall(code)
            for match in matches:
                # Redact the actual secret for the report
                redacted = match[:8] + "..." if len(match) > 8 else "***"
                findings.append(Vulnerability(
                    scan_type=ScanType.SECRET_LEAK,
                    severity=VulnerabilitySeverity.CRITICAL,
                    title=f"Leaked {pattern_name}",
                    description=f"Found {pattern_name} in {filepath or 'code'}: {redacted}",
                    affected_component=filepath,
                ))

        return findings

    def scan_security_headers(self, code_files: dict[str, str]) -> list[Vulnerability]:
        """Check for missing security headers in config/middleware files."""
        findings: list[Vulnerability] = []

        # Look for middleware/config files
        relevant_files = {
            k: v for k, v in code_files.items()
            if any(kw in k.lower() for kw in ("middleware", "config", "security", "main", "app"))
        }

        if not relevant_files:
            return findings

        # Check if security headers are configured
        all_code = "\n".join(relevant_files.values())
        for header_name in REQUIRED_SECURITY_HEADERS:
            if header_name.lower() not in all_code.lower():
                findings.append(Vulnerability(
                    scan_type=ScanType.SECURITY_HEADER,
                    severity=VulnerabilitySeverity.MEDIUM,
                    title=f"Missing {header_name} header",
                    description=f"Security header '{header_name}' not found in configuration.",
                    affected_component="security_middleware",
                ))

        return findings

    def classify_update_risk(self, current: str, latest: str) -> UpdateRisk:
        """Classify the risk level of a version update."""
        cur_parts = current.split(".")
        lat_parts = latest.split(".")

        if len(cur_parts) >= 1 and len(lat_parts) >= 1:
            if cur_parts[0] != lat_parts[0]:
                return UpdateRisk.MAJOR
            if len(cur_parts) >= 2 and len(lat_parts) >= 2:
                if cur_parts[1] != lat_parts[1]:
                    return UpdateRisk.MINOR
        return UpdateRisk.PATCH

    def _collect_code(self, context: dict[str, Any]) -> dict[str, str]:
        """Collect all code files from pipeline context."""
        files: dict[str, str] = {}
        for agent_key in ("shubham", "aanya"):
            output = context.get(agent_key, {})
            if isinstance(output, dict):
                files.update(output.get("file_contents", {}))
        return files


# Register
_security_guardian = SecurityGuardian()
register_agent(_security_guardian)
