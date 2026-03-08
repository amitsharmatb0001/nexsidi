"""Platform security hardening for the NexSidi v2 platform itself.

Covers Gaps 243-252: Rate limiting, request size limits, security headers,
input sanitization middleware, agent sandbox verification, and secret rotation.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


# --- Rate Limiting (Gap 243-244) ---

class RateLimitStrategy(Enum):
    """Rate limiting strategies."""
    FIXED_WINDOW = "fixed_window"
    SLIDING_WINDOW = "sliding_window"
    TOKEN_BUCKET = "token_bucket"
    LEAKY_BUCKET = "leaky_bucket"


@dataclass(frozen=True)
class RateLimitRule:
    """A rate limit rule for an endpoint or group."""
    name: str
    max_requests: int
    window_seconds: int
    strategy: RateLimitStrategy = RateLimitStrategy.FIXED_WINDOW
    per_ip: bool = True
    per_user: bool = False
    burst_multiplier: float = 1.0  # Allow burst_multiplier * max_requests in bursts

    def __post_init__(self) -> None:
        if self.max_requests < 1:
            raise ValueError("max_requests must be >= 1")
        if self.window_seconds < 1:
            raise ValueError("window_seconds must be >= 1")
        if self.burst_multiplier < 1.0:
            raise ValueError("burst_multiplier must be >= 1.0")


# Predefined rate limit rules for different endpoint groups
DEFAULT_RATE_LIMITS: dict[str, RateLimitRule] = {
    "auth": RateLimitRule(
        name="auth",
        max_requests=10,
        window_seconds=60,
        strategy=RateLimitStrategy.SLIDING_WINDOW,
        per_ip=True,
    ),
    "api": RateLimitRule(
        name="api",
        max_requests=100,
        window_seconds=60,
        strategy=RateLimitStrategy.FIXED_WINDOW,
        per_ip=True,
        per_user=True,
    ),
    "pipeline": RateLimitRule(
        name="pipeline",
        max_requests=5,
        window_seconds=300,
        strategy=RateLimitStrategy.TOKEN_BUCKET,
        per_user=True,
    ),
    "upload": RateLimitRule(
        name="upload",
        max_requests=10,
        window_seconds=60,
        per_ip=True,
    ),
    "webhook": RateLimitRule(
        name="webhook",
        max_requests=50,
        window_seconds=60,
        per_ip=True,
    ),
    "health": RateLimitRule(
        name="health",
        max_requests=60,
        window_seconds=60,
        per_ip=True,
    ),
}


class RateLimiter:
    """In-memory rate limiter supporting multiple strategies."""

    _MAX_KEYS: int = 10_000  # R38-FIX: Hard cap on tracked keys

    def __init__(self) -> None:
        self._windows: dict[str, list[float]] = {}
        self._check_count: int = 0  # R38-FIX: Track checks for periodic eviction

    def _make_key(self, rule_name: str, identifier: str) -> str:
        return f"{rule_name}:{identifier}"

    def _evict_stale(self) -> None:
        """R38-FIX: Remove keys with empty timestamp lists and enforce cap."""
        # Remove keys with empty lists
        stale = [k for k, v in self._windows.items() if not v]
        for k in stale:
            del self._windows[k]

        # Enforce hard cap — drop oldest entries if still over limit
        if len(self._windows) > self._MAX_KEYS:
            sorted_keys = sorted(
                self._windows,
                key=lambda k: self._windows[k][-1] if self._windows[k] else 0,
            )
            for k in sorted_keys[: len(self._windows) - self._MAX_KEYS]:
                del self._windows[k]

    def check(self, rule: RateLimitRule, identifier: str) -> bool:
        """Check if request is allowed. Returns True if allowed, False if rate limited."""
        key = self._make_key(rule.name, identifier)
        now = time.monotonic()

        # R38-FIX: Periodic eviction every 500 checks
        self._check_count += 1
        if self._check_count >= 500:
            self._evict_stale()
            self._check_count = 0

        if key not in self._windows:
            self._windows[key] = []

        # Clean old entries
        window_start = now - rule.window_seconds
        self._windows[key] = [t for t in self._windows[key] if t > window_start]

        max_allowed = int(rule.max_requests * rule.burst_multiplier)
        if len(self._windows[key]) >= max_allowed:
            return False

        self._windows[key].append(now)
        return True

    def get_remaining(self, rule: RateLimitRule, identifier: str) -> int:
        """Get remaining requests for the current window."""
        key = self._make_key(rule.name, identifier)
        now = time.monotonic()

        if key not in self._windows:
            return rule.max_requests

        window_start = now - rule.window_seconds
        active = [t for t in self._windows[key] if t > window_start]
        return max(0, int(rule.max_requests * rule.burst_multiplier) - len(active))

    def reset(self, rule_name: str, identifier: str) -> None:
        """Reset rate limit for a specific key."""
        key = self._make_key(rule_name, identifier)
        self._windows.pop(key, None)

    def reset_all(self) -> None:
        """Reset all rate limits."""
        self._windows.clear()


# --- Request Size Limits (Gap 245) ---

@dataclass(frozen=True)
class RequestSizeLimit:
    """Request size limits for different content types."""
    name: str
    max_body_bytes: int
    max_header_bytes: int = 8 * 1024          # 8KB header limit
    max_url_length: int = 2048                 # URL length limit
    max_field_count: int = 100                 # Max form fields
    max_file_size_bytes: int = 50 * 1024 * 1024  # 50MB file upload limit

    def __post_init__(self) -> None:
        if self.max_body_bytes < 0:
            raise ValueError("max_body_bytes must be >= 0")
        if self.max_header_bytes < 0:
            raise ValueError("max_header_bytes must be >= 0")


DEFAULT_SIZE_LIMITS: dict[str, RequestSizeLimit] = {
    "json": RequestSizeLimit(name="json", max_body_bytes=1 * 1024 * 1024),  # 1MB
    "form": RequestSizeLimit(name="form", max_body_bytes=2 * 1024 * 1024),  # 2MB
    "upload": RequestSizeLimit(
        name="upload",
        max_body_bytes=100 * 1024 * 1024,  # 100MB total
        max_file_size_bytes=50 * 1024 * 1024,  # 50MB per file
    ),
    "webhook": RequestSizeLimit(name="webhook", max_body_bytes=256 * 1024),  # 256KB
}


def validate_request_size(
    content_type: str,
    body_size: int,
    header_size: int = 0,
    url_length: int = 0,
) -> tuple[bool, str]:
    """Validate request sizes against limits. Returns (is_valid, error_message)."""
    # Determine which limit to apply
    if "multipart" in content_type or "upload" in content_type:
        limit = DEFAULT_SIZE_LIMITS["upload"]
    elif "json" in content_type:
        limit = DEFAULT_SIZE_LIMITS["json"]
    elif "form" in content_type:
        limit = DEFAULT_SIZE_LIMITS["form"]
    else:
        limit = DEFAULT_SIZE_LIMITS["json"]  # Default to JSON limits

    if body_size > limit.max_body_bytes:
        return False, f"Request body too large: {body_size} > {limit.max_body_bytes}"
    if header_size > limit.max_header_bytes:
        return False, f"Headers too large: {header_size} > {limit.max_header_bytes}"
    if url_length > limit.max_url_length:
        return False, f"URL too long: {url_length} > {limit.max_url_length}"

    return True, ""


# --- Security Headers (Gap 246) ---

SECURITY_HEADERS: dict[str, str] = {
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains; preload",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "0",  # Disabled — CSP is preferred
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cross-Origin-Embedder-Policy": "require-corp",
}


def get_security_headers(include_csp: bool = True) -> dict[str, str]:
    """Get security headers dict, optionally excluding CSP."""
    headers = dict(SECURITY_HEADERS)
    if not include_csp:
        headers.pop("Content-Security-Policy", None)
    return headers


# --- Input Sanitization (Gap 247-248) ---

# Dangerous patterns to strip/reject
_DANGEROUS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL),
    re.compile(r"javascript:", re.IGNORECASE),
    re.compile(r"on\w+\s*=", re.IGNORECASE),  # onclick=, onerror=, etc.
    re.compile(r"data:text/html", re.IGNORECASE),
    re.compile(r"vbscript:", re.IGNORECASE),
)

_SQL_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"('\s*(OR|AND)\s+[\d']+\s*=\s*[\d']+)", re.IGNORECASE),
    re.compile(r"(UNION\s+SELECT)", re.IGNORECASE),
    re.compile(r"(;\s*DROP\s+TABLE)", re.IGNORECASE),
    re.compile(r"(;\s*DELETE\s+FROM)", re.IGNORECASE),
    re.compile(r"(--\s*$)", re.MULTILINE),
    re.compile(r"(/\*.*?\*/)", re.DOTALL),
)

_PATH_TRAVERSAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\.\./"),
    re.compile(r"\.\.\\"),
    re.compile(r"%2e%2e[/\\]", re.IGNORECASE),
    re.compile(r"%252e%252e", re.IGNORECASE),
)


class InputSanitizer:
    """Sanitize and validate user inputs."""

    def check_xss(self, value: str) -> list[str]:
        """Check for XSS patterns. Returns list of detected threats."""
        threats: list[str] = []
        for pattern in _DANGEROUS_PATTERNS:
            if pattern.search(value):
                threats.append(f"XSS pattern detected: {pattern.pattern[:50]}")
        return threats

    def check_sql_injection(self, value: str) -> list[str]:
        """Check for SQL injection patterns. Returns list of detected threats."""
        threats: list[str] = []
        for pattern in _SQL_INJECTION_PATTERNS:
            if pattern.search(value):
                threats.append(f"SQL injection pattern: {pattern.pattern[:50]}")
        return threats

    def check_path_traversal(self, value: str) -> list[str]:
        """Check for path traversal patterns. Returns list of detected threats."""
        threats: list[str] = []
        for pattern in _PATH_TRAVERSAL_PATTERNS:
            if pattern.search(value):
                threats.append(f"Path traversal pattern: {pattern.pattern[:30]}")
        return threats

    def sanitize(self, value: str) -> tuple[str, list[str]]:
        """Sanitize input value. Returns (sanitized_value, list_of_threats_found)."""
        threats: list[str] = []
        threats.extend(self.check_xss(value))
        threats.extend(self.check_sql_injection(value))
        threats.extend(self.check_path_traversal(value))

        # Strip dangerous HTML tags
        sanitized = value
        for pattern in _DANGEROUS_PATTERNS:
            sanitized = pattern.sub("", sanitized)

        # Strip null bytes
        sanitized = sanitized.replace("\x00", "")

        return sanitized, threats

    def validate_email(self, email: str) -> bool:
        """Basic email validation."""
        pattern = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
        return bool(pattern.match(email)) and len(email) <= 254

    def validate_url(self, url: str) -> bool:
        """Validate URL format (http/https only)."""
        pattern = re.compile(r"^https?://[a-zA-Z0-9.-]+(?:/[^\s]*)?$")
        return bool(pattern.match(url)) and len(url) <= 2048


# --- Agent Sandbox Verification (Gap 249-250) ---

@dataclass
class SandboxRule:
    """A sandbox rule for agent file access."""
    agent_name: str
    allowed_paths: tuple[str, ...]       # Allowed path prefixes
    denied_paths: tuple[str, ...]        # Explicitly denied paths
    can_read_outside: bool = False       # Can read files outside allowed_paths
    can_write_outside: bool = False      # Can write files outside allowed_paths
    can_execute: bool = False            # Can execute system commands
    max_file_size_bytes: int = 10 * 1024 * 1024  # 10MB per file

    def __post_init__(self) -> None:
        if not self.agent_name:
            raise ValueError("agent_name is required")
        if not self.allowed_paths:
            raise ValueError("At least one allowed path required")


class AgentSandbox:
    """Verify that agents only access their own project files."""

    def __init__(self) -> None:
        self._rules: dict[str, SandboxRule] = {}

    def register_rule(self, rule: SandboxRule) -> None:
        """Register a sandbox rule for an agent."""
        self._rules[rule.agent_name] = rule

    def check_access(
        self,
        agent_name: str,
        file_path: str,
        operation: str = "read",
    ) -> tuple[bool, str]:
        """Check if an agent can access a file. Returns (allowed, reason)."""
        if agent_name not in self._rules:
            return False, f"No sandbox rule for agent '{agent_name}'"

        rule = self._rules[agent_name]
        normalized = file_path.replace("\\", "/").rstrip("/")

        # Check denied paths first (takes priority)
        for denied in rule.denied_paths:
            if normalized.startswith(denied.replace("\\", "/")):
                return False, f"Path '{file_path}' is explicitly denied for agent '{agent_name}'"

        # Check allowed paths
        in_allowed = any(
            normalized.startswith(allowed.replace("\\", "/"))
            for allowed in rule.allowed_paths
        )

        if in_allowed:
            return True, "Path is within allowed paths"

        # Outside allowed paths — check permissions
        if operation == "read" and rule.can_read_outside:
            return True, "Agent has read access outside allowed paths"
        if operation == "write" and rule.can_write_outside:
            return True, "Agent has write access outside allowed paths"
        if operation == "execute" and rule.can_execute:
            return True, "Agent has execute permission"

        return False, f"Agent '{agent_name}' cannot {operation} outside allowed paths"

    def verify_agent_isolation(self, project_path: str) -> dict[str, bool]:
        """Verify all registered agents are properly isolated for a project."""
        results: dict[str, bool] = {}
        for name, rule in self._rules.items():
            # Every allowed path should be under the project
            all_scoped = all(
                allowed.replace("\\", "/").startswith(project_path.replace("\\", "/"))
                for allowed in rule.allowed_paths
            )
            results[name] = all_scoped and not rule.can_write_outside
        return results

    def list_rules(self) -> list[str]:
        """List all registered agent sandbox rules."""
        return sorted(self._rules.keys())


# --- Secret Rotation (Gap 251-252) ---

class RotationSchedule(Enum):
    """Secret rotation schedules."""
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUALLY = "annually"


@dataclass(frozen=True)
class SecretRotationPolicy:
    """Policy for secret rotation."""
    secret_name: str
    schedule: RotationSchedule
    max_age_days: int
    auto_rotate: bool = False
    notify_before_days: int = 7
    require_dual_approval: bool = False

    def __post_init__(self) -> None:
        if not self.secret_name:
            raise ValueError("secret_name is required")
        if self.max_age_days < 1:
            raise ValueError("max_age_days must be >= 1")
        if self.notify_before_days < 0:
            raise ValueError("notify_before_days must be >= 0")
        if self.notify_before_days >= self.max_age_days:
            raise ValueError("notify_before_days must be < max_age_days")


DEFAULT_ROTATION_POLICIES: dict[str, SecretRotationPolicy] = {
    "jwt_secret": SecretRotationPolicy(
        secret_name="jwt_secret",
        schedule=RotationSchedule.MONTHLY,
        max_age_days=30,
        auto_rotate=True,
        notify_before_days=7,
    ),
    "api_encryption_key": SecretRotationPolicy(
        secret_name="api_encryption_key",
        schedule=RotationSchedule.QUARTERLY,
        max_age_days=90,
        auto_rotate=False,
        notify_before_days=14,
        require_dual_approval=True,
    ),
    "database_password": SecretRotationPolicy(
        secret_name="database_password",
        schedule=RotationSchedule.QUARTERLY,
        max_age_days=90,
        auto_rotate=False,
        notify_before_days=14,
    ),
    "webhook_signing_key": SecretRotationPolicy(
        secret_name="webhook_signing_key",
        schedule=RotationSchedule.MONTHLY,
        max_age_days=30,
        auto_rotate=True,
        notify_before_days=5,
    ),
}


class SecretRotationManager:
    """Manage secret rotation schedules and enforcement."""

    _MAX_HISTORY: int = 500  # R38-FIX: Cap rotation history

    def __init__(self) -> None:
        self._policies: dict[str, SecretRotationPolicy] = dict(DEFAULT_ROTATION_POLICIES)
        self._last_rotated: dict[str, float] = {}
        self._rotation_history: list[dict[str, str]] = []

    def add_policy(self, policy: SecretRotationPolicy) -> None:
        """Add or update a rotation policy."""
        self._policies[policy.secret_name] = policy

    def get_policy(self, secret_name: str) -> SecretRotationPolicy | None:
        """Get rotation policy for a secret."""
        return self._policies.get(secret_name)

    def record_rotation(self, secret_name: str, rotated_by: str) -> None:
        """Record that a secret was rotated."""
        now = time.time()
        self._last_rotated[secret_name] = now
        self._rotation_history.append({
            "secret_name": secret_name,
            "rotated_by": rotated_by,
            "timestamp": str(now),
            "hash": hashlib.sha256(f"{secret_name}:{now}".encode()).hexdigest()[:16],
        })
        # R38-FIX: Cap rotation history to prevent slow memory leak
        if len(self._rotation_history) > self._MAX_HISTORY:
            self._rotation_history = self._rotation_history[-self._MAX_HISTORY:]

    def check_expiry(self, secret_name: str) -> tuple[bool, int]:
        """Check if a secret needs rotation. Returns (needs_rotation, days_until_expiry)."""
        policy = self._policies.get(secret_name)
        if policy is None:
            return False, -1

        last = self._last_rotated.get(secret_name)
        if last is None:
            return True, 0  # Never rotated — needs rotation now

        elapsed_days = (time.time() - last) / 86400
        remaining = int(policy.max_age_days - elapsed_days)
        needs_rotation = remaining <= 0
        return needs_rotation, max(0, remaining)

    def get_expiring_secrets(self, within_days: int = 7) -> list[str]:
        """Get secrets expiring within N days."""
        expiring: list[str] = []
        for name in self._policies:
            needs, remaining = self.check_expiry(name)
            if needs or remaining <= within_days:
                expiring.append(name)
        return expiring

    def get_rotation_history(self, secret_name: str | None = None) -> list[dict[str, str]]:
        """Get rotation history, optionally filtered by secret name."""
        if secret_name is None:
            return list(self._rotation_history)
        return [r for r in self._rotation_history if r["secret_name"] == secret_name]

    def list_policies(self) -> list[str]:
        """List all registered secret names."""
        return sorted(self._policies.keys())


# --- Singleton Factories ---

_rate_limiter: RateLimiter | None = None
_input_sanitizer: InputSanitizer | None = None
_agent_sandbox: AgentSandbox | None = None
_secret_rotation_manager: SecretRotationManager | None = None


def get_rate_limiter() -> RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter


def get_input_sanitizer() -> InputSanitizer:
    global _input_sanitizer
    if _input_sanitizer is None:
        _input_sanitizer = InputSanitizer()
    return _input_sanitizer


def get_agent_sandbox() -> AgentSandbox:
    global _agent_sandbox
    if _agent_sandbox is None:
        _agent_sandbox = AgentSandbox()
    return _agent_sandbox


def get_secret_rotation_manager() -> SecretRotationManager:
    global _secret_rotation_manager
    if _secret_rotation_manager is None:
        _secret_rotation_manager = SecretRotationManager()
    return _secret_rotation_manager
