"""Attack Tester — Security attack payload generation and resistance testing.

Generates comprehensive attack payloads to test that generated applications
properly resist common security attacks:

1. SQL Injection (classic, UNION, blind, time-based)
2. Cross-Site Scripting (reflected, stored, DOM-based)
3. CSRF token bypass attempts
4. Path Traversal (encoded, double-encoded, OS-specific)
5. Authentication Bypass (JWT manipulation, header removal)
6. Rate Limit Bypass attempts
7. File Upload Bypass (MIME spoofing, double extensions)
8. Brute Force detection testing
9. IDOR (Insecure Direct Object Reference)
10. Header Injection (CRLF, Host header)
11. SSRF (Server-Side Request Forgery)
12. Command Injection (OS command chaining)

The agent produces an AttackReport with block_rate and pass/fail status.
A generated app MUST block >= 95% of attack payloads to pass.
"""

from __future__ import annotations

import os  # ATTACK-FIX
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx  # ATTACK-FIX
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


# -- Attack Types --------------------------------------------------------


class AttackType(str, Enum):
    """Categories of security attacks to test against."""

    SQL_INJECTION = "sql_injection"
    XSS = "xss"
    CSRF = "csrf"
    PATH_TRAVERSAL = "path_traversal"
    AUTH_BYPASS = "auth_bypass"
    RATE_LIMIT_BYPASS = "rate_limit_bypass"
    FILE_UPLOAD_BYPASS = "file_upload_bypass"
    BRUTE_FORCE = "brute_force"
    IDOR = "idor"
    HEADER_INJECTION = "header_injection"
    SSRF = "ssrf"
    COMMAND_INJECTION = "command_injection"


# -- Data Classes --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AttackPayload:
    """A single attack payload to test against a generated application."""

    attack_type: AttackType
    payload: str
    description: str
    expected_blocked: bool = True
    category: str = ""


@dataclass(frozen=True, slots=True)
class AttackResult:
    """Result of executing a single attack payload."""

    attack_type: AttackType
    payload: str
    blocked: bool
    response_code: int = 0
    details: str = ""


@dataclass(slots=True)
class AttackReport:
    """Aggregated report of all attack test results."""

    results: list[AttackResult] = field(default_factory=list)
    total_tests: int = 0
    blocked_count: int = 0
    bypassed_count: int = 0

    @property
    def block_rate(self) -> float:
        """Percentage of attacks that were blocked (0.0 - 100.0)."""
        if self.total_tests == 0:
            return 0.0
        return (self.blocked_count / self.total_tests) * 100.0

    @property
    def passed(self) -> bool:
        """True if block_rate >= 95.0%."""
        return self.block_rate >= 95.0


# -- Attack Payload Generator -------------------------------------------


class AttackPayloadGenerator:
    """Static methods to generate attack payloads by category."""

    @staticmethod
    def generate_sql_injection() -> list[AttackPayload]:
        """Generate SQL injection attack payloads."""
        return [
            AttackPayload(
                attack_type=AttackType.SQL_INJECTION,
                payload="' OR 1=1--",
                description="Classic SQL injection: always-true OR condition",
                category="classic",
            ),
            AttackPayload(
                attack_type=AttackType.SQL_INJECTION,
                payload="' UNION SELECT null,username,password FROM users--",
                description="UNION-based injection to extract credentials",
                category="union",
            ),
            AttackPayload(
                attack_type=AttackType.SQL_INJECTION,
                payload="'; DROP TABLE users;--",
                description="Destructive injection: drop users table",
                category="destructive",
            ),
            AttackPayload(
                attack_type=AttackType.SQL_INJECTION,
                payload="1; WAITFOR DELAY '0:0:10'--",
                description="Time-based blind SQL injection",
                category="blind",
            ),
            AttackPayload(
                attack_type=AttackType.SQL_INJECTION,
                payload='" OR ""="',
                description="Double-quote SQL injection bypass",
                category="classic",
            ),
            AttackPayload(
                attack_type=AttackType.SQL_INJECTION,
                payload="admin'--",
                description="Comment-out password check for admin login",
                category="auth_bypass",
            ),
            AttackPayload(
                attack_type=AttackType.SQL_INJECTION,
                payload="1' AND 1=CONVERT(int, @@version)--",
                description="Error-based injection to extract DB version",
                category="error_based",
            ),
            AttackPayload(
                attack_type=AttackType.SQL_INJECTION,
                payload="1' OR '1'='1' /*",
                description="Block comment injection to bypass filters",
                category="classic",
            ),
        ]

    @staticmethod
    def generate_xss() -> list[AttackPayload]:
        """Generate cross-site scripting (XSS) attack payloads."""
        return [
            AttackPayload(
                attack_type=AttackType.XSS,
                payload="<script>alert(1)</script>",
                description="Basic reflected XSS via script tag",
                category="reflected",
            ),
            AttackPayload(
                attack_type=AttackType.XSS,
                payload='<img onerror=alert(1) src=x>',
                description="XSS via img tag error handler",
                category="event_handler",
            ),
            AttackPayload(
                attack_type=AttackType.XSS,
                payload="<svg/onload=alert(1)>",
                description="XSS via SVG onload event",
                category="event_handler",
            ),
            AttackPayload(
                attack_type=AttackType.XSS,
                payload="javascript:alert(1)",
                description="XSS via javascript: protocol in URLs",
                category="protocol",
            ),
            AttackPayload(
                attack_type=AttackType.XSS,
                payload="<body onload=alert(1)>",
                description="XSS via body onload event",
                category="event_handler",
            ),
            AttackPayload(
                attack_type=AttackType.XSS,
                payload='"><script>alert(1)</script>',
                description="XSS breaking out of attribute context",
                category="context_escape",
            ),
            AttackPayload(
                attack_type=AttackType.XSS,
                payload="'><img src=x onerror=alert(1)>",
                description="XSS breaking out of single-quoted attribute",
                category="context_escape",
            ),
            AttackPayload(
                attack_type=AttackType.XSS,
                payload='<iframe src="javascript:alert(1)">',
                description="XSS via iframe with javascript: src",
                category="iframe",
            ),
        ]

    @staticmethod
    def generate_path_traversal() -> list[AttackPayload]:
        """Generate path traversal attack payloads."""
        return [
            AttackPayload(
                attack_type=AttackType.PATH_TRAVERSAL,
                payload="../../../etc/passwd",
                description="Classic path traversal to read /etc/passwd",
                category="unix",
            ),
            AttackPayload(
                attack_type=AttackType.PATH_TRAVERSAL,
                payload="..%2F..%2F..%2Fetc%2Fpasswd",
                description="URL-encoded path traversal",
                category="encoded",
            ),
            AttackPayload(
                attack_type=AttackType.PATH_TRAVERSAL,
                payload="....//....//etc/passwd",
                description="Double-dot-slash bypass for naive filters",
                category="filter_bypass",
            ),
            AttackPayload(
                attack_type=AttackType.PATH_TRAVERSAL,
                payload="/etc/shadow",
                description="Direct access to sensitive system file",
                category="unix",
            ),
            AttackPayload(
                attack_type=AttackType.PATH_TRAVERSAL,
                payload="%2e%2e%2f%2e%2e%2f%2e%2e%2fetc%2fpasswd",
                description="Fully URL-encoded traversal path",
                category="encoded",
            ),
            AttackPayload(
                attack_type=AttackType.PATH_TRAVERSAL,
                payload="..\\..\\windows\\system32\\config\\sam",
                description="Windows-style path traversal",
                category="windows",
            ),
        ]

    @staticmethod
    def generate_csrf() -> list[AttackPayload]:
        """Generate CSRF attack payloads."""
        return [
            AttackPayload(
                attack_type=AttackType.CSRF,
                payload='<form action="/api/transfer" method="POST"><input name="amount" value="10000"></form><script>document.forms[0].submit()</script>',
                description="Auto-submitting form CSRF attack",
                category="form_based",
            ),
            AttackPayload(
                attack_type=AttackType.CSRF,
                payload='<img src="/api/delete-account?confirm=true">',
                description="GET-based CSRF via image tag",
                category="get_based",
            ),
            AttackPayload(
                attack_type=AttackType.CSRF,
                payload="fetch('/api/settings', {method:'POST', body:'admin=true', credentials:'include'})",
                description="JavaScript fetch-based CSRF with credentials",
                category="fetch_based",
            ),
            AttackPayload(
                attack_type=AttackType.CSRF,
                payload='<a href="/api/change-email?email=attacker@evil.com">Click here</a>',
                description="Link-based CSRF to change account email",
                category="link_based",
            ),
        ]

    @staticmethod
    def generate_auth_bypass() -> list[AttackPayload]:
        """Generate authentication bypass payloads."""
        return [
            AttackPayload(
                attack_type=AttackType.AUTH_BYPASS,
                payload="eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJzdWIiOiJhZG1pbiIsInJvbGUiOiJhZG1pbiJ9.",
                description="JWT with alg=none to bypass signature verification",
                category="jwt",
            ),
            AttackPayload(
                attack_type=AttackType.AUTH_BYPASS,
                payload='{"sub":"admin","role":"admin","exp":9999999999}',
                description="Modified JWT payload with admin role injection",
                category="jwt",
            ),
            AttackPayload(
                attack_type=AttackType.AUTH_BYPASS,
                payload="",
                description="Missing Authorization header entirely",
                expected_blocked=True,
                category="missing_auth",
            ),
            AttackPayload(
                attack_type=AttackType.AUTH_BYPASS,
                payload="Bearer eyJhbGciOiJIUzI1NiJ9.tampered.invalid_signature",
                description="JWT with invalid/tampered signature",
                category="jwt",
            ),
            AttackPayload(
                attack_type=AttackType.AUTH_BYPASS,
                payload="Bearer ",
                description="Empty Bearer token value",
                category="empty_token",
            ),
            AttackPayload(
                attack_type=AttackType.AUTH_BYPASS,
                payload='{"role":"admin","is_superuser":true}',
                description="Admin role injection via request body",
                category="role_injection",
            ),
        ]

    @staticmethod
    def generate_header_injection() -> list[AttackPayload]:
        """Generate HTTP header injection payloads."""
        return [
            AttackPayload(
                attack_type=AttackType.HEADER_INJECTION,
                payload="value\r\nInjected-Header: malicious",
                description="CRLF injection to add arbitrary response header",
                category="crlf",
            ),
            AttackPayload(
                attack_type=AttackType.HEADER_INJECTION,
                payload="value\r\n\r\n<html>Injected Body</html>",
                description="CRLF injection to inject response body (HTTP response splitting)",
                category="crlf",
            ),
            AttackPayload(
                attack_type=AttackType.HEADER_INJECTION,
                payload="evil.com",
                description="Host header injection for cache poisoning or password reset hijack",
                category="host",
            ),
            AttackPayload(
                attack_type=AttackType.HEADER_INJECTION,
                payload="value%0d%0aSet-Cookie:%20session=hijacked",
                description="URL-encoded CRLF injection to set arbitrary cookies",
                category="crlf",
            ),
        ]

    @staticmethod
    def generate_ssrf() -> list[AttackPayload]:
        """Generate Server-Side Request Forgery (SSRF) payloads."""
        return [
            AttackPayload(
                attack_type=AttackType.SSRF,
                payload="http://169.254.169.254/latest/meta-data/",
                description="AWS metadata endpoint access via SSRF",
                category="cloud_metadata",
            ),
            AttackPayload(
                attack_type=AttackType.SSRF,
                payload="http://127.0.0.1:6379/",
                description="Internal Redis service access via SSRF",
                category="internal_service",
            ),
            AttackPayload(
                attack_type=AttackType.SSRF,
                payload="http://0.0.0.0:22/",
                description="Internal SSH service probing via SSRF",
                category="internal_service",
            ),
            AttackPayload(
                attack_type=AttackType.SSRF,
                payload="file:///etc/passwd",
                description="Local file read via file:// protocol SSRF",
                category="file_protocol",
            ),
        ]

    @staticmethod
    def generate_command_injection() -> list[AttackPayload]:
        """Generate OS command injection payloads."""
        return [
            AttackPayload(
                attack_type=AttackType.COMMAND_INJECTION,
                payload="; cat /etc/passwd",
                description="Semicolon-chained command injection",
                category="chaining",
            ),
            AttackPayload(
                attack_type=AttackType.COMMAND_INJECTION,
                payload="| ls -la /",
                description="Pipe-based command injection",
                category="piping",
            ),
            AttackPayload(
                attack_type=AttackType.COMMAND_INJECTION,
                payload="$(whoami)",
                description="Command substitution injection",
                category="substitution",
            ),
            AttackPayload(
                attack_type=AttackType.COMMAND_INJECTION,
                payload="`id`",
                description="Backtick command injection",
                category="backtick",
            ),
        ]

    @staticmethod
    def generate_rate_limit_bypass() -> list[AttackPayload]:
        """Generate rate limit bypass payloads."""
        return [
            AttackPayload(
                attack_type=AttackType.RATE_LIMIT_BYPASS,
                payload="X-Forwarded-For: 127.0.0.1",
                description="IP spoofing via X-Forwarded-For header to bypass rate limit",
                category="ip_spoofing",
            ),
            AttackPayload(
                attack_type=AttackType.RATE_LIMIT_BYPASS,
                payload="X-Real-IP: 10.0.0.1",
                description="IP spoofing via X-Real-IP header",
                category="ip_spoofing",
            ),
        ]

    @staticmethod
    def generate_file_upload_bypass() -> list[AttackPayload]:
        """Generate file upload bypass payloads."""
        return [
            AttackPayload(
                attack_type=AttackType.FILE_UPLOAD_BYPASS,
                payload="malware.php.jpg",
                description="Double extension to bypass file type filter",
                category="double_extension",
            ),
            AttackPayload(
                attack_type=AttackType.FILE_UPLOAD_BYPASS,
                payload="shell.php%00.jpg",
                description="Null byte injection in filename to truncate extension",
                category="null_byte",
            ),
            AttackPayload(
                attack_type=AttackType.FILE_UPLOAD_BYPASS,
                payload="Content-Type: image/jpeg\n\n<?php system($_GET['cmd']); ?>",
                description="MIME type spoofing with PHP webshell",
                category="mime_spoofing",
            ),
        ]

    @staticmethod
    def generate_brute_force() -> list[AttackPayload]:
        """Generate brute force detection test payloads."""
        return [
            AttackPayload(
                attack_type=AttackType.BRUTE_FORCE,
                payload="admin:password123",
                description="Common credential pair for brute force detection",
                category="credential_stuffing",
            ),
            AttackPayload(
                attack_type=AttackType.BRUTE_FORCE,
                payload="rapid_login_attempts_100",
                description="Simulated rapid login attempts to test lockout",
                category="rate_detection",
            ),
        ]

    @staticmethod
    def generate_idor() -> list[AttackPayload]:
        """Generate Insecure Direct Object Reference payloads."""
        return [
            AttackPayload(
                attack_type=AttackType.IDOR,
                payload="/api/users/1/profile",
                description="Access another user's profile via sequential ID",
                category="sequential_id",
            ),
            AttackPayload(
                attack_type=AttackType.IDOR,
                payload="/api/orders/9999",
                description="Access another user's order by guessing ID",
                category="sequential_id",
            ),
            AttackPayload(
                attack_type=AttackType.IDOR,
                payload="/api/admin/users",
                description="Access admin endpoint without admin role",
                category="privilege_escalation",
            ),
        ]

    @staticmethod
    def generate_all() -> list[AttackPayload]:
        """Generate all attack payloads from every category."""
        payloads: list[AttackPayload] = []
        payloads.extend(AttackPayloadGenerator.generate_sql_injection())
        payloads.extend(AttackPayloadGenerator.generate_xss())
        payloads.extend(AttackPayloadGenerator.generate_path_traversal())
        payloads.extend(AttackPayloadGenerator.generate_csrf())
        payloads.extend(AttackPayloadGenerator.generate_auth_bypass())
        payloads.extend(AttackPayloadGenerator.generate_header_injection())
        payloads.extend(AttackPayloadGenerator.generate_ssrf())
        payloads.extend(AttackPayloadGenerator.generate_command_injection())
        payloads.extend(AttackPayloadGenerator.generate_rate_limit_bypass())
        payloads.extend(AttackPayloadGenerator.generate_file_upload_bypass())
        payloads.extend(AttackPayloadGenerator.generate_brute_force())
        payloads.extend(AttackPayloadGenerator.generate_idor())
        return payloads


# -- Attack Tester Agent -------------------------------------------------


class AttackTester:
    """Security attack testing agent.

    Generates comprehensive attack payloads to validate that generated
    applications resist common security attacks. Produces an AttackReport
    with block rate and pass/fail determination.
    """

    name = "attack_tester"
    display_name = "Attack Tester"
    default_complexity = TaskComplexity.HIGH
    default_model = "claude-sonnet-4-6"  # Security-critical

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

        self.register_tool(ToolDefinition(
            name="generate_attack_payloads",
            description="Generate security attack payloads for testing generated app resistance.",
            parameters={
                "type": "object",
                "properties": {
                    "attack_types": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [t.value for t in AttackType],
                        },
                        "description": "Attack types to generate payloads for. Empty = all types.",
                    },
                },
                "required": [],
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

    async def _run_basic_checks(self, deployment_url: str) -> list[dict]:  # ATTACK-FIX
        """Run basic HTTP-based security probes against a deployed URL.  # ATTACK-FIX

        Probes: SQL injection, XSS reflection, auth bypass (no Authorization header).
        Each probe uses a 5-second timeout and is wrapped in try/except.  # ATTACK-FIX
        """
        findings: list[dict] = []  # ATTACK-FIX
        url = deployment_url.rstrip("/")  # ATTACK-FIX
        async with httpx.AsyncClient(timeout=5.0) as client:  # ATTACK-FIX
            # SQL injection probe  # ATTACK-FIX
            try:
                sqli_payload = "1' OR '1'='1"  # ATTACK-FIX
                resp = await client.get(
                    f"{url}/api/test",
                    params={"id": sqli_payload},
                )
                if resp.status_code == 200 and len(resp.text) > 100:  # ATTACK-FIX
                    findings.append({"type": "sql_injection", "flagged": True, "status": resp.status_code, "details": "Probe returned 200 with substantial data"})  # ATTACK-FIX
            except Exception as exc:  # ATTACK-FIX
                findings.append({"type": "sql_injection", "flagged": False, "details": str(exc)[:80]})  # ATTACK-FIX
            # XSS probe  # ATTACK-FIX
            try:
                xss_payload = "<script>alert(1)</script>"  # ATTACK-FIX
                resp = await client.get(f"{url}/api/test", params={"q": xss_payload})  # ATTACK-FIX
                if "<script>" in resp.text:  # ATTACK-FIX
                    findings.append({"type": "xss", "flagged": True, "status": resp.status_code, "details": "Response body reflects <script> tag"})  # ATTACK-FIX
            except Exception as exc:  # ATTACK-FIX
                findings.append({"type": "xss", "flagged": False, "details": str(exc)[:80]})  # ATTACK-FIX
            # Auth bypass probe - no Authorization header  # ATTACK-FIX
            try:
                resp = await client.get(f"{url}/api/admin")  # ATTACK-FIX
                if resp.status_code == 200:  # ATTACK-FIX
                    findings.append({"type": "auth_bypass", "flagged": True, "status": 200, "details": "Admin endpoint returned 200 without auth"})  # ATTACK-FIX
            except Exception as exc:  # ATTACK-FIX
                findings.append({"type": "auth_bypass", "flagged": False, "details": str(exc)[:80]})  # ATTACK-FIX
        return findings  # ATTACK-FIX

    async def execute(
        self,
        pipeline_run_id: str,
        context: dict[str, Any],
    ) -> AgentResult:
        """Generate attack payloads and produce an attack payload manifest.

        Collects all attack payloads that SHOULD be tested against the
        generated application. Results are marked as ``untested`` — the
        actual block/bypass status must be determined by running these
        payloads against the deployed app or code analysis.

        HONESTY-FIX: Previous version set blocked=expected_blocked which
        made the report meaningless (always 100% block rate). Now results
        are marked as untested, and block_rate is None until real testing.
        """
        # ATTACK-FIX: If deployment_url is in context, run real HTTP checks.
        deployment_url: str | None = context.get("deployment_url") or context.get("deployed_url")  # ATTACK-FIX
        real_check_findings: list[dict] = []  # ATTACK-FIX
        if deployment_url:  # ATTACK-FIX
            real_check_findings = await self._run_basic_checks(deployment_url)  # ATTACK-FIX
            logger.info("attack_real_checks_ran", deployment_url=deployment_url, findings=len(real_check_findings))  # ATTACK-FIX
        payloads = AttackPayloadGenerator.generate_all()

        # Build manifest — payloads are NOT tested, only catalogued.
        # Each result is marked with blocked=False and untested=True
        # so downstream consumers know results are not validated.
        results: list[AttackResult] = []
        for payload in payloads:
            results.append(AttackResult(
                attack_type=payload.attack_type,
                payload=payload.payload,
                blocked=False,  # Not tested — do not assume blocked
                details=f"[UNTESTED] {payload.description}",
            ))

        report = AttackReport(
            results=results,
            total_tests=len(results),
            blocked_count=0,  # Nothing actually tested
            bypassed_count=0,
        )

        output: dict[str, Any] = {
            "manifest_only": not bool(deployment_url),  # ATTACK-FIX: False if real checks ran
            "manifest_only_reason": "" if deployment_url else "No deployed URL to test against",  # ATTACK-FIX
            "real_check_findings": real_check_findings,  # ATTACK-FIX
            "total_payloads": report.total_tests,
            "blocked": 0,
            "bypassed": 0,
            "untested": report.total_tests,
            "block_rate": None,  # Cannot compute — not tested
            "passed": False,  # Cannot pass without real testing
            "attack_types_tested": list({r.attack_type.value for r in results}),
            "results": [
                {
                    "attack_type": r.attack_type.value,
                    "payload": r.payload,
                    "blocked": r.blocked,
                    "tested": False,
                    "response_code": r.response_code,
                    "details": r.details,
                }
                for r in results
            ],
        }

        logger.info(
            "attack_manifest_generated",
            total_payloads=report.total_tests,
            attack_types=len({r.attack_type for r in results}),
            manifest_only=not bool(deployment_url),  # ATTACK-FIX
        )

        # STORE-FIX: Persist output to context engine for downstream agents
        await store_output(self, pipeline_run_id, output)

        # HONESTY-FIX: Return COMPLETED (manifest generated successfully).
        # The manifest is an input for downstream real testing, not a pass/fail verdict.
        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.COMPLETED,
            output=output,
        )


# -- Register Agent ------------------------------------------------------

register_agent(AttackTester())
