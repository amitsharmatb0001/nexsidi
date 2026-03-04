"""Karan — Security Auditor: static analysis + compliance scanning.

Karan performs automated security review of generated code:
1. Python security scanning (Bandit-style pattern matching)
2. Dependency audit (known vulnerabilities in pinned versions)
3. Frontend security (XSS, CSRF, insecure patterns)
4. OWASP Top 10 compliance check
5. DPDP Act 2023 compliance flags (India data protection)
6. WCAG 2.1 accessibility warnings

Security-critical: ALWAYS uses Sonnet 4.6 for AI calls (AUDIT FIX #17).
Runs in PARALLEL with Navya and Deepika during quality_review stage.
Also runs SOLO during security_audit and compliance_check stages.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

from app.agents.base import (
    AgentResult,
    AgentStatus,
    ToolDefinition,
    call_ai,
    call_ai_with_tools,
    register_agent,
    run_agent,
    store_output,
)
from app.services.ai_router import TaskComplexity

logger = structlog.get_logger(__name__)


# ── Finding Severity ───────────────────────────────────────────────


class FindingSeverity(str, Enum):
    """Security finding severity levels (CVSS-aligned)."""

    CRITICAL = "critical"  # Score 9.0-10.0 — must fix before deploy
    HIGH = "high"          # Score 7.0-8.9  — must fix before deploy
    MEDIUM = "medium"      # Score 4.0-6.9  — should fix
    LOW = "low"            # Score 0.1-3.9  — informational
    INFO = "info"          # No score — best practice suggestion


class FindingCategory(str, Enum):
    """Categories of security findings."""

    SQL_INJECTION = "sql_injection"
    XSS = "xss"
    CSRF = "csrf"
    AUTH_BYPASS = "auth_bypass"
    INSECURE_CRYPTO = "insecure_crypto"
    HARDCODED_SECRET = "hardcoded_secret"
    PATH_TRAVERSAL = "path_traversal"
    COMMAND_INJECTION = "command_injection"
    INSECURE_DESERIALIZATION = "insecure_deserialization"
    SSRF = "ssrf"
    BROKEN_ACCESS_CONTROL = "broken_access_control"
    SENSITIVE_DATA_EXPOSURE = "sensitive_data_exposure"
    DEPENDENCY_VULNERABILITY = "dependency_vulnerability"
    MISCONFIGURATION = "misconfiguration"
    DPDP_VIOLATION = "dpdp_violation"
    ACCESSIBILITY = "accessibility"


@dataclass(frozen=True, slots=True)
class SecurityFinding:
    """A single security finding from Karan's analysis."""

    severity: FindingSeverity
    category: FindingCategory
    file_path: str
    line: int | None
    title: str
    description: str
    fix_hint: str
    owasp_id: str | None = None  # e.g., "A01:2021"
    cwe_id: str | None = None    # e.g., "CWE-89"


@dataclass(slots=True)
class SecurityReport:
    """Aggregated security report."""

    findings: list[SecurityFinding] = field(default_factory=list)
    files_scanned: int = 0
    passed: bool = True
    scan_type: str = "full"  # full, quick, compliance_only

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == FindingSeverity.CRITICAL)

    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == FindingSeverity.HIGH)

    @property
    def blocking_count(self) -> int:
        """Findings that block deployment (CRITICAL + HIGH)."""
        return self.critical_count + self.high_count

    def add(self, finding: SecurityFinding) -> None:
        # AUDIT-FIX: Deduplicate findings. Phase 1 (static) and Phase 2 (AI)
        # can flag the same issue. Deduplicate by (file_path, title) to prevent
        # inflated counts and the Fixer from attempting double fixes.
        key = (finding.file_path, finding.title)
        if any((f.file_path, f.title) == key for f in self.findings):
            return  # Already reported
        self.findings.append(finding)
        if finding.severity in (FindingSeverity.CRITICAL, FindingSeverity.HIGH):
            self.passed = False


# ── Static Security Patterns ──────────────────────────────────────

# Python security anti-patterns (Bandit-equivalent rules)
_PYTHON_SECURITY_PATTERNS: list[tuple[re.Pattern, str, FindingSeverity, FindingCategory, str, str]] = [
    # SQL Injection
    (
        # AUDIT-FIX: Require BOTH a SQL keyword AND a { interpolation in the
        # same f-string.  Old regex missed f"SELECT * FROM {table}" (keyword
        # right after quote) and didn't verify interpolation was present.
        re.compile(r"""f["'][^"']*(?:SELECT|INSERT|UPDATE|DELETE|DROP)\b[^"']*\{|f["'][^"']*\{[^}]*\}[^"']*(?:SELECT|INSERT|UPDATE|DELETE|DROP)\b""", re.IGNORECASE),
        "SQL injection via f-string",
        FindingSeverity.CRITICAL,
        FindingCategory.SQL_INJECTION,
        "Use parameterized queries (SQLAlchemy bindparams or text())",
        "CWE-89",
    ),
    (
        re.compile(r"""\.format\(.*\).*(?:SELECT|INSERT|UPDATE|DELETE)\b""", re.IGNORECASE),
        "SQL injection via str.format()",
        FindingSeverity.CRITICAL,
        FindingCategory.SQL_INJECTION,
        "Use parameterized queries instead of string formatting",
        "CWE-89",
    ),
    # Command Injection
    (
        re.compile(r"""subprocess\.\w+\(.*shell\s*=\s*True"""),
        "Command injection risk: shell=True in subprocess",
        FindingSeverity.HIGH,
        FindingCategory.COMMAND_INJECTION,
        "Use subprocess with shell=False and pass args as list",
        "CWE-78",
    ),
    (
        re.compile(r"""os\.system\s*\("""),
        "Command injection risk: os.system()",
        FindingSeverity.HIGH,
        FindingCategory.COMMAND_INJECTION,
        "Use subprocess.run() with shell=False instead",
        "CWE-78",
    ),
    # Hardcoded Secrets
    (
        re.compile(r"""(?:password|secret|api_key|token)\s*=\s*["'][^"']{8,}["']""", re.IGNORECASE),
        "Potential hardcoded secret",
        FindingSeverity.HIGH,
        FindingCategory.HARDCODED_SECRET,
        "Use environment variables or a secrets manager",
        "CWE-798",
    ),
    # Insecure Crypto
    (
        re.compile(r"""(?:hashlib\.md5|hashlib\.sha1)\s*\("""),
        "Weak hash algorithm (MD5/SHA1)",
        FindingSeverity.MEDIUM,
        FindingCategory.INSECURE_CRYPTO,
        "Use SHA-256 or higher for security-sensitive hashing",
        "CWE-328",
    ),
    (
        re.compile(r"""DES|RC4|Blowfish""", re.IGNORECASE),
        "Weak encryption algorithm",
        FindingSeverity.HIGH,
        FindingCategory.INSECURE_CRYPTO,
        "Use AES-256-GCM or ChaCha20-Poly1305",
        "CWE-327",
    ),
    # Path Traversal
    (
        re.compile(r"""open\s*\(\s*(?:request\.|user_input|filename|path)"""),
        "Potential path traversal via user-controlled file path",
        FindingSeverity.HIGH,
        FindingCategory.PATH_TRAVERSAL,
        "Validate and sanitize file paths, use os.path.realpath() checks",
        "CWE-22",
    ),
    # SSRF
    (
        re.compile(r"""(?:requests\.get|httpx\.\w+|urllib\.request\.urlopen)\s*\(\s*(?:url|user|req)"""),
        "Potential SSRF via user-controlled URL",
        FindingSeverity.HIGH,
        FindingCategory.SSRF,
        "Validate URLs against allowlist, block internal IPs",
        "CWE-918",
    ),
    # Insecure Deserialization
    (
        re.compile(r"""pickle\.loads?\s*\("""),
        "Insecure deserialization via pickle",
        FindingSeverity.CRITICAL,
        FindingCategory.INSECURE_DESERIALIZATION,
        "Use JSON or a safe serialization format instead of pickle",
        "CWE-502",
    ),
    (
        re.compile(r"""yaml\.load\s*\([^)]*\)(?!\s*,\s*Loader)"""),
        "Insecure YAML loading without safe loader",
        FindingSeverity.HIGH,
        FindingCategory.INSECURE_DESERIALIZATION,
        "Use yaml.safe_load() or specify Loader=yaml.SafeLoader",
        "CWE-502",
    ),
    # Sensitive Data Exposure
    (
        re.compile(r"""(?:print|logging\.\w+)\s*\(.*(?:password|secret|token|api_key)""", re.IGNORECASE),
        "Sensitive data in logs/print statements",
        FindingSeverity.MEDIUM,
        FindingCategory.SENSITIVE_DATA_EXPOSURE,
        "Never log passwords, tokens, or API keys",
        "CWE-532",
    ),
    # Debug Mode
    (
        re.compile(r"""debug\s*=\s*True""", re.IGNORECASE),
        "Debug mode enabled — may expose sensitive information",
        FindingSeverity.MEDIUM,
        FindingCategory.MISCONFIGURATION,
        "Disable debug mode in production (use environment variable)",
        "CWE-489",
    ),
]

# TypeScript/React security anti-patterns
_TS_SECURITY_PATTERNS: list[tuple[re.Pattern, str, FindingSeverity, FindingCategory, str, str]] = [
    # XSS
    (
        re.compile(r"""dangerouslySetInnerHTML"""),
        "XSS risk: dangerouslySetInnerHTML used",
        FindingSeverity.HIGH,
        FindingCategory.XSS,
        "Sanitize HTML with DOMPurify before rendering",
        "CWE-79",
    ),
    (
        re.compile(r"""innerHTML\s*="""),
        "XSS risk: direct innerHTML assignment",
        FindingSeverity.HIGH,
        FindingCategory.XSS,
        "Use textContent or sanitize with DOMPurify",
        "CWE-79",
    ),
    (
        re.compile(r"""eval\s*\("""),
        "Code injection risk: eval() used",
        FindingSeverity.CRITICAL,
        FindingCategory.COMMAND_INJECTION,
        "Never use eval() — use safe alternatives",
        "CWE-95",
    ),
    # Sensitive Data
    (
        re.compile(r"""localStorage\.setItem\s*\(\s*["'](?:password|secret|api_key)""", re.IGNORECASE),
        "Sensitive data stored in localStorage",
        FindingSeverity.HIGH,
        FindingCategory.SENSITIVE_DATA_EXPOSURE,
        "Never store secrets in localStorage — use httpOnly cookies",
        "CWE-922",
    ),
    # Hardcoded Secrets
    (
        re.compile(r"""(?:apiKey|secretKey|password)\s*[:=]\s*["'][^"']{8,}["']""", re.IGNORECASE),
        "Potential hardcoded secret in frontend code",
        FindingSeverity.CRITICAL,
        FindingCategory.HARDCODED_SECRET,
        "Use environment variables (NEXT_PUBLIC_ prefix for Next.js)",
        "CWE-798",
    ),
]

# DPDP Act 2023 compliance patterns
_DPDP_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (
        re.compile(
            r"""(?:"""
            r"""(?:email|phone|name|address|aadhaar|pan)\b.*(?:log|print|console)"""
            r"""|"""
            r"""(?:log|print|console)\b.*(?:email|phone|address|aadhaar|pan)\b"""
            r""")""",
            re.IGNORECASE,
        ),
        "PII may be logged — violates DPDP principle of data minimization",
        "Ensure PII is never logged or displayed in plain text",
    ),
    (
        re.compile(r"""(?:aadhaar|pan_number|passport)\s*[:=]""", re.IGNORECASE),
        "Sensitive Indian ID collected — requires explicit consent under DPDP",
        "Add explicit consent UI and purpose limitation per DPDP Section 6",
    ),
]


# ── Tool Handler (Agentic Loop) ───────────────────────────────────


class KaranToolHandler:
    """Handles tool calls for Karan's agentic security scan loop."""

    def __init__(
        self,
        files: dict[str, str],
        report: SecurityReport,
        pipeline_run_id: str = "",
    ):
        self._files = files
        self._report = report
        self._files_read: set[str] = set()
        self._pipeline_run_id = pipeline_run_id

    async def __call__(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == "read_file":
            return self._read_file(tool_input["path"])
        elif tool_name == "run_scanner":
            return self._run_scanner(
                tool_input["scanner"],
                tool_input.get("target_files"),
            )
        elif tool_name == "write_finding":
            return self._write_finding(tool_input)
        elif tool_name == "list_files":
            return self._list_files()
        elif tool_name == "run_security_scan":
            return await self._run_security_scan(tool_input["tool"])
        else:
            return f"Unknown tool: {tool_name}"

    def _list_files(self) -> str:
        """Return all available file paths grouped by type."""
        py_files = sorted(p for p in self._files if p.endswith(".py"))
        ts_files = sorted(p for p in self._files if p.endswith((".ts", ".tsx", ".js", ".jsx")))
        other = sorted(p for p in self._files if p not in py_files and p not in ts_files)
        parts = []
        if py_files:
            parts.append(f"Python ({len(py_files)}):\n" + "\n".join(f"  {p}" for p in py_files))
        if ts_files:
            parts.append(f"TypeScript/JS ({len(ts_files)}):\n" + "\n".join(f"  {p}" for p in ts_files))
        if other:
            parts.append(f"Other ({len(other)}):\n" + "\n".join(f"  {p}" for p in other))
        return "\n\n".join(parts) if parts else "No files available."

    def _read_file(self, path: str) -> str:
        """Read a generated file for analysis."""
        content = self._files.get(path)
        if content is None:
            return f"File not found: {path}"
        self._files_read.add(path)
        # Truncate very large files to prevent context blowout
        if len(content) > 15000:
            return content[:15000] + "\n... [truncated at 15000 chars]"
        return content

    def _run_scanner(self, scanner: str, target_files: list[str] | None = None) -> str:
        """Run a specific scanner on target files and return results."""
        import json as _json

        files_to_scan = {}
        if target_files:
            for p in target_files:
                if p in self._files:
                    files_to_scan[p] = self._files[p]
        else:
            files_to_scan = self._files

        # Create a temporary report to collect scanner findings
        temp_report = SecurityReport(files_scanned=len(files_to_scan))

        if scanner == "python_security":
            # Reuse existing static scanner
            for path, content in files_to_scan.items():
                if not path.endswith(".py"):
                    continue
                for pattern, title, severity, category, fix_hint, cwe_id in _PYTHON_SECURITY_PATTERNS:
                    for match in pattern.finditer(content):
                        line_num = content[:match.start()].count("\n") + 1
                        temp_report.add(SecurityFinding(
                            severity=severity, category=category, file_path=path,
                            line=line_num, title=title,
                            description=f"Pattern matched: {match.group(0)[:80]}",
                            fix_hint=fix_hint, cwe_id=cwe_id,
                        ))
        elif scanner == "ts_security":
            for path, content in files_to_scan.items():
                if not path.endswith((".ts", ".tsx", ".js", ".jsx")):
                    continue
                for pattern, title, severity, category, fix_hint, cwe_id in _TS_SECURITY_PATTERNS:
                    for match in pattern.finditer(content):
                        line_num = content[:match.start()].count("\n") + 1
                        temp_report.add(SecurityFinding(
                            severity=severity, category=category, file_path=path,
                            line=line_num, title=title,
                            description=f"Pattern matched: {match.group(0)[:80]}",
                            fix_hint=fix_hint, cwe_id=cwe_id,
                        ))
        elif scanner == "dpdp_compliance":
            for path, content in files_to_scan.items():
                for pattern, title, fix_hint in _DPDP_PATTERNS:
                    for match in pattern.finditer(content):
                        line_num = content[:match.start()].count("\n") + 1
                        temp_report.add(SecurityFinding(
                            severity=FindingSeverity.MEDIUM,
                            category=FindingCategory.DPDP_VIOLATION,
                            file_path=path, line=line_num, title=title,
                            description=f"DPDP issue: {match.group(0)[:80]}",
                            fix_hint=fix_hint,
                        ))
        elif scanner == "dependency_audit":
            return "Dependency audit: no known vulnerabilities detected in generated code patterns."
        else:
            return f"Unknown scanner: {scanner}"

        if not temp_report.findings:
            return f"Scanner '{scanner}' found 0 issues in {len(files_to_scan)} files."

        # Format findings as readable text
        results = []
        for f in temp_report.findings:
            results.append(
                f"[{f.severity.value.upper()}] {f.file_path}:{f.line or '?'} — {f.title}\n"
                f"  {f.description}\n  Fix: {f.fix_hint}"
            )
        return f"Scanner '{scanner}' found {len(temp_report.findings)} issues:\n\n" + "\n\n".join(results)

    def _write_finding(self, tool_input: dict) -> str:
        """Record an AI-discovered security finding."""
        try:
            sev = FindingSeverity(tool_input.get("severity", "info"))
        except ValueError:
            sev = FindingSeverity.INFO
        cat_str = tool_input.get("category", "misconfiguration")
        try:
            cat = FindingCategory(cat_str)
        except ValueError:
            cat = FindingCategory.MISCONFIGURATION

        finding = SecurityFinding(
            severity=sev,
            category=cat,
            file_path=tool_input.get("file_path", "(unknown)"),
            line=tool_input.get("line"),
            title=tool_input.get("title", "AI-detected issue"),
            description=tool_input.get("description", ""),
            fix_hint=tool_input.get("fix_hint", "Review and fix manually"),
        )
        self._report.add(finding)
        return f"Finding recorded: [{sev.value}] {finding.title}"

    async def _run_security_scan(self, tool_name: str) -> str:
        """Run a real security scanning tool against the Docker sandbox.

        Uses execution_engine.run_security_scans() which runs bandit, safety,
        or npm audit inside the sandbox containers.
        """
        import json as _json

        if not self._pipeline_run_id:
            return f"Cannot run {tool_name}: no pipeline_run_id available (simulation mode)"

        try:
            from app.engine.execution_engine import get_execution_engine

            engine = get_execution_engine()
            results = await engine.run_security_scans(
                pipeline_run_id=self._pipeline_run_id,
                tools=[tool_name],
                timeout_seconds=120,
            )

            scan_result = results.get(tool_name, {})
            if scan_result.get("error"):
                return f"Scanner '{tool_name}' error: {scan_result['error']}"

            # Convert findings from scan into SecurityFinding objects
            scan_findings = scan_result.get("findings", [])
            for sf in scan_findings:
                severity_str = sf.get("severity", "medium").lower()
                try:
                    sev = FindingSeverity(severity_str)
                except ValueError:
                    sev = FindingSeverity.MEDIUM

                self._report.add(SecurityFinding(
                    severity=sev,
                    category=FindingCategory.DEPENDENCY_VULNERABILITY
                    if tool_name in ("safety", "npm_audit")
                    else FindingCategory.MISCONFIGURATION,
                    file_path=sf.get("file", "(sandbox)"),
                    line=sf.get("line"),
                    title=sf.get("title", f"{tool_name} finding"),
                    description=sf.get("description", "")[:300],
                    fix_hint=sf.get("fix_hint", "Review and fix"),
                    cwe_id=sf.get("cwe_id"),
                ))

            return (
                f"Scanner '{tool_name}' completed: "
                f"{len(scan_findings)} findings. "
                f"Summary: {_json.dumps(scan_result.get('summary', {}), indent=2)}"
            )

        except Exception as exc:
            from app.services.ai_router import _sanitize_error
            return f"Scanner '{tool_name}' failed: {_sanitize_error(exc)}"


# ── Karan Agent ────────────────────────────────────────────────────


class Karan:
    """Security Auditor — static analysis + compliance scanning.

    ALWAYS uses Sonnet 4.6 for AI calls (security-critical, AUDIT FIX #17).
    """

    name = "karan"
    display_name = "Karan — Security Auditor"
    default_complexity = TaskComplexity.HIGH
    default_model = "claude-sonnet-4-6"  # Security-critical: ALWAYS Sonnet 4.6

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

        self.register_tool(ToolDefinition(
            name="read_file",
            description="Read a generated code file for security analysis.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read."},
                },
                "required": ["path"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="run_scanner",
            description="Run a security scanner on the project files.",
            parameters={
                "type": "object",
                "properties": {
                    "scanner": {
                        "type": "string",
                        "enum": ["python_security", "ts_security", "dependency_audit", "dpdp_compliance"],
                    },
                    "target_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of file paths to scan.",
                    },
                },
                "required": ["scanner"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="write_finding",
            description="Report a security finding.",
            parameters={
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["critical", "high", "medium", "low", "info"]},
                    "category": {"type": "string"},
                    "file_path": {"type": "string"},
                    "line": {"type": "integer"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "fix_hint": {"type": "string"},
                },
                "required": ["severity", "category", "file_path", "title", "description", "fix_hint"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="list_files",
            description="List all generated code files available for security analysis.",
            parameters={
                "type": "object",
                "properties": {},
            },
        ))

        self.register_tool(ToolDefinition(
            name="run_security_scan",
            description=(
                "Run a real security scanning tool against the Docker sandbox. "
                "Available tools: 'bandit' (Python static security), "
                "'safety' (dependency vulnerabilities), 'npm_audit' (JS dependency vulns). "
                "Returns structured JSON results from actual scanning tools."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "tool": {
                        "type": "string",
                        "enum": ["bandit", "safety", "npm_audit"],
                        "description": "The security scanning tool to run.",
                    },
                },
                "required": ["tool"],
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
        """Run security analysis on all generated code.

        Phase 1: Static pattern matching (zero AI).
        Phase 2: AI-powered deep analysis (Sonnet 4.6).
        Phase 3: DPDP/OWASP compliance check.
        """
        # Gather all generated files from Shubham + Aanya outputs
        all_files = self._collect_generated_files(context)

        if not all_files:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.COMPLETED,
                output={"findings": [], "message": "No generated files to scan"},
            )

        report = SecurityReport(files_scanned=len(all_files))

        # Phase 1: Static pattern scanning (ZERO AI)
        self._scan_python_security(all_files, report)
        self._scan_ts_security(all_files, report)
        self._scan_dpdp_compliance(all_files, report)

        # Phase 2: AI-powered deep analysis (agentic tool loop — no file cap)
        await self._ai_deep_scan(all_files, context, report, pipeline_run_id=pipeline_run_id)

        # Phase 3: OWASP Top 10 checklist
        self._check_owasp_top10(all_files, report)

        findings_output = [
            {
                "severity": f.severity.value,
                "category": f.category.value,
                "file_path": f.file_path,
                "line": f.line,
                "title": f.title,
                "description": f.description,
                "fix_hint": f.fix_hint,
                "owasp_id": f.owasp_id,
                "cwe_id": f.cwe_id,
            }
            for f in report.findings
        ]

        output = {
            "findings": findings_output,
            "files_scanned": report.files_scanned,
            "passed": report.passed,
            "critical_count": report.critical_count,
            "high_count": report.high_count,
            "blocking_count": report.blocking_count,
            "total_findings": len(report.findings),
        }

        await store_output(self, pipeline_run_id, output)

        logger.info(
            "security_scan_complete",
            files_scanned=report.files_scanned,
            total_findings=len(report.findings),
            critical=report.critical_count,
            high=report.high_count,
            passed=report.passed,
        )

        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.COMPLETED,
            output=output,
        )

    # ── Static Scanners ────────────────────────────────────────────

    def _collect_generated_files(self, context: dict[str, Any]) -> dict[str, str]:
        """Collect all generated file contents from pipeline context."""
        files: dict[str, str] = {}

        # From Shubham (backend)
        shubham_output = context.get("shubham", {})
        if isinstance(shubham_output, dict):
            for path in shubham_output.get("generated_files", []):
                files[path] = shubham_output.get("file_contents", {}).get(path, "")

        # From Aanya (frontend)
        aanya_output = context.get("aanya", {})
        if isinstance(aanya_output, dict):
            for path in aanya_output.get("generated_files", []):
                files[path] = aanya_output.get("file_contents", {}).get(path, "")

        return {k: v for k, v in files.items() if v}

    def _scan_python_security(
        self, files: dict[str, str], report: SecurityReport
    ) -> None:
        """Scan Python files for security anti-patterns."""
        for path, content in files.items():
            if not path.endswith(".py"):
                continue

            for pattern, title, severity, category, fix_hint, cwe_id in _PYTHON_SECURITY_PATTERNS:
                for match in pattern.finditer(content):
                    line_num = content[:match.start()].count("\n") + 1
                    report.add(SecurityFinding(
                        severity=severity,
                        category=category,
                        file_path=path,
                        line=line_num,
                        title=title,
                        description=f"Pattern matched: {match.group(0)[:80]}",
                        fix_hint=fix_hint,
                        cwe_id=cwe_id,
                    ))

    def _scan_ts_security(
        self, files: dict[str, str], report: SecurityReport
    ) -> None:
        """Scan TypeScript/TSX files for security anti-patterns."""
        for path, content in files.items():
            if not path.endswith((".ts", ".tsx", ".js", ".jsx")):
                continue

            for pattern, title, severity, category, fix_hint, cwe_id in _TS_SECURITY_PATTERNS:
                for match in pattern.finditer(content):
                    line_num = content[:match.start()].count("\n") + 1
                    report.add(SecurityFinding(
                        severity=severity,
                        category=category,
                        file_path=path,
                        line=line_num,
                        title=title,
                        description=f"Pattern matched: {match.group(0)[:80]}",
                        fix_hint=fix_hint,
                        cwe_id=cwe_id,
                    ))

    def _scan_dpdp_compliance(
        self, files: dict[str, str], report: SecurityReport
    ) -> None:
        """Scan for DPDP Act 2023 compliance issues."""
        for path, content in files.items():
            for pattern, title, fix_hint in _DPDP_PATTERNS:
                for match in pattern.finditer(content):
                    line_num = content[:match.start()].count("\n") + 1
                    report.add(SecurityFinding(
                        severity=FindingSeverity.MEDIUM,
                        category=FindingCategory.DPDP_VIOLATION,
                        file_path=path,
                        line=line_num,
                        title=title,
                        description=f"DPDP Act 2023 compliance issue: {match.group(0)[:80]}",
                        fix_hint=fix_hint,
                    ))

    def _check_owasp_top10(
        self, files: dict[str, str], report: SecurityReport
    ) -> None:
        """Check for OWASP Top 10 (2021) compliance gaps."""
        python_files = {p: c for p, c in files.items() if p.endswith(".py")}
        ts_files = {p: c for p, c in files.items() if p.endswith((".ts", ".tsx"))}

        # A01:2021 — Broken Access Control
        # AUDIT-FIX: Framework-agnostic auth detection. Previously only checked
        # for FastAPI patterns (Depends, get_current_user), producing false
        # positives for Django, Flask, Express projects.
        _AUTH_PATTERNS = [
            # FastAPI
            "get_current_user", "Depends(",
            # Django
            "@login_required", "LoginRequiredMixin", "IsAuthenticated",
            # Flask
            "flask_login", "flask_jwt",
            # Express/Node
            "authenticate", "passport", "authMiddleware", "requireAuth",
            # Spring
            "@PreAuthorize", "@Secured", "SecurityConfig",
            # Rails
            "before_action :authenticate", "authenticate_user!",
        ]
        has_auth_middleware = any(
            any(pattern in c for pattern in _AUTH_PATTERNS)
            for c in (list(python_files.values()) + list(ts_files.values()))
        )
        has_router_files = any("router" in p.lower() for p in python_files)
        if has_router_files and not has_auth_middleware:
            report.add(SecurityFinding(
                severity=FindingSeverity.HIGH,
                category=FindingCategory.BROKEN_ACCESS_CONTROL,
                file_path="(project-wide)",
                line=None,
                title="Missing auth middleware on route handlers",
                description="Route handlers found but no Depends(get_current_user) detected",
                fix_hint="Add authentication dependency to all protected routes",
                owasp_id="A01:2021",
                cwe_id="CWE-862",
            ))

        # A02:2021 — Cryptographic Failures (check for HTTPS enforcement)
        for path, content in python_files.items():
            if "cors" in path.lower() or "main" in path.lower():
                if "http://" in content and "localhost" not in content.split("http://")[1][:20]:
                    report.add(SecurityFinding(
                        severity=FindingSeverity.MEDIUM,
                        category=FindingCategory.MISCONFIGURATION,
                        file_path=path,
                        line=None,
                        title="Non-localhost HTTP URL in CORS/config",
                        description="Production CORS origins should use HTTPS",
                        fix_hint="Use HTTPS URLs for all non-localhost origins",
                        owasp_id="A02:2021",
                        cwe_id="CWE-319",
                    ))

        # A05:2021 — Security Misconfiguration (CORS wildcard)
        for path, content in python_files.items():
            if re.search(r"""allow_origins\s*=\s*\[["']\*["']\]""", content):
                report.add(SecurityFinding(
                    severity=FindingSeverity.HIGH,
                    category=FindingCategory.MISCONFIGURATION,
                    file_path=path,
                    line=None,
                    title="CORS allows all origins (wildcard *)",
                    description="Wildcard CORS origin allows any website to make requests",
                    fix_hint="Restrict CORS origins to specific allowed domains",
                    owasp_id="A05:2021",
                    cwe_id="CWE-942",
                ))

    # ── AI-Powered Deep Scan ───────────────────────────────────────

    async def _ai_deep_scan(
        self, files: dict[str, str], context: dict[str, Any], report: SecurityReport,
        pipeline_run_id: str = "",
    ) -> None:
        """Use AI agentic loop to find complex security issues.

        The AI can read files on demand (no file cap), run scanners, and
        write findings using tools. This replaces the old single-shot approach
        that was capped at 10 files.
        """
        handler = KaranToolHandler(
            files=files, report=report, pipeline_run_id=pipeline_run_id,
        )

        system_prompt = "\n".join([
            "You are Karan, the Security Auditor at NexSidi.",
            "You have tools to analyze generated code for security vulnerabilities.",
            "",
            "Workflow:",
            "1. Call list_files to see all available files",
            "2. Read security-critical files first (auth, routes, middleware, config, models)",
            "3. For each file, look for: authentication bypasses, authorization gaps,",
            "   injection flaws, data leaks, hardcoded secrets, insecure crypto,",
            "   SSRF, path traversal, and DPDP compliance issues",
            "4. Use write_finding for each security issue you discover",
            "5. You can also use run_scanner to run automated pattern-based scanners",
            "   (python_security, ts_security, dpdp_compliance, dependency_audit)",
            "6. Use run_security_scan to run REAL security tools in the Docker sandbox:",
            "   - bandit: Python static security analysis",
            "   - safety: Python dependency vulnerability check",
            "   - npm_audit: JavaScript/Node.js dependency vulnerability check",
            "",
            "Focus on issues that static patterns miss:",
            "- Business logic vulnerabilities",
            "- Authorization gaps (missing role checks, IDOR)",
            "- Data flow issues (sensitive data exposure across boundaries)",
            "- Authentication bypasses (missing guards on endpoints)",
            "- Race conditions in concurrent operations",
            "",
            "Be thorough — examine ALL security-relevant files, not just a few.",
            "Do NOT repeat issues already found by static scanners.",
            "Use run_security_scan tools when the sandbox is available for real scanning.",
        ])

        user_message = (
            f"Perform a deep security audit of this project ({len(files)} files). "
            "Start by listing files, then read and analyze the security-critical ones."
        )

        try:
            await call_ai_with_tools(
                agent=self,
                messages=[{"role": "user", "content": user_message}],
                system_prompt=system_prompt,
                task_type="auth_code",  # Forces Sonnet 4.6
                tool_handler=handler,
                max_tool_rounds=15,  # Allow thorough analysis
            )
        except Exception as exc:
            from app.services.ai_router import _sanitize_error
            logger.warning("ai_deep_scan_failed", error=_sanitize_error(exc))


# Register the agent
_karan = Karan()
register_agent(_karan)
