"""Tests for engines: compliance, code_quality, template, execution, delivery."""

from __future__ import annotations

import pytest


class TestComplianceEngine:
    """Test the DPDP/OWASP/WCAG compliance engine."""

    def test_frameworks(self):
        from app.engine.compliance_engine import ComplianceFramework
        assert len(ComplianceFramework) == 3

    def test_dpdp_rules_exist(self):
        from app.engine.compliance_engine import _DPDP_RULES
        assert len(_DPDP_RULES) == 5

    def test_owasp_rules_exist(self):
        from app.engine.compliance_engine import _OWASP_RULES
        assert len(_OWASP_RULES) == 10

    def test_wcag_rules_exist(self):
        from app.engine.compliance_engine import _WCAG_RULES
        assert len(_WCAG_RULES) == 8

    def test_check_dpdp_clean_code(self):
        from app.engine.compliance_engine import get_compliance_engine
        engine = get_compliance_engine()
        report = engine.check_dpdp({"clean.py": "def get_user(): return db.query()"})
        assert len(report.findings) == 0

    def test_check_dpdp_aadhaar_detection(self):
        from app.engine.compliance_engine import get_compliance_engine
        engine = get_compliance_engine()
        report = engine.check_dpdp({"app.py": 'aadhaar = request.form.get("id")'})
        assert any("DPDP-02" in f.rule_id for f in report.findings)

    def test_check_owasp_sql_injection(self):
        from app.engine.compliance_engine import get_compliance_engine
        engine = get_compliance_engine()
        report = engine.check_owasp({"db.py": 'cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")'})
        assert len(report.findings) >= 1

    def test_check_all(self):
        from app.engine.compliance_engine import get_compliance_engine, ComplianceReport
        engine = get_compliance_engine()
        files = {"app.py": "def safe(): pass", "index.html": "<html><body>Hello</body></html>"}
        report = engine.check_all(files)
        assert isinstance(report, ComplianceReport)
        assert hasattr(report, "blocking_count")

    def test_compliance_report_to_dict(self):
        from app.engine.compliance_engine import ComplianceReport, ComplianceFramework
        report = ComplianceReport(findings=[], frameworks_checked=[ComplianceFramework.DPDP_2023])
        d = report.to_dict()
        assert "blocking" in d
        assert "findings" in d

    def test_singleton(self):
        from app.engine.compliance_engine import get_compliance_engine
        e1 = get_compliance_engine()
        e2 = get_compliance_engine()
        assert e1 is e2


class TestCodeQualityEngine:
    """Test the code quality engine."""

    def test_import_succeeds(self):
        from app.engine.code_quality import CodeQualityEngine
        engine = CodeQualityEngine()
        assert engine is not None

    def test_validate_project(self):
        from app.engine.code_quality import CodeQualityEngine, QualityReport
        engine = CodeQualityEngine()
        files = {
            "app/main.py": "from fastapi import FastAPI\napp = FastAPI()",
            "app/models.py": "from sqlalchemy import Column",
        }
        contract = {"tech_stack": {"backend": "FastAPI"}}
        result = engine.validate_project(files, contract)
        assert isinstance(result, QualityReport)


class TestTemplateEngine:
    """Test the template engine."""

    def test_import_succeeds(self):
        from app.engine.template_engine import TemplateEngine
        engine = TemplateEngine()
        assert engine is not None

    def test_has_backend_templates(self):
        from app.engine.template_engine import TemplateEngine
        engine = TemplateEngine()
        templates = engine.list_templates("backend")
        assert isinstance(templates, (list, dict))

    def test_has_frontend_templates(self):
        from app.engine.template_engine import TemplateEngine
        engine = TemplateEngine()
        templates = engine.list_templates("frontend")
        assert isinstance(templates, (list, dict))


class TestDeliveryEngine:
    """Test the delivery package builder."""

    def test_import_succeeds(self):
        from app.engine.delivery import DeliveryEngine
        engine = DeliveryEngine()
        assert engine is not None

    def test_singleton(self):
        from app.engine.delivery import get_delivery_engine
        e1 = get_delivery_engine()
        e2 = get_delivery_engine()
        assert e1 is e2
