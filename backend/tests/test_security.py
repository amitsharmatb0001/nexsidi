"""Tests for Security Guardian: secret detection, header scanning, vulnerability tracking."""

from __future__ import annotations

import pytest

from app.agents.security_guardian import (
    SecurityGuardian,
    VulnerabilitySeverity,
    ScanType,
    UpdateRisk,
    Vulnerability,
    DependencyUpdate,
    SecurityScanReport,
    SECRET_PATTERNS,
    REQUIRED_SECURITY_HEADERS,
)


class TestSecretDetection:
    """Test secret/credential leak detection patterns."""

    def setup_method(self):
        self.sg = SecurityGuardian()

    def test_detect_aws_access_key(self):
        code = 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"'
        vulns = self.sg.scan_secrets(code, "config.py")
        assert len(vulns) >= 1
        assert vulns[0].severity == VulnerabilitySeverity.CRITICAL

    def test_detect_github_token(self):
        code = 'token = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk"'
        vulns = self.sg.scan_secrets(code, "auth.py")
        assert len(vulns) >= 1

    def test_detect_jwt_token(self):
        code = 'jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc123def456"'
        vulns = self.sg.scan_secrets(code, "test.py")
        assert len(vulns) >= 1

    def test_detect_database_url(self):
        code = 'DB = "postgres://admin:secret123@db.example.com/mydb"'
        vulns = self.sg.scan_secrets(code, "settings.py")
        assert len(vulns) >= 1

    def test_detect_private_key(self):
        code = '-----BEGIN RSA PRIVATE KEY-----\nMIIE...'
        vulns = self.sg.scan_secrets(code, "key.pem")
        assert len(vulns) >= 1

    def test_clean_code_no_secrets(self):
        code = """
def get_user(user_id: str) -> User:
    return db.query(User).filter(User.id == user_id).first()
"""
        vulns = self.sg.scan_secrets(code, "users.py")
        assert len(vulns) == 0

    def test_env_reference_not_flagged(self):
        code = 'api_key = os.getenv("API_KEY")'
        vulns = self.sg.scan_secrets(code, "config.py")
        assert len(vulns) == 0

    def test_secrets_are_redacted_in_report(self):
        code = 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"'
        vulns = self.sg.scan_secrets(code, "config.py")
        assert len(vulns) >= 1
        # The actual key should be redacted
        assert "AKIAIOSFODNN7EXAMPLE" not in vulns[0].description


class TestSecurityHeaderScan:
    """Test missing security header detection."""

    def setup_method(self):
        self.sg = SecurityGuardian()

    def test_missing_headers_detected(self):
        files = {"app/main.py": "app = FastAPI()"}
        vulns = self.sg.scan_security_headers(files)
        assert len(vulns) >= 5  # Most headers should be missing

    def test_all_required_headers_checked(self):
        assert len(REQUIRED_SECURITY_HEADERS) >= 7
        assert "Strict-Transport-Security" in REQUIRED_SECURITY_HEADERS
        assert "X-Content-Type-Options" in REQUIRED_SECURITY_HEADERS
        assert "Content-Security-Policy" in REQUIRED_SECURITY_HEADERS

    def test_no_relevant_files_no_findings(self):
        files = {"utils/math.py": "def add(a, b): return a + b"}
        vulns = self.sg.scan_security_headers(files)
        assert len(vulns) == 0


class TestUpdateRiskClassification:
    """Test dependency update risk classification."""

    def setup_method(self):
        self.sg = SecurityGuardian()

    def test_major_update(self):
        assert self.sg.classify_update_risk("1.0.0", "2.0.0") == UpdateRisk.MAJOR

    def test_minor_update(self):
        assert self.sg.classify_update_risk("1.0.0", "1.1.0") == UpdateRisk.MINOR

    def test_patch_update(self):
        assert self.sg.classify_update_risk("1.0.0", "1.0.1") == UpdateRisk.PATCH

    def test_same_version(self):
        assert self.sg.classify_update_risk("1.0.0", "1.0.0") == UpdateRisk.PATCH


class TestFullSecurityScan:
    """Test full security scan integration."""

    def test_full_scan_returns_report(self):
        sg = SecurityGuardian()
        report = sg.full_scan({"main.py": "app = FastAPI()"})
        assert isinstance(report, SecurityScanReport)
        assert report.scanned_at

    def test_scan_with_secrets_has_findings(self):
        sg = SecurityGuardian()
        report = sg.full_scan({
            "config.py": 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"',
            "app/main.py": "app = FastAPI()",
        })
        assert report.total_findings > 0
        assert report.has_blocking  # AWS key is CRITICAL

    def test_report_to_dict(self):
        sg = SecurityGuardian()
        report = sg.full_scan({"main.py": "print('hello')"})
        d = report.to_dict()
        assert "total_findings" in d
        assert "vulnerabilities" in d
        assert "pending_updates" in d
        assert "has_blocking" in d

    def test_vulnerability_to_dict(self):
        v = Vulnerability(
            scan_type=ScanType.DEPENDENCY,
            severity=VulnerabilitySeverity.HIGH,
            title="CVE-2024-1234",
            description="Test",
            cve_id="CVE-2024-1234",
        )
        d = v.to_dict()
        assert d["cve_id"] == "CVE-2024-1234"
        assert d["severity"] == "high"

    def test_dependency_update_to_dict(self):
        u = DependencyUpdate(
            package_name="fastapi",
            current_version="0.100.0",
            latest_version="0.115.0",
            risk=UpdateRisk.MINOR,
            has_security_fix=True,
        )
        d = u.to_dict()
        assert d["package_name"] == "fastapi"
        assert d["has_security_fix"] is True
