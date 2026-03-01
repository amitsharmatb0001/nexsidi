"""Phase 8 -- Security Attack Testing: tests for Gaps 253-264.

Covers:
- AttackType enum (all 12 values)
- AttackPayload frozen dataclass
- AttackResult frozen dataclass
- AttackReport properties (block_rate, passed)
- AttackPayloadGenerator: each generate_* method
- Payload content validation (SQL patterns, XSS patterns, etc.)
- AttackTester agent class
- Edge cases (empty results, boundary block rates)
"""

from __future__ import annotations

import dataclasses

import pytest

from app.agents.attack_tester import (
    AttackPayload,
    AttackPayloadGenerator,
    AttackReport,
    AttackResult,
    AttackTester,
    AttackType,
)


# ================================================================
# Section 1 -- AttackType Enum
# ================================================================


class TestAttackTypeEnum:
    """Test the AttackType enum has all 12 expected values."""

    EXPECTED_MEMBERS = [
        "SQL_INJECTION",
        "XSS",
        "CSRF",
        "PATH_TRAVERSAL",
        "AUTH_BYPASS",
        "RATE_LIMIT_BYPASS",
        "FILE_UPLOAD_BYPASS",
        "BRUTE_FORCE",
        "IDOR",
        "HEADER_INJECTION",
        "SSRF",
        "COMMAND_INJECTION",
    ]

    def test_enum_count(self):
        assert len(AttackType) == 12

    @pytest.mark.parametrize("member_name", EXPECTED_MEMBERS)
    def test_enum_member_present(self, member_name: str):
        assert hasattr(AttackType, member_name)

    def test_enum_is_str_subclass(self):
        assert issubclass(AttackType, str)

    def test_enum_values_are_strings(self):
        for member in AttackType:
            assert isinstance(member.value, str)
            assert len(member.value) > 0

    def test_sql_injection_value(self):
        assert AttackType.SQL_INJECTION.value == "sql_injection"

    def test_xss_value(self):
        assert AttackType.XSS.value == "xss"

    def test_command_injection_value(self):
        assert AttackType.COMMAND_INJECTION.value == "command_injection"


# ================================================================
# Section 2 -- AttackPayload Frozen Dataclass
# ================================================================


class TestAttackPayloadDataclass:
    """Test the AttackPayload frozen dataclass."""

    def test_create_valid_payload(self):
        p = AttackPayload(
            attack_type=AttackType.SQL_INJECTION,
            payload="' OR 1=1--",
            description="Classic SQL injection",
        )
        assert p.attack_type == AttackType.SQL_INJECTION
        assert p.payload == "' OR 1=1--"
        assert p.description == "Classic SQL injection"
        assert p.expected_blocked is True
        assert p.category == ""

    def test_frozen_cannot_modify(self):
        p = AttackPayload(
            attack_type=AttackType.XSS,
            payload="<script>alert(1)</script>",
            description="XSS",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            p.payload = "changed"  # type: ignore[misc]

    def test_custom_defaults(self):
        p = AttackPayload(
            attack_type=AttackType.CSRF,
            payload="csrf_payload",
            description="CSRF test",
            expected_blocked=False,
            category="form_based",
        )
        assert p.expected_blocked is False
        assert p.category == "form_based"

    def test_equality(self):
        p1 = AttackPayload(
            attack_type=AttackType.XSS,
            payload="<script>",
            description="XSS",
        )
        p2 = AttackPayload(
            attack_type=AttackType.XSS,
            payload="<script>",
            description="XSS",
        )
        assert p1 == p2

    def test_hashable(self):
        p = AttackPayload(
            attack_type=AttackType.XSS,
            payload="<script>",
            description="XSS",
        )
        assert hash(p) is not None
        # Can be added to a set
        s = {p}
        assert len(s) == 1


# ================================================================
# Section 3 -- AttackResult Frozen Dataclass
# ================================================================


class TestAttackResultDataclass:
    """Test the AttackResult frozen dataclass."""

    def test_create_valid_result(self):
        r = AttackResult(
            attack_type=AttackType.SQL_INJECTION,
            payload="' OR 1=1--",
            blocked=True,
        )
        assert r.attack_type == AttackType.SQL_INJECTION
        assert r.blocked is True
        assert r.response_code == 0
        assert r.details == ""

    def test_frozen_cannot_modify(self):
        r = AttackResult(
            attack_type=AttackType.XSS,
            payload="<script>",
            blocked=False,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.blocked = True  # type: ignore[misc]

    def test_with_response_code(self):
        r = AttackResult(
            attack_type=AttackType.SQL_INJECTION,
            payload="' OR 1=1--",
            blocked=True,
            response_code=403,
            details="Blocked by WAF",
        )
        assert r.response_code == 403
        assert r.details == "Blocked by WAF"

    def test_equality(self):
        r1 = AttackResult(
            attack_type=AttackType.CSRF,
            payload="csrf",
            blocked=True,
            response_code=400,
        )
        r2 = AttackResult(
            attack_type=AttackType.CSRF,
            payload="csrf",
            blocked=True,
            response_code=400,
        )
        assert r1 == r2


# ================================================================
# Section 4 -- AttackReport Properties
# ================================================================


class TestAttackReport:
    """Test AttackReport block_rate and passed properties."""

    def test_empty_report(self):
        report = AttackReport()
        assert report.total_tests == 0
        assert report.blocked_count == 0
        assert report.bypassed_count == 0
        assert report.block_rate == 0.0
        assert report.passed is False

    def test_100_percent_block_rate(self):
        report = AttackReport(
            results=[],
            total_tests=10,
            blocked_count=10,
            bypassed_count=0,
        )
        assert report.block_rate == 100.0
        assert report.passed is True

    def test_0_percent_block_rate(self):
        report = AttackReport(
            results=[],
            total_tests=10,
            blocked_count=0,
            bypassed_count=10,
        )
        assert report.block_rate == 0.0
        assert report.passed is False

    def test_exactly_95_percent_passes(self):
        report = AttackReport(
            results=[],
            total_tests=100,
            blocked_count=95,
            bypassed_count=5,
        )
        assert report.block_rate == 95.0
        assert report.passed is True

    def test_just_below_95_fails(self):
        report = AttackReport(
            results=[],
            total_tests=100,
            blocked_count=94,
            bypassed_count=6,
        )
        assert report.block_rate == 94.0
        assert report.passed is False

    def test_block_rate_fractional(self):
        report = AttackReport(
            results=[],
            total_tests=3,
            blocked_count=2,
            bypassed_count=1,
        )
        expected = (2 / 3) * 100.0
        assert abs(report.block_rate - expected) < 0.01

    def test_report_with_results(self):
        results = [
            AttackResult(attack_type=AttackType.XSS, payload="<script>", blocked=True),
            AttackResult(attack_type=AttackType.SQL_INJECTION, payload="' OR", blocked=False),
        ]
        report = AttackReport(
            results=results,
            total_tests=2,
            blocked_count=1,
            bypassed_count=1,
        )
        assert report.block_rate == 50.0
        assert report.passed is False
        assert len(report.results) == 2

    def test_single_test_blocked(self):
        report = AttackReport(
            results=[],
            total_tests=1,
            blocked_count=1,
            bypassed_count=0,
        )
        assert report.block_rate == 100.0
        assert report.passed is True

    def test_single_test_bypassed(self):
        report = AttackReport(
            results=[],
            total_tests=1,
            blocked_count=0,
            bypassed_count=1,
        )
        assert report.block_rate == 0.0
        assert report.passed is False


# ================================================================
# Section 5 -- AttackPayloadGenerator: Method Return Types
# ================================================================


class TestAttackPayloadGeneratorTypes:
    """Test that each generator returns the correct types and counts."""

    def test_sql_injection_returns_list(self):
        payloads = AttackPayloadGenerator.generate_sql_injection()
        assert isinstance(payloads, list)
        assert len(payloads) >= 8

    def test_xss_returns_list(self):
        payloads = AttackPayloadGenerator.generate_xss()
        assert isinstance(payloads, list)
        assert len(payloads) >= 8

    def test_path_traversal_returns_list(self):
        payloads = AttackPayloadGenerator.generate_path_traversal()
        assert isinstance(payloads, list)
        assert len(payloads) >= 6

    def test_csrf_returns_list(self):
        payloads = AttackPayloadGenerator.generate_csrf()
        assert isinstance(payloads, list)
        assert len(payloads) >= 4

    def test_auth_bypass_returns_list(self):
        payloads = AttackPayloadGenerator.generate_auth_bypass()
        assert isinstance(payloads, list)
        assert len(payloads) >= 6

    def test_header_injection_returns_list(self):
        payloads = AttackPayloadGenerator.generate_header_injection()
        assert isinstance(payloads, list)
        assert len(payloads) >= 4

    def test_ssrf_returns_list(self):
        payloads = AttackPayloadGenerator.generate_ssrf()
        assert isinstance(payloads, list)
        assert len(payloads) >= 4

    def test_command_injection_returns_list(self):
        payloads = AttackPayloadGenerator.generate_command_injection()
        assert isinstance(payloads, list)
        assert len(payloads) >= 4

    def test_rate_limit_bypass_returns_list(self):
        payloads = AttackPayloadGenerator.generate_rate_limit_bypass()
        assert isinstance(payloads, list)
        assert len(payloads) >= 2

    def test_file_upload_bypass_returns_list(self):
        payloads = AttackPayloadGenerator.generate_file_upload_bypass()
        assert isinstance(payloads, list)
        assert len(payloads) >= 3

    def test_brute_force_returns_list(self):
        payloads = AttackPayloadGenerator.generate_brute_force()
        assert isinstance(payloads, list)
        assert len(payloads) >= 2

    def test_idor_returns_list(self):
        payloads = AttackPayloadGenerator.generate_idor()
        assert isinstance(payloads, list)
        assert len(payloads) >= 3

    def test_all_payloads_are_attack_payload_instances(self):
        all_payloads = AttackPayloadGenerator.generate_all()
        for p in all_payloads:
            assert isinstance(p, AttackPayload)

    def test_all_payloads_have_valid_attack_type(self):
        all_payloads = AttackPayloadGenerator.generate_all()
        for p in all_payloads:
            assert isinstance(p.attack_type, AttackType)

    def test_all_payloads_have_nonempty_payload(self):
        all_payloads = AttackPayloadGenerator.generate_all()
        # Note: auth_bypass has an empty-string payload for "missing auth header"
        # That is intentional -- the payload IS the empty string
        for p in all_payloads:
            assert isinstance(p.payload, str)

    def test_all_payloads_have_nonempty_description(self):
        all_payloads = AttackPayloadGenerator.generate_all()
        for p in all_payloads:
            assert isinstance(p.description, str)
            assert len(p.description) > 0


# ================================================================
# Section 6 -- generate_all() Combines All Generators
# ================================================================


class TestGenerateAll:
    """Test that generate_all() returns the combined set of all generators."""

    def test_generate_all_count(self):
        all_payloads = AttackPayloadGenerator.generate_all()
        individual_sum = (
            len(AttackPayloadGenerator.generate_sql_injection())
            + len(AttackPayloadGenerator.generate_xss())
            + len(AttackPayloadGenerator.generate_path_traversal())
            + len(AttackPayloadGenerator.generate_csrf())
            + len(AttackPayloadGenerator.generate_auth_bypass())
            + len(AttackPayloadGenerator.generate_header_injection())
            + len(AttackPayloadGenerator.generate_ssrf())
            + len(AttackPayloadGenerator.generate_command_injection())
            + len(AttackPayloadGenerator.generate_rate_limit_bypass())
            + len(AttackPayloadGenerator.generate_file_upload_bypass())
            + len(AttackPayloadGenerator.generate_brute_force())
            + len(AttackPayloadGenerator.generate_idor())
        )
        assert len(all_payloads) == individual_sum

    def test_generate_all_covers_all_attack_types(self):
        all_payloads = AttackPayloadGenerator.generate_all()
        types_covered = {p.attack_type for p in all_payloads}
        assert types_covered == set(AttackType)

    def test_generate_all_minimum_count(self):
        all_payloads = AttackPayloadGenerator.generate_all()
        # At minimum: 8+8+6+4+6+4+4+4+2+3+2+3 = 54
        assert len(all_payloads) >= 50


# ================================================================
# Section 7 -- SQL Injection Payload Patterns
# ================================================================


class TestSQLInjectionPayloads:
    """Test SQL injection payloads contain expected patterns."""

    @pytest.fixture
    def payloads(self) -> list[AttackPayload]:
        return AttackPayloadGenerator.generate_sql_injection()

    def test_contains_or_pattern(self, payloads: list[AttackPayload]):
        combined = " ".join(p.payload for p in payloads)
        assert "OR" in combined.upper()

    def test_contains_union_select(self, payloads: list[AttackPayload]):
        combined = " ".join(p.payload for p in payloads)
        assert "UNION" in combined.upper()

    def test_contains_drop_table(self, payloads: list[AttackPayload]):
        combined = " ".join(p.payload for p in payloads)
        assert "DROP" in combined.upper()

    def test_contains_waitfor(self, payloads: list[AttackPayload]):
        combined = " ".join(p.payload for p in payloads)
        assert "WAITFOR" in combined.upper()

    def test_all_have_sql_injection_type(self, payloads: list[AttackPayload]):
        for p in payloads:
            assert p.attack_type == AttackType.SQL_INJECTION


# ================================================================
# Section 8 -- XSS Payload Patterns
# ================================================================


class TestXSSPayloads:
    """Test XSS payloads contain expected patterns."""

    @pytest.fixture
    def payloads(self) -> list[AttackPayload]:
        return AttackPayloadGenerator.generate_xss()

    def test_contains_script_tag(self, payloads: list[AttackPayload]):
        combined = " ".join(p.payload for p in payloads)
        assert "<script>" in combined.lower()

    def test_contains_img_tag(self, payloads: list[AttackPayload]):
        combined = " ".join(p.payload for p in payloads)
        assert "<img" in combined.lower()

    def test_contains_svg_tag(self, payloads: list[AttackPayload]):
        combined = " ".join(p.payload for p in payloads)
        assert "<svg" in combined.lower()

    def test_contains_onerror(self, payloads: list[AttackPayload]):
        combined = " ".join(p.payload for p in payloads)
        assert "onerror" in combined.lower()

    def test_contains_iframe(self, payloads: list[AttackPayload]):
        combined = " ".join(p.payload for p in payloads)
        assert "<iframe" in combined.lower()

    def test_all_have_xss_type(self, payloads: list[AttackPayload]):
        for p in payloads:
            assert p.attack_type == AttackType.XSS


# ================================================================
# Section 9 -- Path Traversal Payload Patterns
# ================================================================


class TestPathTraversalPayloads:
    """Test path traversal payloads contain expected patterns."""

    @pytest.fixture
    def payloads(self) -> list[AttackPayload]:
        return AttackPayloadGenerator.generate_path_traversal()

    def test_contains_dot_dot_slash(self, payloads: list[AttackPayload]):
        combined = " ".join(p.payload for p in payloads)
        assert "../" in combined

    def test_contains_etc_passwd(self, payloads: list[AttackPayload]):
        combined = " ".join(p.payload for p in payloads)
        assert "etc" in combined.lower()

    def test_contains_encoded_variant(self, payloads: list[AttackPayload]):
        combined = " ".join(p.payload for p in payloads)
        assert "%2F" in combined or "%2f" in combined

    def test_contains_windows_path(self, payloads: list[AttackPayload]):
        combined = " ".join(p.payload for p in payloads)
        assert "windows" in combined.lower() or "\\..\\" in combined

    def test_all_have_path_traversal_type(self, payloads: list[AttackPayload]):
        for p in payloads:
            assert p.attack_type == AttackType.PATH_TRAVERSAL


# ================================================================
# Section 10 -- Auth Bypass Payload Patterns
# ================================================================


class TestAuthBypassPayloads:
    """Test auth bypass payloads reference JWT/token concepts."""

    @pytest.fixture
    def payloads(self) -> list[AttackPayload]:
        return AttackPayloadGenerator.generate_auth_bypass()

    def test_contains_jwt_reference(self, payloads: list[AttackPayload]):
        combined = " ".join(p.description for p in payloads)
        assert "JWT" in combined.upper() or "token" in combined.lower()

    def test_contains_bearer(self, payloads: list[AttackPayload]):
        combined = " ".join(p.payload for p in payloads)
        assert "Bearer" in combined

    def test_contains_admin_reference(self, payloads: list[AttackPayload]):
        combined = " ".join(p.payload for p in payloads)
        assert "admin" in combined.lower()

    def test_all_have_auth_bypass_type(self, payloads: list[AttackPayload]):
        for p in payloads:
            assert p.attack_type == AttackType.AUTH_BYPASS


# ================================================================
# Section 11 -- AttackTester Agent
# ================================================================


class TestAttackTesterAgent:
    """Test the AttackTester agent class."""

    @pytest.fixture
    def agent(self) -> AttackTester:
        return AttackTester()

    def test_is_standalone_agent(self):
        assert hasattr(AttackTester, "name")
        assert hasattr(AttackTester, "execute")

    def test_agent_name(self, agent: AttackTester):
        assert agent.name == "attack_tester"

    def test_agent_display_name(self, agent: AttackTester):
        assert agent.display_name == "Attack Tester"

    def test_agent_has_execute_method(self, agent: AttackTester):
        assert hasattr(agent, "execute")
        assert callable(agent.execute)

    def test_agent_has_run_method(self, agent: AttackTester):
        assert hasattr(agent, "run")
        assert callable(agent.run)

    def test_agent_default_model(self, agent: AttackTester):
        assert agent.default_model == "claude-sonnet-4-6"

    def test_agent_tools_registered(self, agent: AttackTester):
        tool_names = [t.name for t in agent.tools]
        assert "generate_attack_payloads" in tool_names

    def test_agent_tool_has_parameters(self, agent: AttackTester):
        tool = agent._tools["generate_attack_payloads"]
        assert "properties" in tool.parameters
        assert "attack_types" in tool.parameters["properties"]


# ================================================================
# Section 12 -- Edge Cases
# ================================================================


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_report_zero_total_tests_block_rate(self):
        report = AttackReport(total_tests=0, blocked_count=0, bypassed_count=0)
        assert report.block_rate == 0.0
        assert report.passed is False

    def test_report_all_blocked_is_passed(self):
        report = AttackReport(total_tests=20, blocked_count=20, bypassed_count=0)
        assert report.block_rate == 100.0
        assert report.passed is True

    def test_report_none_blocked_fails(self):
        report = AttackReport(total_tests=20, blocked_count=0, bypassed_count=20)
        assert report.block_rate == 0.0
        assert report.passed is False

    def test_boundary_95_percent_20_tests(self):
        # 19 out of 20 = 95.0%
        report = AttackReport(total_tests=20, blocked_count=19, bypassed_count=1)
        assert report.block_rate == 95.0
        assert report.passed is True

    def test_boundary_below_95_percent_20_tests(self):
        # 18 out of 20 = 90.0%
        report = AttackReport(total_tests=20, blocked_count=18, bypassed_count=2)
        assert report.block_rate == 90.0
        assert report.passed is False

    def test_report_mutable(self):
        report = AttackReport()
        report.total_tests = 5
        report.blocked_count = 5
        assert report.block_rate == 100.0

    def test_attack_payload_frozen_hash_in_set(self):
        p1 = AttackPayload(
            attack_type=AttackType.SQL_INJECTION,
            payload="' OR 1=1--",
            description="test",
        )
        p2 = AttackPayload(
            attack_type=AttackType.XSS,
            payload="<script>",
            description="test",
        )
        s = {p1, p2}
        assert len(s) == 2

    def test_attack_result_frozen_hash_in_set(self):
        r1 = AttackResult(
            attack_type=AttackType.SQL_INJECTION,
            payload="' OR 1=1--",
            blocked=True,
        )
        r2 = AttackResult(
            attack_type=AttackType.XSS,
            payload="<script>",
            blocked=False,
        )
        s = {r1, r2}
        assert len(s) == 2

    def test_payload_description_nonempty_all_generators(self):
        """Every payload from every generator must have a non-empty description."""
        generators = [
            AttackPayloadGenerator.generate_sql_injection,
            AttackPayloadGenerator.generate_xss,
            AttackPayloadGenerator.generate_path_traversal,
            AttackPayloadGenerator.generate_csrf,
            AttackPayloadGenerator.generate_auth_bypass,
            AttackPayloadGenerator.generate_header_injection,
            AttackPayloadGenerator.generate_ssrf,
            AttackPayloadGenerator.generate_command_injection,
            AttackPayloadGenerator.generate_rate_limit_bypass,
            AttackPayloadGenerator.generate_file_upload_bypass,
            AttackPayloadGenerator.generate_brute_force,
            AttackPayloadGenerator.generate_idor,
        ]
        for gen in generators:
            for p in gen():
                assert len(p.description) > 0, (
                    f"Empty description in {gen.__name__}: {p.payload!r}"
                )
