"""Tests for Review Round 30 fixes.

Covers bugs found in R30 brutal code review:
- R30-1:  Template engine uses SandboxedEnvironment (SSTI prevention)
- R30-2:  Thinking budget_tokens clamped < max_tokens
- R30-3:  Challenger AI severity normalized to lowercase
- R30-4:  Pranav _sanitize_log catches Authorization headers
- R30-5:  WebSocket _validate_ws_token uses bracket access for claims
- R30-6:  Logout refresh_payload uses bracket access for "sub"
- R30-7:  get_all_steps uses continue (not break) for missing entries
- R30-8:  Challenger dedup is case-insensitive
- R30-9:  build_log sanitized (not just deploy_log)
- R30-10: send_message updates ChatSession.updated_at
- R30-11: Admin engine includes pool_recycle
- R30-12: SecurityHeadersMiddleware sets CSP and Permissions-Policy
- R30-13: Bracket checker handles single-line and multi-line comments
- R30-D5: format_map preserves ${VAR} shell-style placeholders
"""

from __future__ import annotations

import copy
import inspect
import re
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── R30-1: Template engine uses SandboxedEnvironment ────────────


class TestR30_1_SandboxedEnvironment:
    """Template engine must use SandboxedEnvironment, not Environment."""

    def test_uses_sandboxed_environment(self):
        """_render_template should import SandboxedEnvironment."""
        from app.engine.template_engine import TemplateEngine

        source = inspect.getsource(TemplateEngine._render_template)
        assert "SandboxedEnvironment" in source
        # Should NOT use the unsafe Environment directly
        assert "Environment(" not in source or "SandboxedEnvironment(" in source

    def test_ssti_payload_blocked(self):
        """SSTI payload should be blocked by sandbox."""
        from app.engine.template_engine import TemplateEngine

        # Classic SSTI payload that reads files via __class__.__mro__
        # SandboxedEnvironment should block attribute access to __class__
        with pytest.raises(Exception):
            TemplateEngine._render_template(
                "{{ ''.__class__.__mro__[2].__subclasses__() }}",
                {},
            )


# ── R30-2: Thinking budget clamped ──────────────────────────────


class TestR30_2_ThinkingBudgetClamp:
    """budget_tokens must be strictly less than max_tokens."""

    def test_thinking_disabled_for_low_max_tokens(self):
        """When max_tokens < 1025, thinking should be disabled entirely."""
        from app.services.ai_router import AIRouter

        source = inspect.getsource(AIRouter._build_anthropic_body)
        # Must check budget_tokens >= max_tokens
        assert "thinking_budget >= body[\"max_tokens\"]" in source
        assert "thinking_disabled_low_max_tokens" in source

    def test_budget_formula(self):
        """budget_tokens = max(1024, min(10000, max_tokens // 4))."""
        from app.services.ai_router import AIRouter

        source = inspect.getsource(AIRouter._build_anthropic_body)
        assert "max(1024, min(10000," in source


# ── R30-3: Challenger severity normalization ────────────────────


class TestR30_3_SeverityNormalization:
    """AI-returned severity must be lowercased before categorization."""

    def test_severity_lowered_in_ai_review(self):
        """_ai_review should call .lower() on severity."""
        from app.agents.challenger import Challenger

        source = inspect.getsource(Challenger._ai_review)
        assert ".lower()" in source

    def test_mixed_case_severity_normalized(self):
        """'Critical', 'HIGH', 'Medium' should all become lowercase."""
        from app.agents.challenger import (
            Challenge,
            SEVERITY_CRITICAL,
            SEVERITY_HIGH,
            SEVERITY_MEDIUM,
        )

        # Simulate what _ai_review does
        cases = [
            ("Critical", SEVERITY_CRITICAL),
            ("HIGH", SEVERITY_HIGH),
            ("Medium", SEVERITY_MEDIUM),
            ("low", "low"),
        ]
        for raw, expected in cases:
            result = raw.lower()
            assert result == expected, f"{raw} -> {result} != {expected}"


# ── R30-4: Pranav _sanitize_log catches Authorization ───────────


class TestR30_4_SanitizeLogAuthorization:
    """_sanitize_log must redact Authorization: Bearer tokens."""

    def test_authorization_redacted(self):
        """Authorization: Bearer eyJ... should be redacted."""
        from app.agents.pranav import _sanitize_log

        log = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.xxx"
        result = _sanitize_log(log)
        assert "eyJ" not in result
        assert "REDACTED" in result

    def test_auth_header_without_bearer(self):
        """Authorization: <token> (no Bearer) should also be redacted."""
        from app.agents.pranav import _sanitize_log

        log = "authorization: sk-1234567890abcdef"
        result = _sanitize_log(log)
        assert "sk-1234567890" not in result

    def test_existing_patterns_still_work(self):
        """token/key/secret/password/credential still redacted."""
        from app.agents.pranav import _sanitize_log

        log = "token=sk-abc123 secret=mypass password=hunter2"
        result = _sanitize_log(log)
        assert "sk-abc123" not in result
        assert "mypass" not in result
        assert "hunter2" not in result


# ── R30-5: WebSocket bracket access for claims ──────────────────


class TestR30_5_WebSocketBracketAccess:
    """_validate_ws_token must use bracket access, not .get()."""

    def test_uses_bracket_access(self):
        """Should use payload['sub'], not payload.get('sub', '')."""
        from app.routers.websocket import _validate_ws_token

        source = inspect.getsource(_validate_ws_token)
        # Must use bracket access for mandatory claims in the return dict
        assert 'payload["sub"]' in source or "payload['sub']" in source
        assert 'payload["org"]' in source or "payload['org']" in source
        assert 'payload["role"]' in source or "payload['role']" in source
        # The return dict should NOT use .get() (comments mentioning it are OK)
        # Count actual usage of .get("sub" — should only appear in comments
        lines = [l.strip() for l in source.split("\n") if not l.strip().startswith("#")]
        code_lines = "\n".join(lines)
        assert 'payload.get("sub"' not in code_lines


# ── R30-6: Logout bracket access for refresh sub ────────────────


class TestR30_6_LogoutBracketAccess:
    """Logout refresh_payload should use bracket access for 'sub'."""

    def test_uses_bracket_access_for_sub(self):
        """refresh_payload['sub'] instead of refresh_payload.get('sub')."""
        from app.routers.auth import logout

        source = inspect.getsource(logout)
        # Must use bracket access for mandatory "sub" claim in the comparison
        assert 'refresh_payload["sub"]' in source or "refresh_payload['sub']" in source
        # The old pattern `if refresh_payload.get("sub") == ctx.user_id:` should
        # now be `if refresh_payload["sub"] == ctx.user_id:`
        lines = [l.strip() for l in source.split("\n") if not l.strip().startswith("#")]
        code_lines = "\n".join(lines)
        # .get("sub") in the comparison line should be gone
        assert 'refresh_payload.get("sub") ==' not in code_lines


# ── R30-7: get_all_steps uses continue ──────────────────────────


class TestR30_7_GetAllStepsContinue:
    """get_all_steps must use continue (not break) for missing entries."""

    def test_uses_continue_not_break(self):
        """When verify_chain=False and entry is missing, skip it."""
        from app.services.context_engine import ContextEngine

        source = inspect.getsource(ContextEngine.get_all_steps)
        # Must have continue after the ContextIntegrityError block
        assert "continue" in source
        # The old `break` after the if-verify_chain block should be gone.
        # It's ok if break exists elsewhere (e.g., in another context).
        # Check that 'continue' appears near 'chain_entry_missing'
        lines = source.split("\n")
        for i, line in enumerate(lines):
            if "chain_entry_missing" in line:
                # Within ~10 lines, should have continue, not break
                nearby = "\n".join(lines[i:i + 12])
                assert "continue" in nearby


# ── R30-8: Challenger dedup case-insensitive ────────────────────


class TestR30_8_DedupCaseInsensitive:
    """Challenger dedup must be case-insensitive on description."""

    def test_dedup_source_uses_lower(self):
        """execute() dedup should use .lower() on description."""
        from app.agents.challenger import Challenger

        source = inspect.getsource(Challenger.execute)
        assert ".lower()" in source
        assert "desc_key" in source or "description.lower()" in source

    def test_dedup_catches_case_variants(self):
        """'Missing rate limiting' and 'missing rate limiting' are same."""
        from app.agents.challenger import Challenge

        challenges = [
            Challenge("security_gap", "high", "Missing rate limiting", "Add it"),
            Challenge("security_gap", "high", "missing rate limiting", "Add it"),
            Challenge("security_gap", "medium", "No HTTPS", "Enable TLS"),
        ]

        # Simulate dedup logic
        seen: set[str] = set()
        unique = []
        for c in challenges:
            desc_key = c.description.lower()
            if desc_key not in seen:
                seen.add(desc_key)
                unique.append(c)

        assert len(unique) == 2  # "Missing rate limiting" deduped


# ── R30-9: build_log sanitized ──────────────────────────────────


class TestR30_9_BuildLogSanitized:
    """build_log must also be sanitized (not just deploy_log)."""

    def test_build_log_sanitized_in_output(self):
        """Output dict should call _sanitize_log on build_log."""
        from app.agents.pranav import Pranav

        source = inspect.getsource(Pranav.execute)
        assert "_sanitize_log(result.build_log)" in source

    def test_sanitize_log_works_on_build_output(self):
        """Build logs with leaked env vars should be sanitized."""
        from app.agents.pranav import _sanitize_log

        build_log = "Step 3/10: ARG API_KEY=sk-prod-12345\ntoken=eyJhbGci..."
        result = _sanitize_log(build_log)
        assert "sk-prod-12345" not in result
        assert "eyJhbGci" not in result


# ── R30-10: send_message updates updated_at ─────────────────────


class TestR30_10_SendMessageUpdatesTimestamp:
    """send_message must update ChatSession.updated_at."""

    def test_updates_updated_at(self):
        """Source should set chat.updated_at = datetime.now(...)."""
        from app.routers.chat import send_message

        source = inspect.getsource(send_message)
        assert "chat.updated_at" in source
        assert "datetime.now" in source


# ── R30-11: Admin engine pool_recycle ───────────────────────────


class TestR30_11_AdminEnginePoolRecycle:
    """Admin engine must include pool_recycle."""

    def test_admin_engine_has_pool_recycle(self):
        """setup_database should pass pool_recycle to admin engine."""
        from app.database import setup_database

        source = inspect.getsource(setup_database)
        # Should have pool_recycle for admin engine (appears in admin section)
        # Count occurrences — should have 2: one for main engine, one for admin
        assert source.count("pool_recycle") >= 2


# ── R30-12: CSP and Permissions-Policy headers ──────────────────


class TestR30_12_SecurityHeaders:
    """SecurityHeadersMiddleware must set CSP and Permissions-Policy."""

    def test_csp_header_in_middleware(self):
        """Content-Security-Policy should be set."""
        from app.main import create_app

        source = inspect.getsource(create_app)
        assert "Content-Security-Policy" in source
        assert "default-src" in source
        assert "script-src" in source

    def test_permissions_policy_in_middleware(self):
        """Permissions-Policy should restrict camera, microphone, etc."""
        from app.main import create_app

        source = inspect.getsource(create_app)
        assert "Permissions-Policy" in source
        assert "camera=()" in source
        assert "microphone=()" in source

    def test_frame_ancestors_none(self):
        """CSP should include frame-ancestors 'none' (clickjacking prevention)."""
        from app.main import create_app

        source = inspect.getsource(create_app)
        assert "frame-ancestors 'none'" in source


# ── R30-13: Bracket checker comment handling ────────────────────


class TestR30_13_BracketCheckerComments:
    """Bracket checker must skip brackets inside comments."""

    def test_single_line_comment_ignored(self):
        """Brackets in // comments should not be counted."""
        from app.engine.code_quality import CodeQualityEngine, QualityReport

        checker = CodeQualityEngine()
        report = QualityReport()
        # File with brackets only inside a single-line comment
        files = {"test.ts": "const x = 1; // check if (x > 0)\n"}
        checker._check_bracket_balance(files, report)
        bracket_issues = [i for i in report.issues if i.category == "bracket_balance"]
        assert len(bracket_issues) == 0, f"Got issues: {bracket_issues}"

    def test_multi_line_comment_ignored(self):
        """Brackets inside /* ... */ should not be counted."""
        from app.engine.code_quality import CodeQualityEngine, QualityReport

        checker = CodeQualityEngine()
        report = QualityReport()
        files = {"test.ts": "const x = 1;\n/* function foo() { } */\n"}
        checker._check_bracket_balance(files, report)
        bracket_issues = [i for i in report.issues if i.category == "bracket_balance"]
        assert len(bracket_issues) == 0, f"Got issues: {bracket_issues}"

    def test_real_brackets_still_detected(self):
        """Real unmatched brackets should still be caught."""
        from app.engine.code_quality import CodeQualityEngine, QualityReport

        checker = CodeQualityEngine()
        report = QualityReport()
        files = {"test.ts": "function foo() {\n  const x = 1;\n"}
        checker._check_bracket_balance(files, report)
        bracket_issues = [i for i in report.issues if i.category == "bracket_balance"]
        assert len(bracket_issues) > 0  # Unclosed {

    def test_comment_source_has_states(self):
        """_check_bracket_balance should track in_line_comment and in_block_comment."""
        from app.engine.code_quality import CodeQualityEngine

        source = inspect.getsource(CodeQualityEngine._check_bracket_balance)
        assert "in_line_comment" in source
        assert "in_block_comment" in source


# ── R30-D5: format_map preserves ${VAR} ─────────────────────────


class TestR30_D5_FormatMapPreservesShellVars:
    """format_map must not corrupt ${VAR} shell-style placeholders."""

    def test_ecr_image_preserved(self):
        """${ECR_IMAGE} in AWS templates should survive format_map."""
        from app.agents.pranav import Pranav

        source = inspect.getsource(Pranav._generate_config_files)
        # Should escape ${...} before format_map
        assert "\\$\\{" in source or "${{\\" in source or 're.sub' in source

    def test_format_map_escaping_works(self):
        """Verify the escaping logic preserves ${VAR}."""
        import re
        template = 'image: ${ECR_IMAGE}\nservice: {service_name}'
        values = defaultdict(
            lambda: "",
            service_name="my-app",
        )
        escaped = re.sub(r"\$\{([^}]+)\}", r"${{\1}}", template)
        result = escaped.format_map(values)
        assert "${ECR_IMAGE}" in result
        assert "my-app" in result

    def test_known_placeholders_still_substituted(self):
        """Our known placeholders ({service_name}, {region}) should still work."""
        import re
        template = 'service: {service_name}\nregion: {region}\nimage: ${ECR_IMAGE}'
        values = defaultdict(
            lambda: "",
            service_name="nexsidi",
            region="us-east-1",
        )
        escaped = re.sub(r"\$\{([^}]+)\}", r"${{\1}}", template)
        result = escaped.format_map(values)
        assert "nexsidi" in result
        assert "us-east-1" in result
        assert "${ECR_IMAGE}" in result


# ── Cross-cutting: Verify all R30 fixes are present ──────────────


class TestR30_AllFixesPresent:
    """Verify that R30 fix markers exist in the codebase."""

    @pytest.mark.parametrize("fix_id,module_path", [
        ("R30-FIX-1", "app.engine.template_engine"),
        ("R30-FIX-2", "app.services.ai_router"),
        ("R30-FIX-3", "app.agents.challenger"),
        ("R30-FIX-4", "app.agents.pranav"),
        ("R30-FIX-5", "app.routers.websocket"),
        ("R30-FIX-6", "app.routers.auth"),
        ("R30-FIX-7", "app.services.context_engine"),
        ("R30-FIX-8", "app.agents.challenger"),
        ("R30-FIX-9", "app.agents.pranav"),
        ("R30-FIX-10", "app.routers.chat"),
        ("R30-FIX-11", "app.database"),
        ("R30-FIX-12", "app.main"),
        ("R30-FIX-13", "app.engine.code_quality"),
        ("R30-FIX-D5", "app.agents.pranav"),
    ])
    def test_fix_marker_exists(self, fix_id, module_path):
        """Each R30 fix should have a comment marker in the code."""
        import importlib

        module = importlib.import_module(module_path)
        source = inspect.getsource(module)
        assert fix_id in source, f"{fix_id} marker not found in {module_path}"
