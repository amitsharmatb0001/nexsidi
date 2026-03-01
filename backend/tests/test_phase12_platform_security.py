"""Tests for Phase 12: Platform Security Hardening (Gaps 243-252)."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from app.security.platform import (
    DEFAULT_RATE_LIMITS,
    DEFAULT_ROTATION_POLICIES,
    DEFAULT_SIZE_LIMITS,
    SECURITY_HEADERS,
    AgentSandbox,
    InputSanitizer,
    RateLimiter,
    RateLimitRule,
    RateLimitStrategy,
    RequestSizeLimit,
    RotationSchedule,
    SandboxRule,
    SecretRotationManager,
    SecretRotationPolicy,
    get_agent_sandbox,
    get_input_sanitizer,
    get_rate_limiter,
    get_secret_rotation_manager,
    get_security_headers,
    validate_request_size,
)


# ============================================================================
# Rate Limiting Tests
# ============================================================================


class TestRateLimitStrategy:
    """Tests for the RateLimitStrategy enum."""

    def test_enum_has_four_values(self) -> None:
        assert len(RateLimitStrategy) == 4

    def test_fixed_window_value(self) -> None:
        assert RateLimitStrategy.FIXED_WINDOW.value == "fixed_window"

    def test_sliding_window_value(self) -> None:
        assert RateLimitStrategy.SLIDING_WINDOW.value == "sliding_window"

    def test_token_bucket_value(self) -> None:
        assert RateLimitStrategy.TOKEN_BUCKET.value == "token_bucket"

    def test_leaky_bucket_value(self) -> None:
        assert RateLimitStrategy.LEAKY_BUCKET.value == "leaky_bucket"


class TestRateLimitRule:
    """Tests for RateLimitRule dataclass creation and validation."""

    def test_creation_with_valid_params(self) -> None:
        rule = RateLimitRule(name="test", max_requests=100, window_seconds=60)
        assert rule.name == "test"
        assert rule.max_requests == 100
        assert rule.window_seconds == 60
        assert rule.strategy == RateLimitStrategy.FIXED_WINDOW
        assert rule.per_ip is True
        assert rule.per_user is False
        assert rule.burst_multiplier == 1.0

    def test_creation_with_all_params(self) -> None:
        rule = RateLimitRule(
            name="full",
            max_requests=50,
            window_seconds=120,
            strategy=RateLimitStrategy.TOKEN_BUCKET,
            per_ip=False,
            per_user=True,
            burst_multiplier=3.0,
        )
        assert rule.name == "full"
        assert rule.strategy == RateLimitStrategy.TOKEN_BUCKET
        assert rule.per_ip is False
        assert rule.per_user is True
        assert rule.burst_multiplier == 3.0

    def test_max_requests_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="max_requests must be >= 1"):
            RateLimitRule(name="bad", max_requests=0, window_seconds=60)

    def test_max_requests_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="max_requests must be >= 1"):
            RateLimitRule(name="bad", max_requests=-5, window_seconds=60)

    def test_window_seconds_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="window_seconds must be >= 1"):
            RateLimitRule(name="bad", max_requests=10, window_seconds=0)

    def test_window_seconds_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="window_seconds must be >= 1"):
            RateLimitRule(name="bad", max_requests=10, window_seconds=-1)

    def test_burst_multiplier_below_one_raises(self) -> None:
        with pytest.raises(ValueError, match="burst_multiplier must be >= 1.0"):
            RateLimitRule(name="bad", max_requests=10, window_seconds=60, burst_multiplier=0.5)

    def test_burst_multiplier_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="burst_multiplier must be >= 1.0"):
            RateLimitRule(name="bad", max_requests=10, window_seconds=60, burst_multiplier=0.0)

    def test_frozen_immutability(self) -> None:
        rule = RateLimitRule(name="frozen", max_requests=10, window_seconds=60)
        with pytest.raises(AttributeError):
            rule.max_requests = 999  # type: ignore[misc]


class TestDefaultRateLimits:
    """Tests for DEFAULT_RATE_LIMITS configuration."""

    def test_has_six_entries(self) -> None:
        assert len(DEFAULT_RATE_LIMITS) == 6

    def test_contains_auth(self) -> None:
        assert "auth" in DEFAULT_RATE_LIMITS

    def test_contains_api(self) -> None:
        assert "api" in DEFAULT_RATE_LIMITS

    def test_contains_pipeline(self) -> None:
        assert "pipeline" in DEFAULT_RATE_LIMITS

    def test_contains_upload(self) -> None:
        assert "upload" in DEFAULT_RATE_LIMITS

    def test_contains_webhook(self) -> None:
        assert "webhook" in DEFAULT_RATE_LIMITS

    def test_contains_health(self) -> None:
        assert "health" in DEFAULT_RATE_LIMITS

    @pytest.mark.parametrize("key", ["auth", "api", "pipeline", "upload", "webhook", "health"])
    def test_each_rule_has_valid_name(self, key: str) -> None:
        rule = DEFAULT_RATE_LIMITS[key]
        assert rule.name == key

    @pytest.mark.parametrize("key", ["auth", "api", "pipeline", "upload", "webhook", "health"])
    def test_each_rule_has_positive_max_requests(self, key: str) -> None:
        assert DEFAULT_RATE_LIMITS[key].max_requests >= 1

    @pytest.mark.parametrize("key", ["auth", "api", "pipeline", "upload", "webhook", "health"])
    def test_each_rule_has_positive_window(self, key: str) -> None:
        assert DEFAULT_RATE_LIMITS[key].window_seconds >= 1

    def test_auth_uses_sliding_window(self) -> None:
        assert DEFAULT_RATE_LIMITS["auth"].strategy == RateLimitStrategy.SLIDING_WINDOW

    def test_pipeline_uses_token_bucket(self) -> None:
        assert DEFAULT_RATE_LIMITS["pipeline"].strategy == RateLimitStrategy.TOKEN_BUCKET


class TestRateLimiter:
    """Tests for the RateLimiter class."""

    def _make_rule(self, max_req: int = 5, window: int = 60, burst: float = 1.0) -> RateLimitRule:
        return RateLimitRule(
            name="test_rule",
            max_requests=max_req,
            window_seconds=window,
            burst_multiplier=burst,
        )

    def test_allows_requests_under_limit(self) -> None:
        limiter = RateLimiter()
        rule = self._make_rule(max_req=5)
        for _ in range(5):
            assert limiter.check(rule, "user1") is True

    def test_blocks_after_limit_exceeded(self) -> None:
        limiter = RateLimiter()
        rule = self._make_rule(max_req=3)
        for _ in range(3):
            limiter.check(rule, "user1")
        assert limiter.check(rule, "user1") is False

    def test_blocks_consistently_after_exceeded(self) -> None:
        limiter = RateLimiter()
        rule = self._make_rule(max_req=2)
        limiter.check(rule, "ip1")
        limiter.check(rule, "ip1")
        assert limiter.check(rule, "ip1") is False
        assert limiter.check(rule, "ip1") is False

    def test_get_remaining_full(self) -> None:
        limiter = RateLimiter()
        rule = self._make_rule(max_req=10)
        assert limiter.get_remaining(rule, "user1") == 10

    def test_get_remaining_after_requests(self) -> None:
        limiter = RateLimiter()
        rule = self._make_rule(max_req=10)
        for _ in range(3):
            limiter.check(rule, "user1")
        assert limiter.get_remaining(rule, "user1") == 7

    def test_get_remaining_at_zero(self) -> None:
        limiter = RateLimiter()
        rule = self._make_rule(max_req=2)
        limiter.check(rule, "u")
        limiter.check(rule, "u")
        assert limiter.get_remaining(rule, "u") == 0

    def test_reset_clears_specific_key(self) -> None:
        limiter = RateLimiter()
        rule = self._make_rule(max_req=2)
        limiter.check(rule, "user1")
        limiter.check(rule, "user1")
        assert limiter.check(rule, "user1") is False
        limiter.reset("test_rule", "user1")
        assert limiter.check(rule, "user1") is True

    def test_reset_does_not_affect_other_keys(self) -> None:
        limiter = RateLimiter()
        rule = self._make_rule(max_req=2)
        limiter.check(rule, "user1")
        limiter.check(rule, "user1")
        limiter.check(rule, "user2")
        limiter.check(rule, "user2")
        limiter.reset("test_rule", "user1")
        assert limiter.check(rule, "user1") is True
        assert limiter.check(rule, "user2") is False

    def test_reset_all_clears_everything(self) -> None:
        limiter = RateLimiter()
        rule = self._make_rule(max_req=1)
        limiter.check(rule, "a")
        limiter.check(rule, "b")
        assert limiter.check(rule, "a") is False
        assert limiter.check(rule, "b") is False
        limiter.reset_all()
        assert limiter.check(rule, "a") is True
        assert limiter.check(rule, "b") is True

    def test_burst_multiplier_doubles_limit(self) -> None:
        limiter = RateLimiter()
        rule = self._make_rule(max_req=5, burst=2.0)
        for _ in range(10):
            assert limiter.check(rule, "user1") is True
        assert limiter.check(rule, "user1") is False

    def test_burst_multiplier_reflected_in_remaining(self) -> None:
        limiter = RateLimiter()
        rule = self._make_rule(max_req=5, burst=2.0)
        # When key is unknown, get_remaining returns max_requests (not burst-multiplied)
        assert limiter.get_remaining(rule, "user1") == 5
        # After one check (creating the key), burst multiplier is used in calculation
        limiter.check(rule, "user1")
        assert limiter.get_remaining(rule, "user1") == 9

    def test_multiple_identifiers_are_independent(self) -> None:
        limiter = RateLimiter()
        rule = self._make_rule(max_req=2)
        limiter.check(rule, "alice")
        limiter.check(rule, "alice")
        assert limiter.check(rule, "alice") is False
        assert limiter.check(rule, "bob") is True

    def test_different_rules_same_identifier_are_independent(self) -> None:
        limiter = RateLimiter()
        rule_a = RateLimitRule(name="rule_a", max_requests=1, window_seconds=60)
        rule_b = RateLimitRule(name="rule_b", max_requests=1, window_seconds=60)
        limiter.check(rule_a, "user1")
        assert limiter.check(rule_a, "user1") is False
        assert limiter.check(rule_b, "user1") is True

    def test_window_expiry_allows_new_requests(self) -> None:
        limiter = RateLimiter()
        rule = self._make_rule(max_req=1, window=1)
        assert limiter.check(rule, "user1") is True
        assert limiter.check(rule, "user1") is False
        time.sleep(1.1)
        assert limiter.check(rule, "user1") is True


# ============================================================================
# Request Size Limits Tests
# ============================================================================


class TestRequestSizeLimit:
    """Tests for RequestSizeLimit dataclass."""

    def test_creation_with_valid_params(self) -> None:
        limit = RequestSizeLimit(name="test", max_body_bytes=1024)
        assert limit.name == "test"
        assert limit.max_body_bytes == 1024
        assert limit.max_header_bytes == 8 * 1024
        assert limit.max_url_length == 2048
        assert limit.max_field_count == 100
        assert limit.max_file_size_bytes == 50 * 1024 * 1024

    def test_custom_header_bytes(self) -> None:
        limit = RequestSizeLimit(name="t", max_body_bytes=100, max_header_bytes=4096)
        assert limit.max_header_bytes == 4096

    def test_negative_max_body_bytes_raises(self) -> None:
        with pytest.raises(ValueError, match="max_body_bytes must be >= 0"):
            RequestSizeLimit(name="bad", max_body_bytes=-1)

    def test_zero_max_body_bytes_allowed(self) -> None:
        limit = RequestSizeLimit(name="zero", max_body_bytes=0)
        assert limit.max_body_bytes == 0

    def test_negative_max_header_bytes_raises(self) -> None:
        with pytest.raises(ValueError, match="max_header_bytes must be >= 0"):
            RequestSizeLimit(name="bad", max_body_bytes=100, max_header_bytes=-1)

    def test_frozen_immutability(self) -> None:
        limit = RequestSizeLimit(name="frozen", max_body_bytes=100)
        with pytest.raises(AttributeError):
            limit.max_body_bytes = 9999  # type: ignore[misc]


class TestDefaultSizeLimits:
    """Tests for DEFAULT_SIZE_LIMITS configuration."""

    def test_has_four_entries(self) -> None:
        assert len(DEFAULT_SIZE_LIMITS) == 4

    def test_contains_json(self) -> None:
        assert "json" in DEFAULT_SIZE_LIMITS

    def test_contains_form(self) -> None:
        assert "form" in DEFAULT_SIZE_LIMITS

    def test_contains_upload(self) -> None:
        assert "upload" in DEFAULT_SIZE_LIMITS

    def test_contains_webhook(self) -> None:
        assert "webhook" in DEFAULT_SIZE_LIMITS

    def test_json_body_limit_is_1mb(self) -> None:
        assert DEFAULT_SIZE_LIMITS["json"].max_body_bytes == 1 * 1024 * 1024

    def test_upload_body_limit_is_100mb(self) -> None:
        assert DEFAULT_SIZE_LIMITS["upload"].max_body_bytes == 100 * 1024 * 1024


class TestValidateRequestSize:
    """Tests for validate_request_size function."""

    def test_json_within_limit(self) -> None:
        ok, msg = validate_request_size("application/json", body_size=1000)
        assert ok is True
        assert msg == ""

    def test_json_too_large(self) -> None:
        ok, msg = validate_request_size("application/json", body_size=2 * 1024 * 1024)
        assert ok is False
        assert "too large" in msg

    def test_multipart_uses_upload_limits(self) -> None:
        ok, msg = validate_request_size(
            "multipart/form-data",
            body_size=50 * 1024 * 1024,  # 50MB -- under 100MB upload limit
        )
        assert ok is True
        assert msg == ""

    def test_multipart_exceeds_upload_limit(self) -> None:
        ok, msg = validate_request_size(
            "multipart/form-data",
            body_size=200 * 1024 * 1024,
        )
        assert ok is False
        assert "too large" in msg

    def test_form_urlencoded_uses_form_limits(self) -> None:
        ok, msg = validate_request_size(
            "application/x-www-form-urlencoded",
            body_size=1 * 1024 * 1024,  # 1MB under 2MB limit
        )
        assert ok is True

    def test_form_urlencoded_exceeds_limit(self) -> None:
        ok, msg = validate_request_size(
            "application/x-www-form-urlencoded",
            body_size=3 * 1024 * 1024,
        )
        assert ok is False

    def test_headers_too_large(self) -> None:
        ok, msg = validate_request_size(
            "application/json",
            body_size=100,
            header_size=9 * 1024,  # > 8KB default
        )
        assert ok is False
        assert "Headers too large" in msg

    def test_url_too_long(self) -> None:
        ok, msg = validate_request_size(
            "application/json",
            body_size=100,
            url_length=3000,
        )
        assert ok is False
        assert "URL too long" in msg

    def test_unknown_content_type_defaults_to_json(self) -> None:
        # Unknown content type -> json limit (1MB)
        ok, _ = validate_request_size("application/octet-stream", body_size=500 * 1024)
        assert ok is True
        ok, _ = validate_request_size("application/octet-stream", body_size=2 * 1024 * 1024)
        assert ok is False

    def test_upload_keyword_in_content_type(self) -> None:
        ok, msg = validate_request_size("application/upload-stream", body_size=50 * 1024 * 1024)
        assert ok is True


# ============================================================================
# Security Headers Tests
# ============================================================================


class TestSecurityHeaders:
    """Tests for SECURITY_HEADERS and get_security_headers."""

    def test_has_ten_entries(self) -> None:
        assert len(SECURITY_HEADERS) == 10

    def test_contains_hsts(self) -> None:
        assert "Strict-Transport-Security" in SECURITY_HEADERS

    def test_contains_x_content_type_options(self) -> None:
        assert "X-Content-Type-Options" in SECURITY_HEADERS

    def test_contains_x_frame_options(self) -> None:
        assert "X-Frame-Options" in SECURITY_HEADERS

    def test_contains_csp(self) -> None:
        assert "Content-Security-Policy" in SECURITY_HEADERS

    def test_contains_x_xss_protection(self) -> None:
        assert "X-XSS-Protection" in SECURITY_HEADERS

    def test_contains_referrer_policy(self) -> None:
        assert "Referrer-Policy" in SECURITY_HEADERS

    def test_contains_permissions_policy(self) -> None:
        assert "Permissions-Policy" in SECURITY_HEADERS

    def test_contains_coop(self) -> None:
        assert "Cross-Origin-Opener-Policy" in SECURITY_HEADERS

    def test_contains_corp(self) -> None:
        assert "Cross-Origin-Resource-Policy" in SECURITY_HEADERS

    def test_contains_coep(self) -> None:
        assert "Cross-Origin-Embedder-Policy" in SECURITY_HEADERS

    def test_hsts_max_age_at_least_one_year(self) -> None:
        hsts = SECURITY_HEADERS["Strict-Transport-Security"]
        # Extract max-age value
        import re
        match = re.search(r"max-age=(\d+)", hsts)
        assert match is not None
        assert int(match.group(1)) >= 31536000  # 365 days

    def test_x_frame_options_is_deny(self) -> None:
        assert SECURITY_HEADERS["X-Frame-Options"] == "DENY"

    def test_x_content_type_options_is_nosniff(self) -> None:
        assert SECURITY_HEADERS["X-Content-Type-Options"] == "nosniff"

    def test_csp_contains_default_src(self) -> None:
        assert "default-src" in SECURITY_HEADERS["Content-Security-Policy"]

    def test_csp_contains_script_src(self) -> None:
        assert "script-src" in SECURITY_HEADERS["Content-Security-Policy"]

    def test_get_security_headers_include_csp(self) -> None:
        headers = get_security_headers(include_csp=True)
        assert "Content-Security-Policy" in headers

    def test_get_security_headers_exclude_csp(self) -> None:
        headers = get_security_headers(include_csp=False)
        assert "Content-Security-Policy" not in headers
        # Other headers still present
        assert "Strict-Transport-Security" in headers

    def test_get_security_headers_returns_new_dict(self) -> None:
        headers1 = get_security_headers()
        headers2 = get_security_headers()
        assert headers1 is not headers2

    def test_modifying_returned_dict_does_not_affect_global(self) -> None:
        headers = get_security_headers()
        headers["X-Custom"] = "test"
        assert "X-Custom" not in SECURITY_HEADERS


# ============================================================================
# Input Sanitization Tests
# ============================================================================


class TestInputSanitizerXSS:
    """Tests for XSS detection in InputSanitizer."""

    def setup_method(self) -> None:
        self.sanitizer = InputSanitizer()

    def test_detects_script_tag(self) -> None:
        threats = self.sanitizer.check_xss("<script>alert('xss')</script>")
        assert len(threats) > 0
        assert any("XSS" in t for t in threats)

    def test_detects_script_tag_case_insensitive(self) -> None:
        threats = self.sanitizer.check_xss("<SCRIPT>alert(1)</SCRIPT>")
        assert len(threats) > 0

    def test_detects_javascript_protocol(self) -> None:
        threats = self.sanitizer.check_xss("javascript:alert(1)")
        assert len(threats) > 0

    def test_detects_javascript_protocol_mixed_case(self) -> None:
        threats = self.sanitizer.check_xss("JavaScript:void(0)")
        assert len(threats) > 0

    def test_detects_onclick_handler(self) -> None:
        threats = self.sanitizer.check_xss('<div onclick="evil()">click</div>')
        assert len(threats) > 0

    def test_detects_onerror_handler(self) -> None:
        threats = self.sanitizer.check_xss('<img onerror="evil()" src=x>')
        assert len(threats) > 0

    def test_detects_data_text_html(self) -> None:
        threats = self.sanitizer.check_xss("data:text/html,<h1>evil</h1>")
        assert len(threats) > 0

    def test_detects_vbscript(self) -> None:
        threats = self.sanitizer.check_xss("vbscript:msgbox")
        assert len(threats) > 0

    def test_safe_input_returns_empty(self) -> None:
        threats = self.sanitizer.check_xss("Hello, this is a safe string.")
        assert threats == []

    def test_safe_html_entities_not_flagged(self) -> None:
        threats = self.sanitizer.check_xss("&lt;script&gt; is fine as text")
        assert threats == []


class TestInputSanitizerSQLInjection:
    """Tests for SQL injection detection."""

    def setup_method(self) -> None:
        self.sanitizer = InputSanitizer()

    def test_detects_or_1_equals_1(self) -> None:
        threats = self.sanitizer.check_sql_injection("' OR 1=1")
        assert len(threats) > 0
        assert any("SQL injection" in t for t in threats)

    def test_detects_union_select(self) -> None:
        threats = self.sanitizer.check_sql_injection("1 UNION SELECT * FROM users")
        assert len(threats) > 0

    def test_detects_union_select_case_insensitive(self) -> None:
        threats = self.sanitizer.check_sql_injection("1 union select password from users")
        assert len(threats) > 0

    def test_detects_drop_table(self) -> None:
        threats = self.sanitizer.check_sql_injection("; DROP TABLE users")
        assert len(threats) > 0

    def test_detects_delete_from(self) -> None:
        threats = self.sanitizer.check_sql_injection("; DELETE FROM users")
        assert len(threats) > 0

    def test_detects_sql_comment_dash(self) -> None:
        threats = self.sanitizer.check_sql_injection("admin'-- ")
        assert len(threats) > 0

    def test_detects_sql_block_comment(self) -> None:
        threats = self.sanitizer.check_sql_injection("admin /* comment */")
        assert len(threats) > 0

    def test_clean_input_returns_empty(self) -> None:
        threats = self.sanitizer.check_sql_injection("SELECT is a normal word in text")
        assert threats == []

    def test_normal_sentence_not_flagged(self) -> None:
        threats = self.sanitizer.check_sql_injection("My name is John and I live in Paris")
        assert threats == []


class TestInputSanitizerPathTraversal:
    """Tests for path traversal detection."""

    def setup_method(self) -> None:
        self.sanitizer = InputSanitizer()

    def test_detects_dot_dot_slash(self) -> None:
        threats = self.sanitizer.check_path_traversal("../../etc/passwd")
        assert len(threats) > 0
        assert any("Path traversal" in t for t in threats)

    def test_detects_dot_dot_backslash(self) -> None:
        threats = self.sanitizer.check_path_traversal("..\\..\\windows\\system32")
        assert len(threats) > 0

    def test_detects_encoded_traversal(self) -> None:
        threats = self.sanitizer.check_path_traversal("%2e%2e/etc/passwd")
        assert len(threats) > 0

    def test_detects_double_encoded_traversal(self) -> None:
        threats = self.sanitizer.check_path_traversal("%252e%252e%252fetc/passwd")
        assert len(threats) > 0

    def test_clean_path_returns_empty(self) -> None:
        threats = self.sanitizer.check_path_traversal("/home/user/docs/file.txt")
        assert threats == []


class TestInputSanitizerSanitize:
    """Tests for the sanitize() method."""

    def setup_method(self) -> None:
        self.sanitizer = InputSanitizer()

    def test_strips_script_tags(self) -> None:
        result, threats = self.sanitizer.sanitize("<script>evil()</script>Hello")
        assert "<script>" not in result
        assert "Hello" in result
        assert len(threats) > 0

    def test_strips_null_bytes(self) -> None:
        result, _ = self.sanitizer.sanitize("hello\x00world")
        assert "\x00" not in result
        assert "helloworld" in result

    def test_safe_input_unchanged(self) -> None:
        text = "This is perfectly safe text."
        result, threats = self.sanitizer.sanitize(text)
        assert result == text
        assert threats == []

    def test_combines_all_threat_types(self) -> None:
        payload = "<script>x</script>' OR 1=1 --\n../../etc/passwd"
        result, threats = self.sanitizer.sanitize(payload)
        xss_found = any("XSS" in t for t in threats)
        sqli_found = any("SQL injection" in t for t in threats)
        traversal_found = any("Path traversal" in t for t in threats)
        assert xss_found
        assert sqli_found
        assert traversal_found

    def test_strips_javascript_protocol(self) -> None:
        result, threats = self.sanitizer.sanitize("javascript:alert(1)")
        assert "javascript:" not in result
        assert len(threats) > 0

    def test_strips_vbscript(self) -> None:
        result, threats = self.sanitizer.sanitize("vbscript:run")
        assert "vbscript:" not in result


class TestInputSanitizerEmailValidation:
    """Tests for email validation."""

    def setup_method(self) -> None:
        self.sanitizer = InputSanitizer()

    def test_valid_email_simple(self) -> None:
        assert self.sanitizer.validate_email("user@example.com") is True

    def test_valid_email_with_dots(self) -> None:
        assert self.sanitizer.validate_email("first.last@example.co.uk") is True

    def test_valid_email_with_plus(self) -> None:
        assert self.sanitizer.validate_email("user+tag@example.com") is True

    def test_invalid_email_no_at(self) -> None:
        assert self.sanitizer.validate_email("userexample.com") is False

    def test_invalid_email_no_domain(self) -> None:
        assert self.sanitizer.validate_email("user@") is False

    def test_invalid_email_too_long(self) -> None:
        long_email = "a" * 250 + "@b.com"
        assert self.sanitizer.validate_email(long_email) is False

    def test_invalid_email_spaces(self) -> None:
        assert self.sanitizer.validate_email("user @example.com") is False

    def test_invalid_email_empty(self) -> None:
        assert self.sanitizer.validate_email("") is False

    def test_invalid_email_special_chars(self) -> None:
        assert self.sanitizer.validate_email("user<>@example.com") is False


class TestInputSanitizerURLValidation:
    """Tests for URL validation."""

    def setup_method(self) -> None:
        self.sanitizer = InputSanitizer()

    def test_valid_https_url(self) -> None:
        assert self.sanitizer.validate_url("https://example.com") is True

    def test_valid_http_url(self) -> None:
        assert self.sanitizer.validate_url("http://example.com") is True

    def test_valid_url_with_path(self) -> None:
        assert self.sanitizer.validate_url("https://example.com/path/to/resource") is True

    def test_invalid_url_no_protocol(self) -> None:
        assert self.sanitizer.validate_url("example.com") is False

    def test_invalid_url_ftp(self) -> None:
        assert self.sanitizer.validate_url("ftp://example.com") is False

    def test_invalid_url_spaces(self) -> None:
        assert self.sanitizer.validate_url("https://exam ple.com/bad path") is False

    def test_invalid_url_too_long(self) -> None:
        long_url = "https://example.com/" + "a" * 2050
        assert self.sanitizer.validate_url(long_url) is False

    def test_invalid_url_empty(self) -> None:
        assert self.sanitizer.validate_url("") is False

    def test_invalid_url_javascript_scheme(self) -> None:
        assert self.sanitizer.validate_url("javascript:alert(1)") is False


# ============================================================================
# Agent Sandbox Tests
# ============================================================================


class TestSandboxRule:
    """Tests for SandboxRule dataclass."""

    def test_creation_with_valid_params(self) -> None:
        rule = SandboxRule(
            agent_name="agent-1",
            allowed_paths=("/project/src",),
            denied_paths=("/project/src/.env",),
        )
        assert rule.agent_name == "agent-1"
        assert rule.allowed_paths == ("/project/src",)
        assert rule.denied_paths == ("/project/src/.env",)
        assert rule.can_read_outside is False
        assert rule.can_write_outside is False
        assert rule.can_execute is False

    def test_empty_agent_name_raises(self) -> None:
        with pytest.raises(ValueError, match="agent_name is required"):
            SandboxRule(agent_name="", allowed_paths=("/src",), denied_paths=())

    def test_empty_allowed_paths_raises(self) -> None:
        with pytest.raises(ValueError, match="At least one allowed path required"):
            SandboxRule(agent_name="agent", allowed_paths=(), denied_paths=())

    def test_custom_permissions(self) -> None:
        rule = SandboxRule(
            agent_name="agent-2",
            allowed_paths=("/data",),
            denied_paths=(),
            can_read_outside=True,
            can_write_outside=True,
            can_execute=True,
            max_file_size_bytes=5 * 1024 * 1024,
        )
        assert rule.can_read_outside is True
        assert rule.can_write_outside is True
        assert rule.can_execute is True
        assert rule.max_file_size_bytes == 5 * 1024 * 1024


class TestAgentSandbox:
    """Tests for the AgentSandbox class."""

    def _make_sandbox(self) -> AgentSandbox:
        sandbox = AgentSandbox()
        rule = SandboxRule(
            agent_name="test-agent",
            allowed_paths=("/project/src", "/project/tests"),
            denied_paths=("/project/src/.env", "/project/src/secrets"),
        )
        sandbox.register_rule(rule)
        return sandbox

    def test_register_and_list_rules(self) -> None:
        sandbox = self._make_sandbox()
        assert "test-agent" in sandbox.list_rules()

    def test_list_rules_sorted(self) -> None:
        sandbox = AgentSandbox()
        sandbox.register_rule(SandboxRule(agent_name="z-agent", allowed_paths=("/a",), denied_paths=()))
        sandbox.register_rule(SandboxRule(agent_name="a-agent", allowed_paths=("/b",), denied_paths=()))
        assert sandbox.list_rules() == ["a-agent", "z-agent"]

    def test_check_access_allowed_path(self) -> None:
        sandbox = self._make_sandbox()
        allowed, reason = sandbox.check_access("test-agent", "/project/src/main.py")
        assert allowed is True
        assert "allowed" in reason.lower()

    def test_check_access_denied_path_takes_priority(self) -> None:
        sandbox = self._make_sandbox()
        allowed, reason = sandbox.check_access("test-agent", "/project/src/.env")
        assert allowed is False
        assert "denied" in reason.lower()

    def test_check_access_denied_subdirectory(self) -> None:
        sandbox = self._make_sandbox()
        allowed, _ = sandbox.check_access("test-agent", "/project/src/secrets/key.pem")
        assert allowed is False

    def test_check_access_outside_without_permission(self) -> None:
        sandbox = self._make_sandbox()
        allowed, reason = sandbox.check_access("test-agent", "/other/path/file.txt")
        assert allowed is False
        assert "cannot" in reason.lower()

    def test_check_access_outside_with_read_permission(self) -> None:
        sandbox = AgentSandbox()
        rule = SandboxRule(
            agent_name="reader",
            allowed_paths=("/project",),
            denied_paths=(),
            can_read_outside=True,
        )
        sandbox.register_rule(rule)
        allowed, reason = sandbox.check_access("reader", "/other/file.txt", operation="read")
        assert allowed is True
        assert "read access" in reason.lower()

    def test_check_access_outside_read_not_write(self) -> None:
        sandbox = AgentSandbox()
        rule = SandboxRule(
            agent_name="reader",
            allowed_paths=("/project",),
            denied_paths=(),
            can_read_outside=True,
            can_write_outside=False,
        )
        sandbox.register_rule(rule)
        allowed, _ = sandbox.check_access("reader", "/other/file.txt", operation="write")
        assert allowed is False

    def test_check_access_outside_with_write_permission(self) -> None:
        sandbox = AgentSandbox()
        rule = SandboxRule(
            agent_name="writer",
            allowed_paths=("/project",),
            denied_paths=(),
            can_write_outside=True,
        )
        sandbox.register_rule(rule)
        allowed, reason = sandbox.check_access("writer", "/other/file.txt", operation="write")
        assert allowed is True
        assert "write access" in reason.lower()

    def test_check_access_execute_permission(self) -> None:
        sandbox = AgentSandbox()
        rule = SandboxRule(
            agent_name="executor",
            allowed_paths=("/project",),
            denied_paths=(),
            can_execute=True,
        )
        sandbox.register_rule(rule)
        allowed, reason = sandbox.check_access("executor", "/usr/bin/ls", operation="execute")
        assert allowed is True
        assert "execute" in reason.lower()

    def test_check_access_unknown_agent(self) -> None:
        sandbox = self._make_sandbox()
        allowed, reason = sandbox.check_access("unknown-agent", "/any/file")
        assert allowed is False
        assert "No sandbox rule" in reason

    def test_path_normalization_backslashes(self) -> None:
        sandbox = self._make_sandbox()
        allowed, _ = sandbox.check_access("test-agent", "\\project\\src\\main.py")
        assert allowed is True

    def test_verify_agent_isolation_all_under_project(self) -> None:
        sandbox = AgentSandbox()
        rule = SandboxRule(
            agent_name="safe-agent",
            allowed_paths=("/project/src", "/project/tests"),
            denied_paths=(),
            can_write_outside=False,
        )
        sandbox.register_rule(rule)
        results = sandbox.verify_agent_isolation("/project")
        assert results["safe-agent"] is True

    def test_verify_agent_isolation_path_outside_project(self) -> None:
        sandbox = AgentSandbox()
        rule = SandboxRule(
            agent_name="unsafe-agent",
            allowed_paths=("/project/src", "/other/data"),
            denied_paths=(),
        )
        sandbox.register_rule(rule)
        results = sandbox.verify_agent_isolation("/project")
        assert results["unsafe-agent"] is False

    def test_verify_agent_isolation_write_outside_fails(self) -> None:
        sandbox = AgentSandbox()
        rule = SandboxRule(
            agent_name="leaky-agent",
            allowed_paths=("/project/src",),
            denied_paths=(),
            can_write_outside=True,
        )
        sandbox.register_rule(rule)
        results = sandbox.verify_agent_isolation("/project")
        assert results["leaky-agent"] is False


# ============================================================================
# Secret Rotation Tests
# ============================================================================


class TestRotationSchedule:
    """Tests for RotationSchedule enum."""

    def test_enum_has_five_values(self) -> None:
        assert len(RotationSchedule) == 5

    def test_daily_value(self) -> None:
        assert RotationSchedule.DAILY.value == "daily"

    def test_weekly_value(self) -> None:
        assert RotationSchedule.WEEKLY.value == "weekly"

    def test_monthly_value(self) -> None:
        assert RotationSchedule.MONTHLY.value == "monthly"

    def test_quarterly_value(self) -> None:
        assert RotationSchedule.QUARTERLY.value == "quarterly"

    def test_annually_value(self) -> None:
        assert RotationSchedule.ANNUALLY.value == "annually"


class TestSecretRotationPolicy:
    """Tests for SecretRotationPolicy dataclass."""

    def test_creation_with_valid_params(self) -> None:
        policy = SecretRotationPolicy(
            secret_name="my_secret",
            schedule=RotationSchedule.WEEKLY,
            max_age_days=14,
            notify_before_days=3,
        )
        assert policy.secret_name == "my_secret"
        assert policy.schedule == RotationSchedule.WEEKLY
        assert policy.max_age_days == 14
        assert policy.auto_rotate is False
        assert policy.notify_before_days == 3

    def test_empty_secret_name_raises(self) -> None:
        with pytest.raises(ValueError, match="secret_name is required"):
            SecretRotationPolicy(
                secret_name="",
                schedule=RotationSchedule.DAILY,
                max_age_days=1,
            )

    def test_max_age_days_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="max_age_days must be >= 1"):
            SecretRotationPolicy(
                secret_name="s",
                schedule=RotationSchedule.DAILY,
                max_age_days=0,
            )

    def test_max_age_days_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="max_age_days must be >= 1"):
            SecretRotationPolicy(
                secret_name="s",
                schedule=RotationSchedule.DAILY,
                max_age_days=-10,
            )

    def test_notify_before_days_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="notify_before_days must be >= 0"):
            SecretRotationPolicy(
                secret_name="s",
                schedule=RotationSchedule.DAILY,
                max_age_days=30,
                notify_before_days=-1,
            )

    def test_notify_before_days_equals_max_age_raises(self) -> None:
        with pytest.raises(ValueError, match="notify_before_days must be < max_age_days"):
            SecretRotationPolicy(
                secret_name="s",
                schedule=RotationSchedule.DAILY,
                max_age_days=7,
                notify_before_days=7,
            )

    def test_notify_before_days_exceeds_max_age_raises(self) -> None:
        with pytest.raises(ValueError, match="notify_before_days must be < max_age_days"):
            SecretRotationPolicy(
                secret_name="s",
                schedule=RotationSchedule.DAILY,
                max_age_days=7,
                notify_before_days=10,
            )

    def test_frozen_immutability(self) -> None:
        policy = SecretRotationPolicy(
            secret_name="s",
            schedule=RotationSchedule.MONTHLY,
            max_age_days=30,
        )
        with pytest.raises(AttributeError):
            policy.max_age_days = 999  # type: ignore[misc]


class TestDefaultRotationPolicies:
    """Tests for DEFAULT_ROTATION_POLICIES configuration."""

    def test_has_four_entries(self) -> None:
        assert len(DEFAULT_ROTATION_POLICIES) == 4

    def test_contains_jwt_secret(self) -> None:
        assert "jwt_secret" in DEFAULT_ROTATION_POLICIES

    def test_contains_api_encryption_key(self) -> None:
        assert "api_encryption_key" in DEFAULT_ROTATION_POLICIES

    def test_contains_database_password(self) -> None:
        assert "database_password" in DEFAULT_ROTATION_POLICIES

    def test_contains_webhook_signing_key(self) -> None:
        assert "webhook_signing_key" in DEFAULT_ROTATION_POLICIES

    def test_jwt_secret_is_monthly(self) -> None:
        assert DEFAULT_ROTATION_POLICIES["jwt_secret"].schedule == RotationSchedule.MONTHLY

    def test_api_encryption_key_requires_dual_approval(self) -> None:
        assert DEFAULT_ROTATION_POLICIES["api_encryption_key"].require_dual_approval is True


class TestSecretRotationManager:
    """Tests for SecretRotationManager class."""

    def test_add_policy(self) -> None:
        mgr = SecretRotationManager()
        new_policy = SecretRotationPolicy(
            secret_name="custom_key",
            schedule=RotationSchedule.DAILY,
            max_age_days=1,
            notify_before_days=0,
        )
        mgr.add_policy(new_policy)
        assert mgr.get_policy("custom_key") is new_policy

    def test_get_policy_existing(self) -> None:
        mgr = SecretRotationManager()
        policy = mgr.get_policy("jwt_secret")
        assert policy is not None
        assert policy.secret_name == "jwt_secret"

    def test_get_policy_unknown_returns_none(self) -> None:
        mgr = SecretRotationManager()
        assert mgr.get_policy("nonexistent") is None

    def test_record_rotation_stores_in_history(self) -> None:
        mgr = SecretRotationManager()
        mgr.record_rotation("jwt_secret", "admin")
        history = mgr.get_rotation_history("jwt_secret")
        assert len(history) == 1
        assert history[0]["secret_name"] == "jwt_secret"
        assert history[0]["rotated_by"] == "admin"
        assert "timestamp" in history[0]
        assert "hash" in history[0]

    def test_record_rotation_hash_is_16_chars(self) -> None:
        mgr = SecretRotationManager()
        mgr.record_rotation("jwt_secret", "admin")
        history = mgr.get_rotation_history("jwt_secret")
        assert len(history[0]["hash"]) == 16

    def test_check_expiry_never_rotated(self) -> None:
        mgr = SecretRotationManager()
        needs_rotation, days = mgr.check_expiry("jwt_secret")
        assert needs_rotation is True
        assert days == 0

    def test_check_expiry_just_rotated(self) -> None:
        mgr = SecretRotationManager()
        mgr.record_rotation("jwt_secret", "admin")
        needs_rotation, days = mgr.check_expiry("jwt_secret")
        assert needs_rotation is False
        # Should be approximately max_age_days (30 for jwt_secret)
        assert days == 30 or days == 29  # Allow for rounding

    def test_check_expiry_unknown_secret(self) -> None:
        mgr = SecretRotationManager()
        needs_rotation, days = mgr.check_expiry("unknown_secret")
        assert needs_rotation is False
        assert days == -1

    def test_check_expiry_expired_secret(self) -> None:
        mgr = SecretRotationManager()
        # Manually set last rotated to far in the past
        mgr._last_rotated["jwt_secret"] = time.time() - (31 * 86400)  # 31 days ago
        needs_rotation, days = mgr.check_expiry("jwt_secret")
        assert needs_rotation is True
        assert days == 0

    def test_get_expiring_secrets_all_unrotated(self) -> None:
        mgr = SecretRotationManager()
        expiring = mgr.get_expiring_secrets(within_days=9999)
        # All default policies should be expiring since never rotated
        assert len(expiring) == 4

    def test_get_expiring_secrets_after_rotation(self) -> None:
        mgr = SecretRotationManager()
        # Rotate all secrets
        for name in list(mgr._policies.keys()):
            mgr.record_rotation(name, "admin")
        # Within 0 days, none should be expiring after fresh rotation
        expiring = mgr.get_expiring_secrets(within_days=0)
        assert len(expiring) == 0

    def test_get_rotation_history_all(self) -> None:
        mgr = SecretRotationManager()
        mgr.record_rotation("jwt_secret", "admin")
        mgr.record_rotation("database_password", "ops")
        all_history = mgr.get_rotation_history()
        assert len(all_history) == 2

    def test_get_rotation_history_filtered(self) -> None:
        mgr = SecretRotationManager()
        mgr.record_rotation("jwt_secret", "admin")
        mgr.record_rotation("database_password", "ops")
        mgr.record_rotation("jwt_secret", "admin2")
        jwt_history = mgr.get_rotation_history("jwt_secret")
        assert len(jwt_history) == 2
        db_history = mgr.get_rotation_history("database_password")
        assert len(db_history) == 1

    def test_get_rotation_history_empty_for_unknown(self) -> None:
        mgr = SecretRotationManager()
        history = mgr.get_rotation_history("nonexistent")
        assert history == []

    def test_list_policies_returns_sorted(self) -> None:
        mgr = SecretRotationManager()
        policies = mgr.list_policies()
        assert policies == sorted(policies)
        assert len(policies) == 4

    def test_list_policies_after_adding(self) -> None:
        mgr = SecretRotationManager()
        mgr.add_policy(
            SecretRotationPolicy(
                secret_name="aaa_first",
                schedule=RotationSchedule.DAILY,
                max_age_days=1,
                notify_before_days=0,
            )
        )
        policies = mgr.list_policies()
        assert policies[0] == "aaa_first"
        assert len(policies) == 5

    def test_add_policy_overwrites_existing(self) -> None:
        mgr = SecretRotationManager()
        old = mgr.get_policy("jwt_secret")
        assert old is not None
        assert old.max_age_days == 30
        new_policy = SecretRotationPolicy(
            secret_name="jwt_secret",
            schedule=RotationSchedule.WEEKLY,
            max_age_days=7,
            notify_before_days=1,
        )
        mgr.add_policy(new_policy)
        updated = mgr.get_policy("jwt_secret")
        assert updated is not None
        assert updated.max_age_days == 7
        assert updated.schedule == RotationSchedule.WEEKLY

    def test_multiple_rotations_same_secret(self) -> None:
        mgr = SecretRotationManager()
        mgr.record_rotation("jwt_secret", "admin")
        mgr.record_rotation("jwt_secret", "admin")
        mgr.record_rotation("jwt_secret", "admin")
        history = mgr.get_rotation_history("jwt_secret")
        assert len(history) == 3


# ============================================================================
# Singleton Factory Tests
# ============================================================================


class TestSingletonFactories:
    """Tests for singleton factory functions."""

    def test_get_rate_limiter_returns_same_instance(self) -> None:
        # Reset global to ensure clean test
        import app.security.platform as mod
        mod._rate_limiter = None
        a = get_rate_limiter()
        b = get_rate_limiter()
        assert a is b

    def test_get_rate_limiter_returns_correct_type(self) -> None:
        import app.security.platform as mod
        mod._rate_limiter = None
        assert isinstance(get_rate_limiter(), RateLimiter)

    def test_get_input_sanitizer_returns_same_instance(self) -> None:
        import app.security.platform as mod
        mod._input_sanitizer = None
        a = get_input_sanitizer()
        b = get_input_sanitizer()
        assert a is b

    def test_get_input_sanitizer_returns_correct_type(self) -> None:
        import app.security.platform as mod
        mod._input_sanitizer = None
        assert isinstance(get_input_sanitizer(), InputSanitizer)

    def test_get_agent_sandbox_returns_same_instance(self) -> None:
        import app.security.platform as mod
        mod._agent_sandbox = None
        a = get_agent_sandbox()
        b = get_agent_sandbox()
        assert a is b

    def test_get_agent_sandbox_returns_correct_type(self) -> None:
        import app.security.platform as mod
        mod._agent_sandbox = None
        assert isinstance(get_agent_sandbox(), AgentSandbox)

    def test_get_secret_rotation_manager_returns_same_instance(self) -> None:
        import app.security.platform as mod
        mod._secret_rotation_manager = None
        a = get_secret_rotation_manager()
        b = get_secret_rotation_manager()
        assert a is b

    def test_get_secret_rotation_manager_returns_correct_type(self) -> None:
        import app.security.platform as mod
        mod._secret_rotation_manager = None
        assert isinstance(get_secret_rotation_manager(), SecretRotationManager)
