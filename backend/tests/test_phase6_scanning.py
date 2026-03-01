"""Phase 6 — Multi-Level Scanning: comprehensive tests for Gaps 218-222.

Covers:
- Level 1: Syntax scan (ast.parse for Python, bracket balance for TS/JS)
- Level 2: Logic scan (Navya's patterns, categories, report)
- Level 3: Security scan (Karan's patterns, categories, report)
- Level 4: Performance scan (Deepika's patterns, categories, report)
- Level 5: Compliance scan (GDPR, PCI-DSS, HIPAA + existing DPDP/OWASP/WCAG)
"""

from __future__ import annotations

import pytest

# ── Level 1 Imports ──────────────────────────────────────────────

from app.engine.code_quality import (
    CodeQualityEngine,
    QualityIssue,
    QualityReport,
    Severity,
    get_code_quality_engine,
)

# ── Level 5 Imports ──────────────────────────────────────────────

from app.engine.compliance_engine import (
    ComplianceEngine,
    ComplianceFinding,
    ComplianceFramework,
    ComplianceReport,
    ComplianceSeverity,
    get_compliance_engine,
)


# ────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────


@pytest.fixture
def quality_engine() -> CodeQualityEngine:
    return CodeQualityEngine()


@pytest.fixture
def compliance_engine() -> ComplianceEngine:
    return ComplianceEngine()


EMPTY_CONTRACT: dict = {"database": {}, "api": {}}


# ════════════════════════════════════════════════════════════════════
# LEVEL 1: Syntax Scanning (Gap 218)
# ════════════════════════════════════════════════════════════════════


class TestPythonSyntaxScan:
    """Test AST-based Python syntax validation."""

    def test_valid_python(self, quality_engine: CodeQualityEngine):
        files = {"app/main.py": "def hello():\n    return 'world'\n"}
        report = quality_engine.validate_project(files, EMPTY_CONTRACT)
        syntax_issues = [i for i in report.issues if i.category == "syntax_error"]
        assert len(syntax_issues) == 0

    def test_invalid_python_missing_colon(self, quality_engine: CodeQualityEngine):
        files = {"app/bad.py": "def hello()\n    return 'world'\n"}
        report = quality_engine.validate_project(files, EMPTY_CONTRACT)
        syntax_issues = [i for i in report.issues if i.category == "syntax_error"]
        assert len(syntax_issues) >= 1
        assert syntax_issues[0].severity == Severity.ERROR
        assert "syntax error" in syntax_issues[0].message.lower()

    def test_invalid_python_unmatched_paren(self, quality_engine: CodeQualityEngine):
        files = {"app/bad.py": "x = (1 + 2\n"}
        report = quality_engine.validate_project(files, EMPTY_CONTRACT)
        syntax_issues = [i for i in report.issues if i.category == "syntax_error"]
        assert len(syntax_issues) >= 1

    def test_invalid_python_indentation(self, quality_engine: CodeQualityEngine):
        files = {"app/bad.py": "def hello():\nreturn 'world'\n"}
        report = quality_engine.validate_project(files, EMPTY_CONTRACT)
        syntax_issues = [i for i in report.issues if i.category == "syntax_error"]
        assert len(syntax_issues) >= 1

    def test_syntax_error_line_number(self, quality_engine: CodeQualityEngine):
        files = {"app/bad.py": "x = 1\ny = 2\nz = (3 +\n"}
        report = quality_engine.validate_project(files, EMPTY_CONTRACT)
        syntax_issues = [i for i in report.issues if i.category == "syntax_error"]
        assert len(syntax_issues) >= 1
        # Line number should be provided
        assert syntax_issues[0].line is not None

    def test_multiple_python_files_only_bad_flagged(self, quality_engine: CodeQualityEngine):
        files = {
            "app/good.py": "x = 1\ny = 2\n",
            "app/bad.py": "def broken(\n",
            "app/also_good.py": "class Foo:\n    pass\n",
        }
        report = quality_engine.validate_project(files, EMPTY_CONTRACT)
        syntax_issues = [i for i in report.issues if i.category == "syntax_error"]
        assert len(syntax_issues) >= 1
        assert all(i.file_path == "app/bad.py" for i in syntax_issues)

    def test_non_python_files_skipped(self, quality_engine: CodeQualityEngine):
        files = {"app/main.ts": "this is not valid python!!!{{{"}
        report = quality_engine.validate_project(files, EMPTY_CONTRACT)
        syntax_issues = [i for i in report.issues if i.category == "syntax_error"]
        assert len(syntax_issues) == 0

    def test_empty_python_file_valid(self, quality_engine: CodeQualityEngine):
        files = {"app/empty.py": ""}
        report = quality_engine.validate_project(files, EMPTY_CONTRACT)
        syntax_issues = [i for i in report.issues if i.category == "syntax_error"]
        assert len(syntax_issues) == 0

    def test_syntax_error_marks_report_failed(self, quality_engine: CodeQualityEngine):
        files = {"app/bad.py": "def broken(\n"}
        report = quality_engine.validate_project(files, EMPTY_CONTRACT)
        assert report.passed is False


class TestBracketBalanceScan:
    """Test bracket/brace/paren balance checking for TS/JS files."""

    def test_balanced_typescript(self, quality_engine: CodeQualityEngine):
        files = {
            "app/main.ts": "function hello() {\n  return [1, 2, (3 + 4)];\n}\n"
        }
        report = quality_engine.validate_project(files, EMPTY_CONTRACT)
        bracket_issues = [i for i in report.issues if i.category == "bracket_balance"]
        assert len(bracket_issues) == 0

    def test_unclosed_brace(self, quality_engine: CodeQualityEngine):
        files = {"app/bad.ts": "function hello() {\n  return 1;\n"}
        report = quality_engine.validate_project(files, EMPTY_CONTRACT)
        bracket_issues = [i for i in report.issues if i.category == "bracket_balance"]
        assert len(bracket_issues) >= 1
        assert "Unclosed" in bracket_issues[0].message

    def test_unclosed_bracket(self, quality_engine: CodeQualityEngine):
        files = {"app/bad.tsx": "const arr = [1, 2, 3\n"}
        report = quality_engine.validate_project(files, EMPTY_CONTRACT)
        bracket_issues = [i for i in report.issues if i.category == "bracket_balance"]
        assert len(bracket_issues) >= 1

    def test_extra_closing_brace(self, quality_engine: CodeQualityEngine):
        files = {"app/bad.js": "function hello() {\n}\n}\n"}
        report = quality_engine.validate_project(files, EMPTY_CONTRACT)
        bracket_issues = [i for i in report.issues if i.category == "bracket_balance"]
        assert len(bracket_issues) >= 1
        assert "Unexpected closing" in bracket_issues[0].message

    def test_mismatched_brackets(self, quality_engine: CodeQualityEngine):
        files = {"app/bad.ts": "const x = (1 + 2];\n"}
        report = quality_engine.validate_project(files, EMPTY_CONTRACT)
        bracket_issues = [i for i in report.issues if i.category == "bracket_balance"]
        assert len(bracket_issues) >= 1
        assert "Mismatched" in bracket_issues[0].message

    def test_brackets_in_strings_ignored(self, quality_engine: CodeQualityEngine):
        files = {
            "app/ok.ts": 'const x = "hello { world [";\nconst y = 1;\n'
        }
        report = quality_engine.validate_project(files, EMPTY_CONTRACT)
        bracket_issues = [i for i in report.issues if i.category == "bracket_balance"]
        assert len(bracket_issues) == 0

    def test_brackets_in_template_literals_ignored(self, quality_engine: CodeQualityEngine):
        files = {
            "app/ok.tsx": "const x = `hello ${ world }`;\n"
        }
        report = quality_engine.validate_project(files, EMPTY_CONTRACT)
        bracket_issues = [i for i in report.issues if i.category == "bracket_balance"]
        # Template literal has balanced ${} and backtick strings
        assert len(bracket_issues) == 0

    def test_jsx_file_scanned(self, quality_engine: CodeQualityEngine):
        files = {"app/bad.jsx": "const x = {\n"}
        report = quality_engine.validate_project(files, EMPTY_CONTRACT)
        bracket_issues = [i for i in report.issues if i.category == "bracket_balance"]
        assert len(bracket_issues) >= 1

    def test_python_files_skipped_for_brackets(self, quality_engine: CodeQualityEngine):
        files = {"app/main.py": "x = {\n"}  # Unbalanced but it's Python
        report = quality_engine.validate_project(files, EMPTY_CONTRACT)
        bracket_issues = [i for i in report.issues if i.category == "bracket_balance"]
        assert len(bracket_issues) == 0

    def test_bracket_error_reports_line_number(self, quality_engine: CodeQualityEngine):
        files = {"app/bad.ts": "line1\nline2\nconst x = {\nline4\n"}
        report = quality_engine.validate_project(files, EMPTY_CONTRACT)
        bracket_issues = [i for i in report.issues if i.category == "bracket_balance"]
        assert len(bracket_issues) >= 1
        assert bracket_issues[0].line is not None
        assert bracket_issues[0].line == 3

    def test_deeply_nested_balanced(self, quality_engine: CodeQualityEngine):
        files = {
            "app/ok.ts": "const x = { a: [1, (2 + 3), { b: [4] }] };\n"
        }
        report = quality_engine.validate_project(files, EMPTY_CONTRACT)
        bracket_issues = [i for i in report.issues if i.category == "bracket_balance"]
        assert len(bracket_issues) == 0


# ════════════════════════════════════════════════════════════════════
# LEVEL 2: Logic Scan Patterns (Gap 219)
# ════════════════════════════════════════════════════════════════════


class TestLogicScanTypes:
    """Test Navya's logic scan types and report structures."""

    def test_logic_severity_values(self):
        from app.agents.navya import LogicSeverity
        assert LogicSeverity.ERROR.value == "error"
        assert LogicSeverity.WARNING.value == "warning"
        assert LogicSeverity.INFO.value == "info"

    def test_logic_category_values(self):
        from app.agents.navya import LogicCategory
        expected = {
            "dead_code", "null_safety", "type_mismatch",
            "missing_error_handling", "infinite_loop_risk",
            "off_by_one", "race_condition", "inconsistent_state",
            "contract_mismatch", "missing_return",
            "unreachable_code", "unused_import",
        }
        actual = {m.value for m in LogicCategory}
        assert expected == actual

    def test_logic_finding_frozen(self):
        from app.agents.navya import LogicFinding, LogicSeverity, LogicCategory
        finding = LogicFinding(
            severity=LogicSeverity.ERROR,
            category=LogicCategory.DEAD_CODE,
            file_path="test.py",
            line=10,
            title="Dead code",
            description="Unreachable after return",
        )
        assert finding.severity == LogicSeverity.ERROR
        with pytest.raises(AttributeError):
            finding.severity = LogicSeverity.WARNING  # type: ignore

    def test_logic_report_error_count(self):
        from app.agents.navya import LogicFinding, LogicReport, LogicSeverity, LogicCategory
        report = LogicReport()
        report.findings.append(LogicFinding(
            severity=LogicSeverity.ERROR,
            category=LogicCategory.DEAD_CODE,
            file_path="test.py", line=1,
            title="T", description="D",
        ))
        report.findings.append(LogicFinding(
            severity=LogicSeverity.WARNING,
            category=LogicCategory.NULL_SAFETY,
            file_path="test.py", line=2,
            title="T", description="D",
        ))
        assert report.error_count == 1
        assert report.warning_count == 1

    def test_navya_agent_registered(self):
        from app.agents.navya import Navya
        assert hasattr(Navya, "name")
        assert hasattr(Navya, "execute")

    def test_navya_has_execute_method(self):
        from app.agents.navya import Navya
        assert hasattr(Navya, "execute")
        assert callable(getattr(Navya, "execute"))


# ════════════════════════════════════════════════════════════════════
# LEVEL 3: Security Scan Patterns (Gap 220)
# ════════════════════════════════════════════════════════════════════


class TestSecurityScanTypes:
    """Test Karan's security scan types and patterns."""

    def test_finding_severity_cvss_aligned(self):
        from app.agents.karan import FindingSeverity
        assert FindingSeverity.CRITICAL.value == "critical"
        assert FindingSeverity.HIGH.value == "high"
        assert FindingSeverity.MEDIUM.value == "medium"
        assert FindingSeverity.LOW.value == "low"
        assert FindingSeverity.INFO.value == "info"

    def test_finding_categories(self):
        from app.agents.karan import FindingCategory
        expected = {
            "sql_injection", "xss", "csrf", "auth_bypass",
            "insecure_crypto", "hardcoded_secret", "path_traversal",
            "command_injection", "insecure_deserialization", "ssrf",
            "broken_access_control", "sensitive_data_exposure",
            "dependency_vulnerability", "misconfiguration",
            "dpdp_violation", "accessibility",
        }
        actual = {m.value for m in FindingCategory}
        assert expected == actual

    def test_security_finding_frozen(self):
        from app.agents.karan import SecurityFinding, FindingSeverity, FindingCategory
        finding = SecurityFinding(
            severity=FindingSeverity.CRITICAL,
            category=FindingCategory.SQL_INJECTION,
            file_path="routes.py",
            line=42,
            title="SQL injection",
            description="f-string SQL",
            fix_hint="Use parameterized queries",
        )
        assert finding.severity == FindingSeverity.CRITICAL
        with pytest.raises(AttributeError):
            finding.title = "changed"  # type: ignore

    def test_karan_agent_registered(self):
        from app.agents.karan import Karan
        assert hasattr(Karan, "name")
        assert hasattr(Karan, "execute")

    def test_karan_default_model_is_sonnet(self):
        from app.agents.karan import Karan
        agent = Karan()
        assert agent.default_model == "claude-sonnet-4-6"


# ════════════════════════════════════════════════════════════════════
# LEVEL 4: Performance Scan Patterns (Gap 221)
# ════════════════════════════════════════════════════════════════════


class TestPerformanceScanTypes:
    """Test Deepika's performance scan types and patterns."""

    def test_perf_severity_values(self):
        from app.agents.deepika import PerfSeverity
        assert PerfSeverity.CRITICAL.value == "critical"
        assert PerfSeverity.HIGH.value == "high"
        assert PerfSeverity.MEDIUM.value == "medium"
        assert PerfSeverity.LOW.value == "low"

    def test_perf_categories(self):
        from app.agents.deepika import PerfCategory
        expected = {
            "n_plus_one", "missing_index", "unbounded_query",
            "memory_leak", "sync_in_async", "missing_await",
            "large_import", "missing_pagination", "missing_cache",
            "inefficient_algorithm", "bundle_size", "missing_lazy_load",
        }
        actual = {m.value for m in PerfCategory}
        assert expected == actual

    def test_perf_finding_frozen(self):
        from app.agents.deepika import PerfFinding, PerfSeverity, PerfCategory
        finding = PerfFinding(
            severity=PerfSeverity.HIGH,
            category=PerfCategory.N_PLUS_ONE,
            file_path="services.py",
            line=50,
            title="N+1 query",
            description="Relationship accessed in loop",
            impact="Exponential DB queries",
            suggestion="Use joinedload() or selectinload()",
        )
        assert finding.impact == "Exponential DB queries"
        with pytest.raises(AttributeError):
            finding.impact = "changed"  # type: ignore

    def test_deepika_agent_registered(self):
        from app.agents.deepika import Deepika
        assert hasattr(Deepika, "name")
        assert hasattr(Deepika, "execute")


# ════════════════════════════════════════════════════════════════════
# LEVEL 5: Compliance Scan (Gap 222) — Framework Enum
# ════════════════════════════════════════════════════════════════════


class TestComplianceFrameworks:
    """Test compliance framework enum has all 6 frameworks."""

    def test_framework_count(self):
        assert len(ComplianceFramework) == 6

    @pytest.mark.parametrize("name,value", [
        ("DPDP_2023", "dpdp_2023"),
        ("OWASP_TOP10_2021", "owasp_top10"),
        ("WCAG_22", "wcag_2.2"),
        ("GDPR", "gdpr"),
        ("PCI_DSS", "pci_dss"),
        ("HIPAA", "hipaa"),
    ])
    def test_framework_values(self, name: str, value: str):
        assert ComplianceFramework[name].value == value

    def test_severity_count(self):
        assert len(ComplianceSeverity) == 5


# ════════════════════════════════════════════════════════════════════
# LEVEL 5: GDPR Compliance Tests
# ════════════════════════════════════════════════════════════════════


class TestGDPRCompliance:
    """Test GDPR pattern detection."""

    def test_gdpr_pii_in_logs(self, compliance_engine: ComplianceEngine):
        files = {"app/service.py": "print(f'User email: {user.email}')\n"}
        report = compliance_engine.check_gdpr(files)
        gdpr_findings = [f for f in report.findings if f.framework == ComplianceFramework.GDPR]
        assert any("GDPR-01" == f.rule_id for f in gdpr_findings)

    def test_gdpr_indefinite_retention(self, compliance_engine: ComplianceEngine):
        files = {"app/config.py": "data_retention = 'indefinite'\n"}
        report = compliance_engine.check_gdpr(files)
        gdpr_findings = [f for f in report.findings if f.rule_id == "GDPR-03"]
        assert len(gdpr_findings) >= 1

    def test_gdpr_special_category_data(self, compliance_engine: ComplianceEngine):
        files = {"app/model.py": "health_status = Column(String)\n"}
        report = compliance_engine.check_gdpr(files)
        gdpr_findings = [f for f in report.findings if f.rule_id == "GDPR-05"]
        assert len(gdpr_findings) >= 1
        assert gdpr_findings[0].severity == ComplianceSeverity.CRITICAL

    def test_gdpr_clean_code_no_findings(self, compliance_engine: ComplianceEngine):
        files = {"app/service.py": "def get_user(user_id: str):\n    return db.get(user_id)\n"}
        report = compliance_engine.check_gdpr(files)
        gdpr_findings = [f for f in report.findings if f.framework == ComplianceFramework.GDPR]
        assert len(gdpr_findings) == 0

    def test_gdpr_non_code_files_skipped(self, compliance_engine: ComplianceEngine):
        files = {"readme.md": "This stores email and phone in logs forever\n"}
        report = compliance_engine.check_gdpr(files)
        assert len(report.findings) == 0

    def test_gdpr_framework_in_report(self, compliance_engine: ComplianceEngine):
        files = {"app/s.py": "x = 1\n"}
        report = compliance_engine.check_gdpr(files)
        assert ComplianceFramework.GDPR in report.frameworks_checked


# ════════════════════════════════════════════════════════════════════
# LEVEL 5: PCI-DSS Compliance Tests
# ════════════════════════════════════════════════════════════════════


class TestPCIDSSCompliance:
    """Test PCI-DSS pattern detection."""

    def test_pci_card_number_stored(self, compliance_engine: ComplianceEngine):
        files = {"app/payment.py": "card_number = request.json['card']\n"}
        report = compliance_engine.check_pci_dss(files)
        pci_findings = [f for f in report.findings if f.rule_id == "PCI-01"]
        assert len(pci_findings) >= 1
        assert pci_findings[0].severity == ComplianceSeverity.CRITICAL

    def test_pci_cvv_stored(self, compliance_engine: ComplianceEngine):
        files = {"app/payment.py": "cvv = data['cvv']\n"}
        report = compliance_engine.check_pci_dss(files)
        pci_findings = [f for f in report.findings if f.rule_id == "PCI-02"]
        assert len(pci_findings) >= 1

    def test_pci_card_in_logs(self, compliance_engine: ComplianceEngine):
        files = {"app/payment.py": "print(f'Card: {card_number}')\n"}
        report = compliance_engine.check_pci_dss(files)
        pci_findings = [f for f in report.findings if f.rule_id == "PCI-03"]
        assert len(pci_findings) >= 1

    def test_pci_non_tls(self, compliance_engine: ComplianceEngine):
        files = {"app/api.py": "url = 'http://payment-gateway.com/api'\n"}
        report = compliance_engine.check_pci_dss(files)
        pci_findings = [f for f in report.findings if f.rule_id == "PCI-04"]
        assert len(pci_findings) >= 1

    def test_pci_localhost_http_allowed(self, compliance_engine: ComplianceEngine):
        files = {"app/api.py": "url = 'http://localhost:8000/api'\n"}
        report = compliance_engine.check_pci_dss(files)
        pci_findings = [f for f in report.findings if f.rule_id == "PCI-04"]
        assert len(pci_findings) == 0

    def test_pci_hardcoded_payment_key(self, compliance_engine: ComplianceEngine):
        files = {"app/config.py": "stripe_key = 'sk_live_abc123'\n"}
        report = compliance_engine.check_pci_dss(files)
        pci_findings = [f for f in report.findings if f.rule_id == "PCI-05"]
        assert len(pci_findings) >= 1

    def test_pci_clean_code_passes(self, compliance_engine: ComplianceEngine):
        files = {"app/payment.py": "def process_payment(token: str):\n    return stripe.charge(token)\n"}
        report = compliance_engine.check_pci_dss(files)
        assert report.passed is True

    def test_pci_env_files_scanned(self, compliance_engine: ComplianceEngine):
        files = {".env": "stripe_key = 'sk_live_xyz'\n"}
        report = compliance_engine.check_pci_dss(files)
        pci_findings = [f for f in report.findings if f.rule_id == "PCI-05"]
        assert len(pci_findings) >= 1


# ════════════════════════════════════════════════════════════════════
# LEVEL 5: HIPAA Compliance Tests
# ════════════════════════════════════════════════════════════════════


class TestHIPAACompliance:
    """Test HIPAA pattern detection."""

    def test_hipaa_phi_in_logs(self, compliance_engine: ComplianceEngine):
        files = {"app/service.py": "print(f'Patient diagnosis: {patient.diagnosis}')\n"}
        report = compliance_engine.check_hipaa(files)
        hipaa_findings = [f for f in report.findings if f.rule_id == "HIPAA-01"]
        assert len(hipaa_findings) >= 1
        assert hipaa_findings[0].severity == ComplianceSeverity.CRITICAL

    def test_hipaa_medical_record_field(self, compliance_engine: ComplianceEngine):
        files = {"app/model.py": "medical_record = Column(Text)\n"}
        report = compliance_engine.check_hipaa(files)
        hipaa_findings = [f for f in report.findings if f.rule_id == "HIPAA-02"]
        assert len(hipaa_findings) >= 1

    def test_hipaa_ssn_stored(self, compliance_engine: ComplianceEngine):
        files = {"app/model.py": "social_security = Column(String)\n"}
        report = compliance_engine.check_hipaa(files)
        hipaa_findings = [f for f in report.findings if f.rule_id == "HIPAA-05"]
        assert len(hipaa_findings) >= 1

    def test_hipaa_clean_code(self, compliance_engine: ComplianceEngine):
        files = {"app/service.py": "def get_appointment(apt_id: str):\n    return db.get(apt_id)\n"}
        report = compliance_engine.check_hipaa(files)
        hipaa_findings = [f for f in report.findings if f.framework == ComplianceFramework.HIPAA]
        assert len(hipaa_findings) == 0

    def test_hipaa_non_code_skipped(self, compliance_engine: ComplianceEngine):
        files = {"readme.md": "patient diagnosis logged in print()\n"}
        report = compliance_engine.check_hipaa(files)
        assert len(report.findings) == 0


# ════════════════════════════════════════════════════════════════════
# LEVEL 5: check_all() with all frameworks
# ════════════════════════════════════════════════════════════════════


class TestComplianceCheckAll:
    """Test check_all() runs all 6 frameworks."""

    def test_check_all_runs_all_frameworks(self, compliance_engine: ComplianceEngine):
        files = {"app/s.py": "x = 1\n"}
        report = compliance_engine.check_all(files)
        assert len(report.frameworks_checked) == 6
        assert set(report.frameworks_checked) == set(ComplianceFramework)

    def test_check_all_finds_cross_framework_issues(self, compliance_engine: ComplianceEngine):
        files = {
            "app/service.py": (
                "# PII logging\n"
                "print(f'email: {user.email}')\n"
                "# Card data\n"
                "card_number = form.card\n"
                "# Health data\n"
                "medical_record = db.get_record()\n"
                "# Special category\n"
                "religion = 'buddhist'\n"
            ),
        }
        report = compliance_engine.check_all(files)
        frameworks_found = {f.framework for f in report.findings}
        # Should find DPDP, GDPR, PCI-DSS, HIPAA issues
        assert ComplianceFramework.PCI_DSS in frameworks_found
        assert ComplianceFramework.HIPAA in frameworks_found

    def test_check_all_specific_frameworks(self, compliance_engine: ComplianceEngine):
        files = {"app/s.py": "x = 1\n"}
        report = compliance_engine.check_all(
            files,
            frameworks=[ComplianceFramework.GDPR, ComplianceFramework.PCI_DSS],
        )
        assert len(report.frameworks_checked) == 2
        assert ComplianceFramework.GDPR in report.frameworks_checked
        assert ComplianceFramework.PCI_DSS in report.frameworks_checked

    def test_check_all_clean_code_passes(self, compliance_engine: ComplianceEngine):
        files = {"app/clean.py": "def hello():\n    return 'world'\n"}
        report = compliance_engine.check_all(files)
        assert report.passed is True
        assert report.blocking_count == 0

    def test_report_to_dict_serializable(self, compliance_engine: ComplianceEngine):
        import json
        files = {"app/s.py": "card_number = form.card\n"}
        report = compliance_engine.check_all(files)
        d = report.to_dict()
        json_str = json.dumps(d)
        assert isinstance(json_str, str)
        assert "frameworks_checked" in d

    def test_report_files_scanned_count(self, compliance_engine: ComplianceEngine):
        files = {"a.py": "x=1\n", "b.ts": "y=2\n", "c.tsx": "z=3\n"}
        report = compliance_engine.check_all(files)
        assert report.files_scanned == 3


# ════════════════════════════════════════════════════════════════════
# LEVEL 5: Existing DPDP/OWASP/WCAG still work
# ════════════════════════════════════════════════════════════════════


class TestExistingComplianceFrameworks:
    """Ensure DPDP, OWASP, WCAG still work after adding new frameworks."""

    def test_dpdp_aadhaar(self, compliance_engine: ComplianceEngine):
        files = {"app/model.py": "aadhaar = Column(String)\n"}
        report = compliance_engine.check_dpdp(files)
        assert any(f.rule_id == "DPDP-02" for f in report.findings)

    def test_owasp_hardcoded_secret(self, compliance_engine: ComplianceEngine):
        files = {"app/config.py": "password = 'admin123'\n"}
        report = compliance_engine.check_owasp(files)
        assert any(f.rule_id == "A07:2021" for f in report.findings)

    def test_wcag_missing_alt(self, compliance_engine: ComplianceEngine):
        files = {"app/page.tsx": '<img src="logo.png">\n'}
        report = compliance_engine.check_wcag(files)
        assert any(f.rule_id == "WCAG-1.1.1" for f in report.findings)

    def test_dpdp_shortcut_method(self, compliance_engine: ComplianceEngine):
        files = {"app/s.py": "x = 1\n"}
        report = compliance_engine.check_dpdp(files)
        assert ComplianceFramework.DPDP_2023 in report.frameworks_checked

    def test_owasp_shortcut_method(self, compliance_engine: ComplianceEngine):
        files = {"app/s.py": "x = 1\n"}
        report = compliance_engine.check_owasp(files)
        assert ComplianceFramework.OWASP_TOP10_2021 in report.frameworks_checked

    def test_wcag_shortcut_method(self, compliance_engine: ComplianceEngine):
        files = {"app/s.py": "x = 1\n"}
        report = compliance_engine.check_wcag(files)
        assert ComplianceFramework.WCAG_22 in report.frameworks_checked


# ════════════════════════════════════════════════════════════════════
# Singleton Factories
# ════════════════════════════════════════════════════════════════════


class TestSingletonFactories:
    """Test singleton patterns for engines."""

    def test_code_quality_engine_singleton(self):
        e1 = get_code_quality_engine()
        e2 = get_code_quality_engine()
        assert e1 is e2
        assert isinstance(e1, CodeQualityEngine)

    def test_compliance_engine_singleton(self):
        e1 = get_compliance_engine()
        e2 = get_compliance_engine()
        assert e1 is e2
        assert isinstance(e1, ComplianceEngine)

    def test_compliance_engine_has_new_methods(self):
        engine = get_compliance_engine()
        assert hasattr(engine, "check_gdpr")
        assert hasattr(engine, "check_pci_dss")
        assert hasattr(engine, "check_hipaa")
        assert callable(engine.check_gdpr)
        assert callable(engine.check_pci_dss)
        assert callable(engine.check_hipaa)


# ════════════════════════════════════════════════════════════════════
# Edge Cases
# ════════════════════════════════════════════════════════════════════


class TestScanningEdgeCases:
    """Edge cases across all scanning levels."""

    def test_empty_files_dict(self, quality_engine: CodeQualityEngine):
        report = quality_engine.validate_project({}, EMPTY_CONTRACT)
        assert report.passed is True
        assert report.files_checked == 0

    def test_compliance_empty_files(self, compliance_engine: ComplianceEngine):
        report = compliance_engine.check_all({})
        assert report.passed is True
        assert report.files_scanned == 0

    def test_unicode_content_python(self, quality_engine: CodeQualityEngine):
        files = {"app/i18n.py": "message = '日本語テスト'\nprint(message)\n"}
        report = quality_engine.validate_project(files, EMPTY_CONTRACT)
        syntax_issues = [i for i in report.issues if i.category == "syntax_error"]
        assert len(syntax_issues) == 0

    def test_unicode_content_compliance(self, compliance_engine: ComplianceEngine):
        files = {"app/i18n.py": "# 日本語コメント\nprint('hello')\n"}
        report = compliance_engine.check_all(files)
        # Should not crash
        assert isinstance(report, ComplianceReport)

    def test_very_large_file_python(self, quality_engine: CodeQualityEngine):
        large_file = "x = 1\n" * 10000
        files = {"app/big.py": large_file}
        report = quality_engine.validate_project(files, EMPTY_CONTRACT)
        syntax_issues = [i for i in report.issues if i.category == "syntax_error"]
        assert len(syntax_issues) == 0

    def test_multiple_syntax_errors(self, quality_engine: CodeQualityEngine):
        # Only one syntax error reported per file (ast.parse fails at first error)
        files = {"app/bad.py": "def a(\ndef b(\n"}
        report = quality_engine.validate_project(files, EMPTY_CONTRACT)
        syntax_issues = [i for i in report.issues if i.category == "syntax_error"]
        assert len(syntax_issues) >= 1

    def test_compliance_finding_line_numbers(self, compliance_engine: ComplianceEngine):
        files = {
            "app/payment.py": (
                "# Line 1\n"
                "# Line 2\n"
                "card_number = form.card\n"  # Line 3
            ),
        }
        report = compliance_engine.check_pci_dss(files)
        pci_findings = [f for f in report.findings if f.rule_id == "PCI-01"]
        if pci_findings:
            assert pci_findings[0].line == 3

    def test_compliance_report_blocking_count(self, compliance_engine: ComplianceEngine):
        files = {
            "app/bad.py": (
                "card_number = form.card\n"  # PCI-01: CRITICAL
                "cvv = data['cvv']\n"  # PCI-02: CRITICAL
            ),
        }
        report = compliance_engine.check_pci_dss(files)
        assert report.blocking_count >= 2
        assert report.passed is False
