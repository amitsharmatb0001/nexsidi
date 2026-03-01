"""
Security Checklist Verifier (Gap 223-242) for NexSidi v2.

After code generation, verifies that every required security feature
is present in the generated code by scanning files with regex patterns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ChecklistItem:
    """A single security checklist requirement."""

    category: str
    description: str
    severity: str  # "critical", "high", "medium"
    patterns: Tuple[str, ...]

    def __post_init__(self) -> None:
        if self.severity not in ("critical", "high", "medium"):
            raise ValueError(
                f"Invalid severity '{self.severity}'; "
                "must be 'critical', 'high', or 'medium'"
            )


@dataclass
class ChecklistResult:
    """Result of evaluating a single checklist item against generated code."""

    item: ChecklistItem
    present: bool
    matched_files: List[str] = field(default_factory=list)
    matched_pattern: str = ""


@dataclass
class SecurityChecklistReport:
    """Aggregated report across all checklist items."""

    results: List[ChecklistResult]
    total_checks: int
    passed_checks: int
    failed_checks: int
    critical_missing: List[str]
    score: float  # 0.0 .. 100.0
    passed: bool  # True when no critical items are missing


# ---------------------------------------------------------------------------
# Pattern definitions per framework
# ---------------------------------------------------------------------------

_PYTHON_PATTERNS: Dict[str, Tuple[str, ...]] = {
    "rate_limiting": (
        r"\bSlowAPI\b",
        r"\bLimiter\b",
        r"\brate_limit\b",
        r"\bthrottle\b",
        r"from\s+slowapi\b",
        r"from\s+fastapi_limiter\b",
        r"@[\w.]*limiter\.limit\(",
        r"RateLimitMiddleware",
    ),
    "cors": (
        r"\bCORSMiddleware\b",
        r"\ballow_origins\b",
        r"\ballow_methods\b",
        r"\ballow_headers\b",
        r"add_middleware\(\s*CORSMiddleware",
        r"from\s+fastapi\.middleware\.cors\b",
        r"from\s+starlette\.middleware\.cors\b",
    ),
    "csrf": (
        r"\bcsrf_token\b",
        r"\bCSRFProtect\b",
        r"\bcsrf_protect\b",
        r"from\s+fastapi_csrf_protect\b",
        r"\bgenerate_csrf\b",
        r"\bvalidate_csrf\b",
        r"X-CSRF-Token",
    ),
    "input_validation": (
        r"class\s+\w+\(\s*BaseModel\s*\)",
        r"from\s+pydantic\s+import\b",
        r"\bField\(\s*\.\.\.",
        r"@validator\(",
        r"@field_validator\(",
        r"\bconstr\(",
        r"\bconfloat\(",
        r"\bconint\(",
        r"\bEmailStr\b",
        r"\bHttpUrl\b",
    ),
    "password_hashing": (
        r"\bbcrypt\b",
        r"\bargon2\b",
        r"\bpasslib\b",
        r"\bhash_password\b",
        r"\bverify_password\b",
        r"\bpwd_context\b",
        r"CryptContext\(",
        r"from\s+passlib\.context\b",
        r"\.hash\(\s*password",
        r"\.verify\(\s*password",
    ),
    "jwt_auth": (
        r"\bjwt\.encode\b",
        r"\bjwt\.decode\b",
        r"\bjose\b",
        r"from\s+jose\s+import\b",
        r"\bverify_token\b",
        r"\bget_current_user\b",
        r"\bcreate_access_token\b",
        r"\bOAuth2PasswordBearer\b",
        r"JWTError",
        r"jwt_required",
    ),
    "file_upload_security": (
        r"\bmax_size\b",
        r"\ballowed_extensions\b",
        r"\bcontent_type\b",
        r"\bfile_size\b",
        r"\bUploadFile\b",
        r"ALLOWED_MIME_TYPES",
        r"MAX_UPLOAD_SIZE",
        r"validate_file_type",
        r"validate_file_size",
        r"magic\.from_buffer",
    ),
    "security_headers": (
        r"Strict-Transport-Security",
        r"X-Frame-Options",
        r"X-Content-Type-Options",
        r"Content-Security-Policy",
        r"X-XSS-Protection",
        r"Referrer-Policy",
        r"Permissions-Policy",
        r"SecurityHeadersMiddleware",
        r"add_middleware.*TrustedHostMiddleware",
    ),
    "encryption": (
        r"\bFernet\b",
        r"\bAES\b",
        r"\bencrypt\(",
        r"\bdecrypt\(",
        r"from\s+cryptography\b",
        r"from\s+cryptography\.fernet\b",
        r"AESGCM",
        r"cipher\.encrypt",
        r"cipher\.decrypt",
    ),
    "secrets_management": (
        r"\bos\.environ\b",
        r"\bos\.getenv\b",
        r"\bdotenv\b",
        r"\bload_dotenv\b",
        r"\bsecret_manager\b",
        r"SecretManagerServiceClient",
        r"from\s+google\.cloud\s+import\s+secretmanager\b",
        r"Settings\(\s*\)",
        r"class\s+Settings\(\s*BaseSettings\s*\)",
    ),
    "rbac": (
        r"\brole\b",
        r"\bpermission\b",
        r"\bhas_role\b",
        r"\bis_admin\b",
        r"\bauthorize\b",
        r"\brequire_role\b",
        r"\bcheck_permission\b",
        r"RoleChecker",
        r"Depends\(\s*require_role",
        r"current_user\.role",
    ),
    "audit_logging": (
        r"\baudit\b",
        r"\blog_action\b",
        r"\baudit_log\b",
        r"\bAuditLog\b",
        r"create_audit_entry",
        r"audit_trail",
        r"log_security_event",
    ),
    "session_security": (
        r"\bsession\b",
        r"\bhttponly\b",
        r"\bsecure\b",
        r"\bsamesite\b",
        r"SessionMiddleware",
        r"session_config",
        r"cookie.*secure",
        r"cookie.*httponly",
        r"set_cookie\(.*httponly",
    ),
    "tls_enforcement": (
        r"\bhttps\b",
        r"\bssl\b",
        r"\btls\b",
        r"\bredirect_https\b",
        r"HTTPSRedirectMiddleware",
        r"ssl_context",
        r"FORCE_HTTPS",
        r"SECURE_SSL_REDIRECT",
    ),
    "dependency_security": (
        r"\brequirements\.txt\b",
        r"\bpip-audit\b",
        r"\bsafety\s+check\b",
        r"\bpip\s+install\b.*--require-hashes",
        r"Pipfile\.lock",
        r"poetry\.lock",
        r"dependabot",
        r"pyup",
    ),
}


_TYPESCRIPT_PATTERNS: Dict[str, Tuple[str, ...]] = {
    "rate_limiting": (
        r"\bexpress-rate-limit\b",
        r"\brateLimit\b",
        r"\bRateLimiterMemory\b",
        r"import.*rate-limit",
        r"@Throttle\(",
        r"ThrottlerModule",
        r"ThrottlerGuard",
    ),
    "cors": (
        r"\bcors\(\)",
        r"\bcors\(\{",
        r"import\s+cors\b",
        r"\borigin\s*:",
        r"app\.enableCors\(",
        r"@nestjs/common.*enableCors",
    ),
    "csrf": (
        r"\bcsurf\b",
        r"\bcsrf\b",
        r"import.*csurf",
        r"csrf_token",
        r"csrfToken",
        r"X-CSRF-Token",
        r"X-XSRF-TOKEN",
    ),
    "input_validation": (
        r"\bzod\b",
        r"\bz\.object\b",
        r"\bz\.string\b",
        r"\bclass-validator\b",
        r"\bIsEmail\b",
        r"\bIsNotEmpty\b",
        r"\bValidationPipe\b",
        r"\bJoi\b",
        r"\.safeParse\(",
        r"\.parse\(",
    ),
    "password_hashing": (
        r"\bbcrypt\b",
        r"\bargon2\b",
        r"\bhash_password\b",
        r"\bhashPassword\b",
        r"\bcomparePassword\b",
        r"\bbcrypt\.hash\b",
        r"\bbcrypt\.compare\b",
        r"import.*bcryptjs",
        r"import.*argon2",
    ),
    "jwt_auth": (
        r"\bjsonwebtoken\b",
        r"\bjwt\.sign\b",
        r"\bjwt\.verify\b",
        r"\bverifyToken\b",
        r"\bauthGuard\b",
        r"\bAuthGuard\b",
        r"\bJwtModule\b",
        r"\bJwtStrategy\b",
        r"\bPassportStrategy\b",
        r"import.*jsonwebtoken",
    ),
    "file_upload_security": (
        r"\bmaxSize\b",
        r"\ballowedExtensions\b",
        r"\bcontentType\b",
        r"\bfileSize\b",
        r"\bmulter\b",
        r"fileFilter",
        r"MAX_FILE_SIZE",
        r"ALLOWED_MIME_TYPES",
        r"FileInterceptor",
    ),
    "security_headers": (
        r"\bhelmet\b",
        r"import.*helmet",
        r"app\.use\(\s*helmet",
        r"Strict-Transport-Security",
        r"X-Frame-Options",
        r"X-Content-Type-Options",
        r"Content-Security-Policy",
        r"contentSecurityPolicy",
    ),
    "encryption": (
        r"\bcrypto\b",
        r"\bcreateCipheriv\b",
        r"\bcreateDecipheriv\b",
        r"import.*crypto",
        r"\bencrypt\(",
        r"\bdecrypt\(",
        r"AES-256-GCM",
        r"AES-256-CBC",
    ),
    "secrets_management": (
        r"\bprocess\.env\b",
        r"\bdotenv\b",
        r"\bconfig\(\)",
        r"import.*dotenv",
        r"ConfigModule",
        r"ConfigService",
        r"@google-cloud/secret-manager",
        r"SecretManagerServiceClient",
    ),
    "rbac": (
        r"\brole\b",
        r"\bpermission\b",
        r"\bhasRole\b",
        r"\bisAdmin\b",
        r"\bauthorize\b",
        r"\bRolesGuard\b",
        r"@Roles\(",
        r"SetMetadata.*roles",
        r"canActivate",
    ),
    "audit_logging": (
        r"\baudit\b",
        r"\blogAction\b",
        r"\bauditLog\b",
        r"\bAuditLog\b",
        r"createAuditEntry",
        r"auditTrail",
        r"logSecurityEvent",
    ),
    "session_security": (
        r"\bsession\b",
        r"\bhttpOnly\b",
        r"\bsecure\b",
        r"\bsameSite\b",
        r"express-session",
        r"cookie.*secure",
        r"cookie.*httpOnly",
    ),
    "tls_enforcement": (
        r"\bhttps\b",
        r"\bssl\b",
        r"\btls\b",
        r"\bredirectHttps\b",
        r"FORCE_HTTPS",
        r"createServer.*https",
        r"express-enforces-ssl",
    ),
    "dependency_security": (
        r"\bpackage-lock\.json\b",
        r"\bnpm\s+audit\b",
        r"\bsnyk\b",
        r"dependabot",
        r"yarn\.lock",
        r"audit-ci",
        r"npm-shrinkwrap",
    ),
}


# ---------------------------------------------------------------------------
# Checklist item definitions (Gaps 223-242)
# ---------------------------------------------------------------------------

def _build_checklist(framework: str) -> List[ChecklistItem]:
    """Build the full security checklist for the given framework."""
    patterns = (
        _PYTHON_PATTERNS if framework == "python" else _TYPESCRIPT_PATTERNS
    )

    items: List[ChecklistItem] = [
        # Gap 223 – Rate limiting
        ChecklistItem(
            category="rate_limiting",
            description="API rate limiting must be configured to prevent abuse and DoS attacks",
            severity="critical",
            patterns=patterns["rate_limiting"],
        ),
        # Gap 224 – CORS
        ChecklistItem(
            category="cors",
            description="CORS must be explicitly configured with allowed origins, methods, and headers",
            severity="critical",
            patterns=patterns["cors"],
        ),
        # Gap 225 – CSRF
        ChecklistItem(
            category="csrf",
            description="CSRF protection must be enabled for state-changing operations",
            severity="high",
            patterns=patterns["csrf"],
        ),
        # Gap 226 – Input validation
        ChecklistItem(
            category="input_validation",
            description="All user inputs must be validated using schema-based validation",
            severity="critical",
            patterns=patterns["input_validation"],
        ),
        # Gap 227 – Password hashing
        ChecklistItem(
            category="password_hashing",
            description="Passwords must be hashed with a strong algorithm (bcrypt, argon2)",
            severity="critical",
            patterns=patterns["password_hashing"],
        ),
        # Gap 228 – JWT authentication
        ChecklistItem(
            category="jwt_auth",
            description="JWT-based authentication must be implemented with proper token verification",
            severity="critical",
            patterns=patterns["jwt_auth"],
        ),
        # Gap 229 – File upload security
        ChecklistItem(
            category="file_upload_security",
            description="File uploads must validate size, extension, and content type",
            severity="high",
            patterns=patterns["file_upload_security"],
        ),
        # Gap 230 – Security headers
        ChecklistItem(
            category="security_headers",
            description="HTTP security headers (HSTS, X-Frame-Options, CSP, etc.) must be set",
            severity="high",
            patterns=patterns["security_headers"],
        ),
        # Gap 231 – Encryption at rest
        ChecklistItem(
            category="encryption",
            description="Sensitive data must be encrypted at rest using AES or equivalent",
            severity="high",
            patterns=patterns["encryption"],
        ),
        # Gap 232 – Secrets management
        ChecklistItem(
            category="secrets_management",
            description="Secrets must be loaded from environment variables or a secrets manager",
            severity="critical",
            patterns=patterns["secrets_management"],
        ),
        # Gap 233 – RBAC
        ChecklistItem(
            category="rbac",
            description="Role-based access control must restrict endpoints by user role",
            severity="critical",
            patterns=patterns["rbac"],
        ),
        # Gap 234 – Audit logging
        ChecklistItem(
            category="audit_logging",
            description="Security-sensitive actions must be recorded in an audit log",
            severity="high",
            patterns=patterns["audit_logging"],
        ),
        # Gap 235 – Session security
        ChecklistItem(
            category="session_security",
            description="Sessions/cookies must use httpOnly, secure, and sameSite flags",
            severity="high",
            patterns=patterns["session_security"],
        ),
        # Gap 236 – TLS enforcement
        ChecklistItem(
            category="tls_enforcement",
            description="All traffic must be served over TLS/HTTPS",
            severity="critical",
            patterns=patterns["tls_enforcement"],
        ),
        # Gap 237-242 – Dependency security
        ChecklistItem(
            category="dependency_security",
            description="Dependencies must be locked and audited for known vulnerabilities",
            severity="medium",
            patterns=patterns["dependency_security"],
        ),
    ]
    return items


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------

class SecurityChecklistVerifier:
    """Scans generated code files against the security checklist."""

    def _resolve_framework(self, framework: str) -> str:
        """Normalise the framework string to 'python' or 'typescript'."""
        fw = framework.strip().lower()
        if fw in (
            "python", "fastapi", "flask", "django", "starlette", "sanic",
        ):
            return "python"
        if fw in (
            "typescript", "javascript", "express", "nestjs", "next",
            "nextjs", "node", "nodejs", "ts", "js",
        ):
            return "typescript"
        # Default to python when uncertain
        return "python"

    def _check_item(
        self,
        item: ChecklistItem,
        files: Dict[str, str],
    ) -> ChecklistResult:
        """Check a single item against all files."""
        matched_files: List[str] = []
        matched_pattern: str = ""

        for pattern_str in item.patterns:
            try:
                compiled = re.compile(pattern_str, re.IGNORECASE)
            except re.error:
                continue

            for filepath, content in files.items():
                if compiled.search(content):
                    if filepath not in matched_files:
                        matched_files.append(filepath)
                    if not matched_pattern:
                        matched_pattern = pattern_str

            # If we already found a match we can stop early
            if matched_files:
                break

        return ChecklistResult(
            item=item,
            present=bool(matched_files),
            matched_files=matched_files,
            matched_pattern=matched_pattern,
        )

    # -- public API ---------------------------------------------------------

    def verify(
        self,
        files: Dict[str, str],
        framework: str = "python",
    ) -> SecurityChecklistReport:
        """Run the full security checklist against *files*.

        Parameters
        ----------
        files:
            Mapping of ``{filepath: source_code}`` for every generated file.
        framework:
            Target framework / language (``"python"``, ``"fastapi"``,
            ``"typescript"``, ``"express"``, ``"nestjs"``, etc.).

        Returns
        -------
        SecurityChecklistReport
        """
        resolved = self._resolve_framework(framework)
        checklist = _build_checklist(resolved)

        results: List[ChecklistResult] = [
            self._check_item(item, files) for item in checklist
        ]

        total = len(results)
        passed = sum(1 for r in results if r.present)
        failed = total - passed

        critical_missing = [
            r.item.category
            for r in results
            if not r.present and r.item.severity == "critical"
        ]

        score = (passed / total * 100.0) if total else 0.0

        return SecurityChecklistReport(
            results=results,
            total_checks=total,
            passed_checks=passed,
            failed_checks=failed,
            critical_missing=critical_missing,
            score=round(score, 2),
            passed=len(critical_missing) == 0,
        )


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_instance: SecurityChecklistVerifier | None = None


def get_security_checklist_verifier() -> SecurityChecklistVerifier:
    """Return the singleton :class:`SecurityChecklistVerifier` instance."""
    global _instance
    if _instance is None:
        _instance = SecurityChecklistVerifier()
    return _instance
