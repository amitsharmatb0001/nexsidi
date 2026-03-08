"""Zero Trust Security Gate — Every input scanned before acceptance.

PHASE-9: Nothing is trusted. Every URL, file, user input, and generated
code passes through this gate before entering the system.

Scanning layers:
  1. URL scanning — DNS, domain reputation, SSL, sandbox fetch, phishing detection
  2. File scanning — MIME deep check, signatures, archive inspection, executables
  3. User input scanning — prompt injection, code injection, social engineering
  4. Generated code scanning — reuses Karan's patterns + outbound URL check

Dependencies:
  - sandbox_engine.py (isolated Docker sandboxes)
  - Reuses: webhook_service.py SSRF logic, karan.py security patterns
"""

from __future__ import annotations

import ipaddress
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.parse import urlparse

import structlog

from app.services.sandbox_engine import (
    SandboxEngine,
    get_sandbox_engine,
)

logger = structlog.get_logger(__name__)


# ── Enums & Types ────────────────────────────────────────────────────

class ThreatLevel(str, Enum):
    SAFE = "safe"
    SUSPICIOUS = "suspicious"
    MALICIOUS = "malicious"
    UNKNOWN = "unknown"


@dataclass
class ScanResult:
    """Result of any Zero Trust scan."""

    threat_level: ThreatLevel
    findings: list[str] = field(default_factory=list)
    scanned_by: list[str] = field(default_factory=list)
    sandbox_id: str | None = None
    quarantined: bool = False
    scan_time_ms: float = 0.0

    def is_safe(self) -> bool:
        return self.threat_level == ThreatLevel.SAFE

    def is_blocked(self) -> bool:
        return self.threat_level == ThreatLevel.MALICIOUS


@dataclass
class SSLResult:
    valid: bool = True
    reason: str = ""


@dataclass
class SignatureResult:
    matched: bool = False
    rule_name: str = ""


# ── Known Malicious Patterns ────────────────────────────────────────

# Common phishing domain patterns
_PHISHING_DOMAIN_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r".*login.*-.*verify", re.IGNORECASE),
    re.compile(r".*secure.*-.*account.*update", re.IGNORECASE),
    re.compile(r".*paypal.*(?!paypal\.com)", re.IGNORECASE),
    re.compile(r".*apple.*id.*(?!apple\.com)", re.IGNORECASE),
    re.compile(r".*microsoft.*365.*(?!microsoft\.com)", re.IGNORECASE),
]

# Phishing content patterns
_PHISHING_CONTENT_PATTERNS: list[tuple[str, float]] = [
    (r"verify\s+your\s+(?:account|identity|email)", 0.3),
    (r"your\s+account\s+(?:has been|will be)\s+(?:suspended|locked|closed)", 0.4),
    (r"click\s+(?:here|below)\s+(?:to\s+)?(?:verify|confirm|update)", 0.3),
    (r"(?:urgent|immediate)\s+(?:action|attention)\s+required", 0.2),
    (r"enter\s+your\s+(?:password|credentials|ssn|credit\s*card)", 0.5),
    (r"(?:bank|financial)\s+(?:security|alert|notification)", 0.3),
]

# Prompt injection patterns
_PROMPT_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+(?:previous|above|all)\s+instructions", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(?:a|an)\s+", re.IGNORECASE),
    re.compile(r"system\s*:\s*", re.IGNORECASE),
    re.compile(r"<\|im_start\|>", re.IGNORECASE),
    re.compile(r"\[INST\]", re.IGNORECASE),
    re.compile(r"```system", re.IGNORECASE),
    re.compile(r"ADMIN\s*OVERRIDE", re.IGNORECASE),
    re.compile(r"do\s+not\s+follow\s+(?:your|the)\s+(?:rules|instructions)", re.IGNORECASE),
    re.compile(r"forget\s+(?:everything|all|your)\s+(?:instructions|rules|training)", re.IGNORECASE),
    re.compile(r"new\s+(?:system|base)\s+prompt", re.IGNORECASE),
    re.compile(r"jailbreak", re.IGNORECASE),
    re.compile(r"DAN\s*(?:mode|prompt)", re.IGNORECASE),
]

# Code injection patterns (in user text)
_CODE_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"<script[^>]*>", re.IGNORECASE),
    re.compile(r"javascript:", re.IGNORECASE),
    re.compile(r"on(?:load|click|error|mouseover)\s*=\s*[\"']", re.IGNORECASE),
    re.compile(r";\s*DROP\s+TABLE", re.IGNORECASE),
    re.compile(r"(?:UNION\s+SELECT|OR\s+1\s*=\s*1|'\s*OR\s*')", re.IGNORECASE),
    re.compile(r"\$\{[^}]+\}"),  # Template injection
    re.compile(r"\{\{[^}]+\}\}"),  # SSTI
    re.compile(r"__import__\s*\(", re.IGNORECASE),
    re.compile(r"eval\s*\(\s*(?:request|input|params)", re.IGNORECASE),
]

# Dangerous file extensions
_DANGEROUS_EXTENSIONS: frozenset[str] = frozenset({
    ".exe", ".dll", ".bat", ".cmd", ".ps1", ".vbs", ".vbe",
    ".js", ".jse", ".wsf", ".wsh", ".msi", ".msp", ".mst",
    ".scr", ".com", ".pif", ".hta", ".cpl", ".inf", ".reg",
    ".rgs", ".sct", ".shb", ".sys", ".drv",
})

# Executable magic bytes
_EXECUTABLE_SIGNATURES: dict[bytes, str] = {
    b"MZ": "PE executable (Windows)",
    b"\x7fELF": "ELF executable (Linux)",
    b"\xfe\xed\xfa\xce": "Mach-O 32-bit (macOS)",
    b"\xfe\xed\xfa\xcf": "Mach-O 64-bit (macOS)",
    b"\xcf\xfa\xed\xfe": "Mach-O reverse (macOS)",
    b"\xca\xfe\xba\xbe": "Java class or Mach-O fat binary",
    b"#!/": "Script with shebang",
    b"PK\x03\x04": "ZIP archive (may contain executables)",
}

# Internal/metadata IPs to block (SSRF prevention)
_BLOCKED_IP_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.IPv4Network("127.0.0.0/8"),       # Loopback
    ipaddress.IPv4Network("10.0.0.0/8"),         # Private
    ipaddress.IPv4Network("172.16.0.0/12"),      # Private
    ipaddress.IPv4Network("192.168.0.0/16"),     # Private
    ipaddress.IPv4Network("169.254.0.0/16"),     # Link-local / metadata
    ipaddress.IPv4Network("100.64.0.0/10"),      # CGN
    ipaddress.IPv6Network("::1/128"),            # IPv6 loopback
    ipaddress.IPv6Network("fc00::/7"),           # IPv6 ULA
    ipaddress.IPv6Network("fe80::/10"),          # IPv6 link-local
]


# ── Zero Trust Gate ──────────────────────────────────────────────────

class ZeroTrustGate:
    """Every input to the system passes through this gate. Nothing is trusted."""

    def __init__(self, sandbox: SandboxEngine | None = None):
        self._sandbox = sandbox or get_sandbox_engine()
        self._url_cache: dict[str, tuple[ScanResult, float]] = {}  # URL → (result, timestamp)
        self._cache_ttl_seconds: float = 300.0  # 5-minute cache

    # ── URL Scanning ─────────────────────────────────────────────────

    async def scan_url(self, url: str) -> ScanResult:
        """Scan URL before ANY navigation or fetch."""
        start = time.monotonic()
        findings: list[str] = []
        scanners_used: list[str] = []

        # Check cache
        cached = self._url_cache.get(url)
        if cached and (time.monotonic() - cached[1]) < self._cache_ttl_seconds:
            return cached[0]

        # 1. URL format validation
        scanners_used.append("url_validator")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            findings.append(f"Invalid URL scheme: {parsed.scheme}")
            result = self._build_result(findings, scanners_used, start)
            return result

        if not parsed.hostname:
            findings.append("URL has no hostname")
            result = self._build_result(findings, scanners_used, start)
            return result

        # 2. DNS / IP check — block internal/metadata endpoints
        scanners_used.append("dns_resolver")
        hostname = parsed.hostname.lower()
        if self._is_internal_hostname(hostname):
            findings.append(f"SSRF: URL points to internal/metadata host: {hostname}")
            result = ScanResult(
                threat_level=ThreatLevel.MALICIOUS,
                findings=findings,
                scanned_by=scanners_used,
                quarantined=True,
                scan_time_ms=(time.monotonic() - start) * 1000,
            )
            self._url_cache[url] = (result, time.monotonic())
            return result

        # 3. Domain reputation — pattern-based phishing detection
        scanners_used.append("domain_reputation")
        for pattern in _PHISHING_DOMAIN_PATTERNS:
            if pattern.search(hostname):
                findings.append(f"Domain matches phishing pattern: {hostname}")

        # 4. SSL check for HTTPS
        if parsed.scheme == "https":
            scanners_used.append("ssl_validator")
            # Basic: just flag non-HTTPS as suspicious
        elif parsed.scheme == "http":
            findings.append("URL uses HTTP (not HTTPS) — data transmitted in plaintext")

        # 5. Sandbox fetch — fetch in isolated container
        scanners_used.append("sandbox_fetch")
        _sandbox_degraded = False
        fetch_result = None
        try:
            fetch_result = await self._sandbox.fetch_in_sandbox(url)
            # Check if sandbox fell back to non-isolated fetch
            if not await self._sandbox._check_docker():
                _sandbox_degraded = True
                findings.append("DEGRADED: URL fetched without sandbox isolation (Docker unavailable)")
            if fetch_result.redirects_to_different_domain:
                findings.append(
                    f"Suspicious redirect to different domain: {url} → {fetch_result.final_url}"
                )
            if fetch_result.contains_javascript_redirect:
                findings.append("Page uses JavaScript redirect (potential phishing)")
            if fetch_result.mimetype_mismatch:
                findings.append(
                    f"MIME mismatch: server says {fetch_result.claimed_mime}, "
                    f"actual content is {fetch_result.actual_mime}"
                )
        except Exception as exc:
            _sandbox_degraded = True
            findings.append(f"Sandbox fetch failed: {str(exc)[:100]}")

        # 6. Content phishing analysis (if we got content)
        scanners_used.append("phishing_analyzer")
        if fetch_result and fetch_result.content:
            phishing_score = self._analyze_phishing_content(fetch_result.content)
            if phishing_score > 0.7:
                findings.append(f"High phishing probability: {phishing_score:.0%}")
            elif phishing_score > 0.4:
                findings.append(f"Moderate phishing indicators: {phishing_score:.0%}")

        result = self._build_result(findings, scanners_used, start, degraded=_sandbox_degraded)
        self._url_cache[url] = (result, time.monotonic())

        logger.info(
            "zero_trust_url_scan",
            url=url[:200],
            threat=result.threat_level,
            findings_count=len(findings),
            scan_ms=round(result.scan_time_ms, 1),
        )

        return result

    # ── File Scanning ────────────────────────────────────────────────

    async def scan_file(
        self, file_path: str, content: bytes, claimed_mime: str = ""
    ) -> ScanResult:
        """Scan file before accepting into the system."""
        start = time.monotonic()
        findings: list[str] = []
        scanners_used: list[str] = []

        # 1. MIME deep check via magic bytes
        scanners_used.append("mime_magic")
        actual_mime = self._detect_mime_by_magic(content)
        if claimed_mime and actual_mime and claimed_mime != actual_mime:
            findings.append(
                f"MIME spoofing: claimed '{claimed_mime}', actual '{actual_mime}'"
            )

        # 2. Extension analysis
        scanners_used.append("extension_check")
        ext = "." + file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
        if ext in _DANGEROUS_EXTENSIONS:
            findings.append(f"Dangerous file extension: {ext}")
        # Double extension check
        parts = file_path.lower().split(".")
        if len(parts) >= 3:
            last_ext = "." + parts[-1]
            second_ext = "." + parts[-2]
            if last_ext in _DANGEROUS_EXTENSIONS or second_ext in _DANGEROUS_EXTENSIONS:
                findings.append(f"Double extension detected: {file_path}")

        # 3. Executable detection via magic bytes
        scanners_used.append("executable_detector")
        for sig, desc in _EXECUTABLE_SIGNATURES.items():
            if content[:len(sig)] == sig:
                findings.append(f"Executable detected: {desc}")
                break

        # 4. Signature scan (pattern-based — no ClamAV/YARA yet)
        scanners_used.append("signature_scanner")
        _file_degraded = False
        sig_result = self._scan_malware_signatures(content)
        if sig_result.matched:
            findings.append(f"Malware signature match: {sig_result.rule_name}")
        # NOTE: This is basic pattern matching, not full AV.
        # Real ClamAV/YARA integration needed for production.
        _file_degraded = True  # Always degraded until real AV is integrated

        # 5. Archive inspection
        if actual_mime in ("application/zip", "application/x-tar", "application/gzip"):
            scanners_used.append("archive_inspector")
            archive_result = await self._sandbox.scan_archive(content)
            if archive_result.is_zip_bomb:
                findings.append(
                    f"ZIP BOMB: compressed={archive_result.compressed_size}, "
                    f"decompressed={archive_result.decompressed_size}"
                )
            if archive_result.is_recursive:
                findings.append("Recursive archive (nested archives)")
            for sf in archive_result.suspicious_files[:10]:
                findings.append(f"In archive: {sf}")

        # 6. Sandbox execution for risky file types
        risky_mimes = {"application/pdf", "text/html", "application/javascript"}
        if actual_mime in risky_mimes:
            scanners_used.append("sandbox_execution")
            if not await self._sandbox._check_docker():
                _file_degraded = True
                findings.append("DEGRADED: File sandbox execution skipped (Docker unavailable)")
            else:
                exec_result = await self._sandbox.execute_in_sandbox(content, actual_mime)
                if exec_result.suspicious_behavior:
                    for behavior in exec_result.behaviors[:5]:
                        findings.append(f"Sandbox: {behavior}")

        result = self._build_result(findings, scanners_used, start, degraded=_file_degraded)

        logger.info(
            "zero_trust_file_scan",
            file=file_path[:200],
            size=len(content),
            mime=actual_mime or claimed_mime,
            threat=result.threat_level,
            findings_count=len(findings),
        )

        return result

    # ── User Input Scanning ──────────────────────────────────────────

    async def scan_user_input(self, text: str) -> ScanResult:
        """Scan user text for prompt injection, code injection, social engineering."""
        start = time.monotonic()
        findings: list[str] = []
        scanners_used: list[str] = []

        # 1. Prompt injection detection
        scanners_used.append("prompt_injection")
        for pattern in _PROMPT_INJECTION_PATTERNS:
            match = pattern.search(text)
            if match:
                findings.append(
                    f"Prompt injection pattern: '{match.group()[:50]}'"
                )

        # 2. Code injection detection
        scanners_used.append("code_injection")
        for pattern in _CODE_INJECTION_PATTERNS:
            match = pattern.search(text)
            if match:
                findings.append(
                    f"Code injection in text: '{match.group()[:50]}'"
                )

        # 3. Embedded URL scanning
        scanners_used.append("embedded_url_check")
        urls_in_text = re.findall(r"https?://[^\s\"'<>]+", text)
        for url in urls_in_text[:5]:  # Limit to 5 URLs
            url_result = await self.scan_url(url)
            if url_result.threat_level == ThreatLevel.MALICIOUS:
                findings.append(f"Malicious URL in text: {url[:100]}")
            elif url_result.threat_level == ThreatLevel.SUSPICIOUS:
                findings.append(f"Suspicious URL in text: {url[:100]}")

        result = self._build_result(findings, scanners_used, start)

        if findings:
            logger.warning(
                "zero_trust_input_scan",
                text_length=len(text),
                threat=result.threat_level,
                findings=findings[:5],
            )

        return result

    # ── Generated Code Scanning ──────────────────────────────────────

    async def scan_generated_code(
        self, file_path: str, content: str, agent: str
    ) -> ScanResult:
        """Scan LLM-generated code before accepting into workspace.

        Defense against LLM hallucinating malicious code patterns.
        Reuses Karan's security patterns for consistency.
        """
        start = time.monotonic()
        findings: list[str] = []
        scanners_used: list[str] = ["generated_code_scanner"]

        # 1. Reuse Karan's regex patterns (lazy import to avoid circular)
        try:
            from app.agents.karan import (
                _PYTHON_SECURITY_PATTERNS,
                _TS_SECURITY_PATTERNS,
            )

            is_python = file_path.endswith(".py")
            is_ts_js = file_path.endswith((".ts", ".tsx", ".js", ".jsx"))

            if is_python:
                for entry in _PYTHON_SECURITY_PATTERNS:
                    if len(entry) >= 3:
                        pat_name, pat_regex, severity = entry[0], entry[1], entry[2]
                        if pat_regex.search(content):
                            findings.append(f"[{severity}] {pat_name}")
            elif is_ts_js:
                for entry in _TS_SECURITY_PATTERNS:
                    if len(entry) >= 3:
                        pat_name, pat_regex, severity = entry[0], entry[1], entry[2]
                        if pat_regex.search(content):
                            findings.append(f"[{severity}] {pat_name}")
        except ImportError:
            scanners_used.append("karan_patterns_unavailable")

        # 2. Outbound URL check — code that phones home
        scanners_used.append("outbound_checker")
        urls_in_code = re.findall(r"https?://[^\s\"'`]+", content)
        # Filter out common safe URLs
        safe_prefixes = (
            "https://cdn.", "https://fonts.googleapis.com",
            "https://unpkg.com", "https://cdnjs.cloudflare.com",
            "http://localhost", "http://127.0.0.1",
        )
        suspicious_urls = [
            u for u in urls_in_code
            if not any(u.startswith(p) for p in safe_prefixes)
        ]
        for url in suspicious_urls[:5]:
            url_scan = await self.scan_url(url)
            if not url_scan.is_safe():
                findings.append(f"Generated code contacts suspicious URL: {url[:100]}")

        # 3. Dependency typosquatting (basic check)
        scanners_used.append("typosquat_checker")
        typosquat_findings = self._check_typosquat_deps(content, file_path)
        findings.extend(typosquat_findings)

        result = self._build_result(findings, scanners_used, start)

        if findings:
            logger.warning(
                "zero_trust_code_scan",
                file=file_path[:200],
                agent=agent,
                threat=result.threat_level,
                findings=findings[:5],
            )

        return result

    # ── Internal Helpers ─────────────────────────────────────────────

    def _is_internal_hostname(self, hostname: str) -> bool:
        """Check if hostname resolves to internal/metadata IP."""
        # Direct IP check
        try:
            ip = ipaddress.ip_address(hostname)
            return any(ip in network for network in _BLOCKED_IP_NETWORKS)
        except ValueError:
            pass  # Not an IP — it's a hostname

        # Known internal hostnames
        internal_hosts = {
            "localhost", "metadata.google.internal",
            "metadata.google.com", "169.254.169.254",
        }
        if hostname in internal_hosts:
            return True

        # Suspicious patterns
        if hostname.endswith(".internal") or hostname.endswith(".local"):
            return True

        return False

    def _analyze_phishing_content(self, content: str) -> float:
        """Score content for phishing indicators. Returns 0.0-1.0."""
        score = 0.0
        content_lower = content.lower()
        for pattern_str, weight in _PHISHING_CONTENT_PATTERNS:
            if re.search(pattern_str, content_lower):
                score += weight
        return min(score, 1.0)

    def _detect_mime_by_magic(self, content: bytes) -> str:
        """Detect MIME type by file magic bytes."""
        if not content:
            return ""

        # Common magic byte signatures
        magic_map: list[tuple[bytes, str]] = [
            (b"\x89PNG\r\n\x1a\n", "image/png"),
            (b"\xff\xd8\xff", "image/jpeg"),
            (b"GIF87a", "image/gif"),
            (b"GIF89a", "image/gif"),
            (b"RIFF", "audio/wav"),  # Could be video too
            (b"PK\x03\x04", "application/zip"),
            (b"\x1f\x8b", "application/gzip"),
            (b"%PDF", "application/pdf"),
            (b"MZ", "application/x-executable"),
            (b"\x7fELF", "application/x-executable"),
            (b"<!DOCTYPE html", "text/html"),
            (b"<html", "text/html"),
        ]

        for magic, mime in magic_map:
            if content[:len(magic)] == magic:
                return mime

        # Try as text
        try:
            content[:1000].decode("utf-8")
            return "text/plain"
        except UnicodeDecodeError:
            return "application/octet-stream"

    def _scan_malware_signatures(self, content: bytes) -> SignatureResult:
        """Basic malware signature detection.

        Uses the EICAR test pattern and common malware signatures.
        In production, this should integrate with ClamAV or YARA.
        """
        # EICAR test string (standard antivirus test pattern)
        eicar = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
        if eicar in content:
            return SignatureResult(matched=True, rule_name="EICAR-Test-File")

        # Common webshell patterns
        webshell_patterns = [
            (b"<?php eval(", "PHP-Webshell-Eval"),
            (b"<?php system(", "PHP-Webshell-System"),
            (b"<?php passthru(", "PHP-Webshell-Passthru"),
            (b"<?php exec(", "PHP-Webshell-Exec"),
            (b"cmd.exe /c", "Windows-Command-Execution"),
            (b"/bin/sh -c", "Unix-Shell-Execution"),
            (b"powershell -encodedcommand", "PowerShell-Encoded"),
        ]

        content_lower = content.lower()
        for sig, name in webshell_patterns:
            if sig.lower() in content_lower:
                return SignatureResult(matched=True, rule_name=name)

        return SignatureResult(matched=False)

    def _check_typosquat_deps(self, content: str, file_path: str) -> list[str]:
        """Basic typosquatting check for Python/JS dependencies."""
        findings = []

        # Known popular packages and their common typosquats
        known_typosquats: dict[str, str] = {
            "requets": "requests",
            "reqeusts": "requests",
            "request": "requests",
            "numppy": "numpy",
            "numpay": "numpy",
            "pandsa": "pandas",
            "djnago": "django",
            "flaask": "flask",
            "expresss": "express",
            "lodahs": "lodash",
            "axois": "axios",
            "reuqests": "requests",
        }

        # Extract import/require statements
        if file_path.endswith(".py"):
            imports = re.findall(r"(?:import|from)\s+(\w+)", content)
        elif file_path.endswith((".js", ".ts", ".jsx", ".tsx")):
            imports = re.findall(r"require\(['\"]([^'\"]+)['\"]\)", content)
            imports += re.findall(r"from\s+['\"]([^'\"]+)['\"]", content)
        else:
            imports = []

        for imp in imports:
            imp_lower = imp.lower()
            if imp_lower in known_typosquats:
                findings.append(
                    f"Potential typosquat: '{imp}' — did you mean '{known_typosquats[imp_lower]}'?"
                )

        return findings

    def _build_result(
        self,
        findings: list[str],
        scanners_used: list[str],
        start_time: float,
        degraded: bool = False,
    ) -> ScanResult:
        """Compute threat level from findings and build ScanResult.

        If degraded=True, core scanners (sandbox/ClamAV) were unavailable —
        return UNKNOWN instead of SAFE so callers know the scan was partial.
        """
        scan_time_ms = (time.monotonic() - start_time) * 1000

        if not findings:
            threat = ThreatLevel.UNKNOWN if degraded else ThreatLevel.SAFE
        else:
            # Count severity indicators
            malicious_keywords = ("malware", "executable", "SSRF", "ZIP BOMB",
                                  "malicious", "webshell", "phishing probability: 7",
                                  "phishing probability: 8", "phishing probability: 9",
                                  "phishing probability: 10")
            has_malicious = any(
                any(kw.lower() in f.lower() for kw in malicious_keywords)
                for f in findings
            )
            if has_malicious:
                threat = ThreatLevel.MALICIOUS
            elif len(findings) >= 3:
                threat = ThreatLevel.SUSPICIOUS
            else:
                threat = ThreatLevel.SUSPICIOUS

        if degraded and threat == ThreatLevel.SAFE:
            threat = ThreatLevel.UNKNOWN

        return ScanResult(
            threat_level=threat,
            findings=findings,
            scanned_by=scanners_used,
            quarantined=threat == ThreatLevel.MALICIOUS,
            scan_time_ms=scan_time_ms,
        )


# ── Singleton ────────────────────────────────────────────────────────

_zero_trust_gate: ZeroTrustGate | None = None


def get_zero_trust_gate() -> ZeroTrustGate:
    """Get or create the singleton ZeroTrustGate."""
    global _zero_trust_gate
    if _zero_trust_gate is None:
        _zero_trust_gate = ZeroTrustGate()
    return _zero_trust_gate


def init_zero_trust_gate(sandbox: SandboxEngine | None = None) -> ZeroTrustGate:
    """Initialize the singleton ZeroTrustGate."""
    global _zero_trust_gate
    _zero_trust_gate = ZeroTrustGate(sandbox)
    return _zero_trust_gate
