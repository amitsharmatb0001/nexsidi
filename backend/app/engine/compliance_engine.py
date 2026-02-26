"""Compliance Engine: DPDP Act 2023, OWASP Top 10 (2021), WCAG 2.2 checks.

Runs as a standalone engine called by Karan during the COMPLIANCE_CHECK stage.
Each compliance framework has its own check suite producing structured findings.

Design:
- Zero-AI path: regex + AST pattern matching (fast, deterministic)
- AI-augmented path: deep analysis for ambiguous cases (optional)
- Karan calls this engine; Karan owns the SecurityReport
- Results feed into CHECKPOINT 2 pre-deploy review
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ── Compliance Framework ──────────────────────────────────────────


class ComplianceFramework(str, Enum):
    """Supported compliance frameworks."""

    DPDP_2023 = "dpdp_2023"            # India Digital Personal Data Protection Act
    OWASP_TOP10_2021 = "owasp_top10"   # OWASP Top 10 (2021 edition)
    WCAG_22 = "wcag_2.2"               # Web Content Accessibility Guidelines 2.2


class ComplianceSeverity(str, Enum):
    CRITICAL = "critical"    # Must fix before deploy
    HIGH = "high"            # Should fix before deploy
    MEDIUM = "medium"        # Recommended fix
    LOW = "low"              # Advisory
    INFO = "info"            # Informational


@dataclass(frozen=True, slots=True)
class ComplianceFinding:
    """A single compliance check finding."""

    framework: ComplianceFramework
    rule_id: str             # e.g., "DPDP-01", "A01:2021", "WCAG-1.1.1"
    severity: ComplianceSeverity
    title: str
    description: str
    file_path: str
    line: int | None = None
    fix_hint: str = ""
    code_snippet: str = ""


@dataclass(slots=True)
class ComplianceReport:
    """Aggregated results from all compliance checks."""

    findings: list[ComplianceFinding] = field(default_factory=list)
    frameworks_checked: list[ComplianceFramework] = field(default_factory=list)
    files_scanned: int = 0
    passed: bool = True

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == ComplianceSeverity.CRITICAL)

    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == ComplianceSeverity.HIGH)

    @property
    def blocking_count(self) -> int:
        """Findings that must be fixed before deployment."""
        return self.critical_count + self.high_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "files_scanned": self.files_scanned,
            "frameworks_checked": [f.value for f in self.frameworks_checked],
            "total_findings": len(self.findings),
            "critical": self.critical_count,
            "high": self.high_count,
            "blocking": self.blocking_count,
            "findings": [
                {
                    "framework": f.framework.value,
                    "rule_id": f.rule_id,
                    "severity": f.severity.value,
                    "title": f.title,
                    "file_path": f.file_path,
                    "line": f.line,
                    "description": f.description,
                    "fix_hint": f.fix_hint,
                }
                for f in self.findings
            ],
        }


# ── DPDP Act 2023 Patterns ───────────────────────────────────────

# Section 4: Consent + lawful purpose
# Section 6: Data minimization — collect only what's needed
# Section 8: Right to erasure
# Section 9: Data breach notification within 72 hours

_DPDP_RULES: list[tuple[str, re.Pattern, ComplianceSeverity, str, str]] = [
    (
        "DPDP-01",
        re.compile(
            r"""(?:"""
            r"""(?:email|phone|name|address|aadhaar|pan)\b.*(?:log|print|console)"""
            r"""|"""
            r"""(?:log|print|console)\b.*(?:email|phone|address|aadhaar|pan)\b"""
            r""")""",
            re.IGNORECASE,
        ),
        ComplianceSeverity.HIGH,
        "PII logged in plain text — violates DPDP Section 6 (data minimization)",
        "Never log PII. Use structured logging with PII fields redacted.",
    ),
    (
        "DPDP-02",
        re.compile(
            r"""(?:aadhaar|pan_number|passport|voter_id|driving_license)\s*[:=]""",
            re.IGNORECASE,
        ),
        ComplianceSeverity.CRITICAL,
        "Indian government ID collected — requires explicit consent under DPDP Section 4",
        "Ensure explicit user consent is collected and stored before processing government IDs.",
    ),
    (
        "DPDP-03",
        re.compile(
            r"""(?:biometric|fingerprint|retina|face_id|voice_print)""",
            re.IGNORECASE,
        ),
        ComplianceSeverity.CRITICAL,
        "Biometric data processing — DPDP Section 4 requires explicit purpose + consent",
        "Document the purpose of biometric collection and obtain verifiable consent.",
    ),
    (
        "DPDP-04",
        re.compile(
            r"""(?:child|minor|age)\s*(?:<|<=|lt)\s*(?:18|13)""",
            re.IGNORECASE,
        ),
        ComplianceSeverity.HIGH,
        "Processing minor's data — DPDP Section 9 requires guardian consent",
        "Implement verifiable parental/guardian consent before processing minor's data.",
    ),
    (
        "DPDP-05",
        re.compile(
            r"""(?:data_retention|retain|store).*(?:indefinite|forever|no_expiry)""",
            re.IGNORECASE,
        ),
        ComplianceSeverity.MEDIUM,
        "Indefinite data retention — DPDP Section 8 requires deletion after purpose fulfilled",
        "Set a retention period and implement automatic data deletion.",
    ),
]

# ── OWASP Top 10 (2021) Patterns ─────────────────────────────────

_OWASP_RULES: list[tuple[str, re.Pattern, ComplianceSeverity, str, str, list[str]]] = [
    (
        "A01:2021",  # Broken Access Control
        re.compile(r"""(?:@app\.(?:get|post|put|delete)\b(?:(?!(?:Depends|require_|get_current))[\s\S]){0,200}(?:def\s+\w+))"""),
        ComplianceSeverity.HIGH,
        "Endpoint may lack access control — OWASP A01:2021",
        "Add authentication/authorization dependency (Depends(get_current_user)) to every endpoint.",
        [".py"],
    ),
    (
        "A02:2021",  # Cryptographic Failures
        re.compile(r"""(?:md5|sha1|DES|RC4|ECB)\b""", re.IGNORECASE),
        ComplianceSeverity.CRITICAL,
        "Weak cryptographic algorithm — OWASP A02:2021",
        "Use SHA-256+ for hashing, AES-256-GCM for encryption. Never use MD5/SHA1/DES.",
        [".py", ".ts", ".tsx"],
    ),
    (
        "A03:2021",  # Injection
        re.compile(r"""(?:f["\'].*(?:SELECT|INSERT|UPDATE|DELETE|DROP)\b.*\{)""", re.IGNORECASE),
        ComplianceSeverity.CRITICAL,
        "SQL injection via f-string — OWASP A03:2021",
        "Use parameterized queries or ORM. Never interpolate user input into SQL.",
        [".py"],
    ),
    (
        "A04:2021",  # Insecure Design
        re.compile(r"""(?:rate_limit|throttle|brute_force)""", re.IGNORECASE),
        ComplianceSeverity.INFO,
        "Rate limiting reference found — verify implementation for OWASP A04:2021",
        "Ensure rate limiting is properly configured on authentication and sensitive endpoints.",
        [".py"],
    ),
    (
        "A05:2021",  # Security Misconfiguration
        re.compile(r"""(?:DEBUG\s*=\s*True|CORS.*\*|allow_origins.*\[.*\*.*\])"""),
        ComplianceSeverity.HIGH,
        "Security misconfiguration — OWASP A05:2021",
        "Disable DEBUG in production. Restrict CORS origins to specific domains.",
        [".py"],
    ),
    (
        "A06:2021",  # Vulnerable Components
        re.compile(r"""(?:import\s+pickle|yaml\.load\s*\((?!.*Loader))"""),
        ComplianceSeverity.HIGH,
        "Vulnerable component usage — OWASP A06:2021 (pickle/unsafe yaml)",
        "Use json instead of pickle. Use yaml.safe_load() instead of yaml.load().",
        [".py"],
    ),
    (
        "A07:2021",  # Auth Failures
        re.compile(r"""(?:password|secret|token)\s*=\s*["'][^"']{1,30}["']""", re.IGNORECASE),
        ComplianceSeverity.CRITICAL,
        "Hardcoded credential — OWASP A07:2021",
        "Move secrets to environment variables or a secret manager.",
        [".py", ".ts", ".tsx", ".env"],
    ),
    (
        "A08:2021",  # Software Integrity
        re.compile(r"""(?:eval\s*\(|exec\s*\(|subprocess\.call\s*\(.*shell\s*=\s*True)"""),
        ComplianceSeverity.CRITICAL,
        "Code execution risk — OWASP A08:2021",
        "Avoid eval/exec. Use subprocess with shell=False and explicit args list.",
        [".py"],
    ),
    (
        "A09:2021",  # Logging Failures
        re.compile(
            r"""(?:"""
            r"""(?:password|secret|token|key)\b.*(?:log|print|console)"""
            r"""|"""
            r"""(?:log|print|console)\b.*(?:password|secret|token|key)\b"""
            r""")""",
            re.IGNORECASE,
        ),
        ComplianceSeverity.HIGH,
        "Sensitive data in logs — OWASP A09:2021",
        "Never log passwords, secrets, tokens, or API keys.",
        [".py", ".ts", ".tsx"],
    ),
    (
        "A10:2021",  # SSRF
        re.compile(r"""(?:requests\.(?:get|post|put|delete)\s*\(\s*(?:f["\']|.*\+|.*format))"""),
        ComplianceSeverity.HIGH,
        "Potential SSRF — OWASP A10:2021",
        "Validate and whitelist URLs before making server-side requests.",
        [".py"],
    ),
]

# ── WCAG 2.2 Patterns (HTML/JSX/TSX) ─────────────────────────────

_WCAG_RULES: list[tuple[str, re.Pattern, ComplianceSeverity, str, str]] = [
    (
        "WCAG-1.1.1",  # Non-text Content
        re.compile(r"""<img\b(?:(?!alt\s*=).)*?>"""),
        ComplianceSeverity.HIGH,
        "Image missing alt attribute — WCAG 1.1.1 (Non-text Content)",
        "Add descriptive alt text to all <img> elements. Use alt='' for decorative images.",
    ),
    (
        "WCAG-1.3.1",  # Info and Relationships
        re.compile(r"""<(?:div|span)\b[^>]*(?:onClick|onKeyDown)[^>]*>"""),
        ComplianceSeverity.MEDIUM,
        "Interactive element without semantic role — WCAG 1.3.1",
        "Use <button> or <a> instead of <div>/<span> with click handlers. Add role + tabIndex if needed.",
    ),
    (
        "WCAG-1.4.3",  # Contrast (Minimum)
        re.compile(r"""color:\s*(?:#(?:ccc|ddd|eee|fff|CCC|DDD|EEE)|lightgr[ae]y|silver)"""),
        ComplianceSeverity.MEDIUM,
        "Potential low-contrast text — WCAG 1.4.3 (Contrast Minimum 4.5:1)",
        "Ensure text has at least 4.5:1 contrast ratio against its background.",
    ),
    (
        "WCAG-2.1.1",  # Keyboard
        re.compile(r"""(?:onMouse(?:Down|Up|Over|Enter)\b(?:(?!onKey).)*?)"""),
        ComplianceSeverity.MEDIUM,
        "Mouse-only handler without keyboard equivalent — WCAG 2.1.1 (Keyboard)",
        "Add onKeyDown/onKeyUp handlers alongside mouse handlers for keyboard accessibility.",
    ),
    (
        "WCAG-2.4.2",  # Page Titled
        re.compile(r"""(?:<(?:html|head)\b(?:(?!<title\b).)*?</head>)""", re.DOTALL),
        ComplianceSeverity.HIGH,
        "Page missing <title> — WCAG 2.4.2 (Page Titled)",
        "Add a descriptive <title> element in the <head> of every page.",
    ),
    (
        "WCAG-3.1.1",  # Language of Page
        re.compile(r"""<html\b(?:(?!lang\s*=).)*?>"""),
        ComplianceSeverity.MEDIUM,
        "HTML element missing lang attribute — WCAG 3.1.1 (Language of Page)",
        "Add lang='en' (or appropriate language) to the <html> element.",
    ),
    (
        "WCAG-4.1.1",  # Parsing
        re.compile(r"""(?:id\s*=\s*["'](\w+)["'].*id\s*=\s*["']\1["'])""", re.DOTALL),
        ComplianceSeverity.MEDIUM,
        "Duplicate ID attribute — WCAG 4.1.1 (Parsing)",
        "Ensure all id attributes are unique within the page.",
    ),
    (
        "WCAG-4.1.2",  # Name, Role, Value
        re.compile(r"""<input\b(?:(?!(?:aria-label|id|name)\s*=).)*?>"""),
        ComplianceSeverity.HIGH,
        "Form input missing label/name — WCAG 4.1.2 (Name, Role, Value)",
        "Add aria-label, name, or associate a <label> with every form input.",
    ),
]


# ── Compliance Engine ─────────────────────────────────────────────


class ComplianceEngine:
    """Runs compliance checks across all supported frameworks.

    Called by Karan during COMPLIANCE_CHECK pipeline stage.
    Produces a ComplianceReport for CHECKPOINT 2 review.
    """

    def check_all(
        self,
        files: dict[str, str],
        frameworks: list[ComplianceFramework] | None = None,
    ) -> ComplianceReport:
        """Run all compliance checks on generated code.

        Args:
            files: dict of {file_path: file_content} for all generated files.
            frameworks: Which frameworks to check (default: all).

        Returns:
            ComplianceReport with all findings.
        """
        if frameworks is None:
            frameworks = list(ComplianceFramework)

        report = ComplianceReport(
            files_scanned=len(files),
            frameworks_checked=list(frameworks),
        )

        for fw in frameworks:
            if fw == ComplianceFramework.DPDP_2023:
                self._check_dpdp(files, report)
            elif fw == ComplianceFramework.OWASP_TOP10_2021:
                self._check_owasp(files, report)
            elif fw == ComplianceFramework.WCAG_22:
                self._check_wcag(files, report)

        report.passed = report.blocking_count == 0

        logger.info(
            "compliance_check_complete",
            frameworks=[f.value for f in frameworks],
            files_scanned=report.files_scanned,
            total_findings=len(report.findings),
            blocking=report.blocking_count,
            passed=report.passed,
        )

        return report

    def check_dpdp(self, files: dict[str, str]) -> ComplianceReport:
        """Run only DPDP Act 2023 compliance checks."""
        return self.check_all(files, frameworks=[ComplianceFramework.DPDP_2023])

    def check_owasp(self, files: dict[str, str]) -> ComplianceReport:
        """Run only OWASP Top 10 compliance checks."""
        return self.check_all(files, frameworks=[ComplianceFramework.OWASP_TOP10_2021])

    def check_wcag(self, files: dict[str, str]) -> ComplianceReport:
        """Run only WCAG 2.2 compliance checks."""
        return self.check_all(files, frameworks=[ComplianceFramework.WCAG_22])

    # ── Internal Check Methods ────────────────────────────────────

    def _check_dpdp(self, files: dict[str, str], report: ComplianceReport) -> None:
        """DPDP Act 2023 compliance scan."""
        for path, content in files.items():
            if not path.endswith((".py", ".ts", ".tsx", ".js", ".jsx")):
                continue
            for rule_id, pattern, severity, title, fix_hint in _DPDP_RULES:
                for match in pattern.finditer(content):
                    line_num = content[:match.start()].count("\n") + 1
                    report.findings.append(ComplianceFinding(
                        framework=ComplianceFramework.DPDP_2023,
                        rule_id=rule_id,
                        severity=severity,
                        title=title,
                        description=f"{match.group(0)[:80]}",
                        file_path=path,
                        line=line_num,
                        fix_hint=fix_hint,
                    ))

    def _check_owasp(self, files: dict[str, str], report: ComplianceReport) -> None:
        """OWASP Top 10 (2021) compliance scan."""
        for path, content in files.items():
            for rule_id, pattern, severity, title, fix_hint, extensions in _OWASP_RULES:
                if not any(path.endswith(ext) for ext in extensions):
                    continue
                for match in pattern.finditer(content):
                    line_num = content[:match.start()].count("\n") + 1
                    report.findings.append(ComplianceFinding(
                        framework=ComplianceFramework.OWASP_TOP10_2021,
                        rule_id=rule_id,
                        severity=severity,
                        title=title,
                        description=f"{match.group(0)[:80]}",
                        file_path=path,
                        line=line_num,
                        fix_hint=fix_hint,
                    ))

    def _check_wcag(self, files: dict[str, str], report: ComplianceReport) -> None:
        """WCAG 2.2 accessibility scan (HTML/JSX/TSX files)."""
        for path, content in files.items():
            if not path.endswith((".html", ".htm", ".tsx", ".jsx")):
                continue
            for rule_id, pattern, severity, title, fix_hint in _WCAG_RULES:
                for match in pattern.finditer(content):
                    line_num = content[:match.start()].count("\n") + 1
                    report.findings.append(ComplianceFinding(
                        framework=ComplianceFramework.WCAG_22,
                        rule_id=rule_id,
                        severity=severity,
                        title=title,
                        description=f"{match.group(0)[:80]}",
                        file_path=path,
                        line=line_num,
                        fix_hint=fix_hint,
                    ))


# ── Singleton ───────────────────────────────────────────────────

_engine: ComplianceEngine | None = None


def get_compliance_engine() -> ComplianceEngine:
    """Get or create the compliance engine singleton."""
    global _engine
    if _engine is None:
        _engine = ComplianceEngine()
    return _engine
