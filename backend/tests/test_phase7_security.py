"""Phase 7 — Generated App Security: tests for Gaps 223-242.

Covers:
- SecurityTemplate dataclass and registry (security_templates.py)
- SecurityChecklistVerifier (security_checklist.py)
- GDPR/PCI/HIPAA patterns already tested in Phase 6 compliance tests
- Per-framework template coverage (FastAPI, Express, Django)
- Checklist verification against generated code patterns
"""

from __future__ import annotations

import pytest


# ── Security Templates imports ───────────────────────────────────

from app.engine.security_templates import (
    SECURITY_CHECKLIST,
    VALID_CATEGORIES,
    SecurityTemplate,
    get_security_checklist,
    get_security_template_registry,
    get_security_templates,
    get_template_by_category,
    list_supported_frameworks,
)

# ── Security Checklist imports ───────────────────────────────────

from app.engine.security_checklist import (
    ChecklistItem,
    ChecklistResult,
    SecurityChecklistReport,
    SecurityChecklistVerifier,
    get_security_checklist_verifier,
)


# ════════════════════════════════════════════════════════════════════
# Section 1 — SecurityTemplate Dataclass
# ════════════════════════════════════════════════════════════════════


class TestSecurityTemplateDataclass:
    """Test the SecurityTemplate frozen dataclass."""

    def test_create_valid_template(self):
        t = SecurityTemplate(
            name="Test CORS",
            category="cors",
            framework="fastapi",
            code="# cors config",
            description="CORS configuration",
        )
        assert t.name == "Test CORS"
        assert t.category == "cors"
        assert t.framework == "fastapi"

    def test_frozen_cannot_modify(self):
        t = SecurityTemplate(
            name="T", category="cors", framework="f",
            code="c", description="d",
        )
        with pytest.raises(AttributeError):
            t.name = "changed"  # type: ignore

    def test_invalid_category_raises(self):
        with pytest.raises(ValueError, match="Invalid category"):
            SecurityTemplate(
                name="T", category="bogus_category", framework="f",
                code="c", description="d",
            )

    @pytest.mark.parametrize("cat", sorted(VALID_CATEGORIES))
    def test_all_valid_categories_accepted(self, cat: str):
        t = SecurityTemplate(
            name="T", category=cat, framework="f",
            code="c", description="d",
        )
        assert t.category == cat

    def test_valid_categories_count(self):
        assert len(VALID_CATEGORIES) == 12


# ════════════════════════════════════════════════════════════════════
# Section 2 — Security Templates Registry
# ════════════════════════════════════════════════════════════════════


class TestSecurityTemplatesRegistry:
    """Test the template registry and helper functions."""

    def test_list_supported_frameworks(self):
        frameworks = list_supported_frameworks()
        assert isinstance(frameworks, list)
        assert len(frameworks) >= 3
        assert "fastapi" in frameworks
        assert "express" in frameworks
        assert "django" in frameworks

    def test_get_security_templates_fastapi(self):
        templates = get_security_templates("fastapi")
        assert len(templates) >= 8
        categories = {t.category for t in templates}
        assert "cors" in categories
        assert "rate_limit" in categories
        assert "auth" in categories

    def test_get_security_templates_express(self):
        templates = get_security_templates("express")
        assert len(templates) >= 8
        categories = {t.category for t in templates}
        assert "cors" in categories
        assert "headers" in categories

    def test_get_security_templates_django(self):
        templates = get_security_templates("django")
        assert len(templates) >= 7
        categories = {t.category for t in templates}
        assert "cors" in categories
        assert "csrf" in categories

    def test_get_security_templates_unknown_framework(self):
        templates = get_security_templates("nonexistent")
        assert templates == []

    def test_get_template_by_category_found(self):
        t = get_template_by_category("fastapi", "cors")
        assert t is not None
        assert t.category == "cors"
        assert t.framework == "fastapi"

    def test_get_template_by_category_not_found(self):
        t = get_template_by_category("fastapi", "nonexistent_cat")
        assert t is None

    def test_get_template_by_category_unknown_framework(self):
        t = get_template_by_category("unknown_fw", "cors")
        assert t is None

    def test_all_templates_have_code(self):
        for fw in list_supported_frameworks():
            for t in get_security_templates(fw):
                assert len(t.code) > 10, f"Template {t.name} has no code"
                assert len(t.description) > 5, f"Template {t.name} has no description"

    def test_all_templates_have_valid_categories(self):
        for fw in list_supported_frameworks():
            for t in get_security_templates(fw):
                assert t.category in VALID_CATEGORIES, (
                    f"Template {t.name} has invalid category '{t.category}'"
                )

    def test_singleton_registry(self):
        r1 = get_security_template_registry()
        r2 = get_security_template_registry()
        assert r1 is r2
        assert isinstance(r1, dict)


# ════════════════════════════════════════════════════════════════════
# Section 3 — Security Checklist Dict
# ════════════════════════════════════════════════════════════════════


class TestSecurityChecklist:
    """Test the SECURITY_CHECKLIST and get_security_checklist()."""

    def test_checklist_has_all_12_categories(self):
        assert len(SECURITY_CHECKLIST) == 12
        assert set(SECURITY_CHECKLIST.keys()) == VALID_CATEGORIES

    def test_checklist_descriptions_not_empty(self):
        for cat, desc in SECURITY_CHECKLIST.items():
            assert len(desc) > 10, f"Checklist description for '{cat}' is too short"

    def test_get_security_checklist_fastapi(self):
        checklist = get_security_checklist("fastapi")
        assert isinstance(checklist, list)
        assert len(checklist) == 12
        # All items should be descriptive strings
        for item in checklist:
            assert isinstance(item, str)
            assert len(item) > 10

    def test_get_security_checklist_unknown_framework(self):
        checklist = get_security_checklist("nonexistent")
        assert isinstance(checklist, list)
        # For unknown frameworks, returns full checklist without NOT COVERED markers
        assert len(checklist) == 12
        for item in checklist:
            assert isinstance(item, str)
            assert ":" in item  # "category: description" format


# ════════════════════════════════════════════════════════════════════
# Section 4 — ChecklistItem Dataclass
# ════════════════════════════════════════════════════════════════════


class TestChecklistItem:
    """Test ChecklistItem frozen dataclass."""

    def test_create_valid(self):
        item = ChecklistItem(
            category="rate_limiting",
            description="Rate limiting must be present",
            severity="critical",
            patterns=(r"\bSlowAPI\b", r"\brate_limit\b"),
        )
        assert item.category == "rate_limiting"
        assert item.severity == "critical"
        assert len(item.patterns) == 2

    def test_frozen(self):
        item = ChecklistItem(
            category="cors", description="d", severity="high",
            patterns=(r"CORSMiddleware",),
        )
        with pytest.raises(AttributeError):
            item.category = "other"  # type: ignore

    def test_invalid_severity_raises(self):
        with pytest.raises(ValueError, match="Invalid severity"):
            ChecklistItem(
                category="cors", description="d", severity="extreme",
                patterns=(r"x",),
            )

    @pytest.mark.parametrize("sev", ["critical", "high", "medium"])
    def test_valid_severities(self, sev: str):
        item = ChecklistItem(
            category="cors", description="d", severity=sev,
            patterns=(r"x",),
        )
        assert item.severity == sev


# ════════════════════════════════════════════════════════════════════
# Section 5 — SecurityChecklistVerifier
# ════════════════════════════════════════════════════════════════════


class TestSecurityChecklistVerifier:
    """Test the verifier against Python and TypeScript generated code."""

    @pytest.fixture
    def verifier(self) -> SecurityChecklistVerifier:
        return SecurityChecklistVerifier()

    def test_empty_files_all_missing(self, verifier: SecurityChecklistVerifier):
        report = verifier.verify({}, "fastapi")
        assert report.total_checks >= 15
        assert report.passed_checks == 0
        assert report.failed_checks == report.total_checks
        assert report.score == 0.0
        assert report.passed is False
        assert len(report.critical_missing) > 0

    def test_python_secure_code_passes_rate_limiting(self, verifier: SecurityChecklistVerifier):
        files = {
            "app/main.py": (
                "from slowapi import Limiter\n"
                "limiter = Limiter(key_func=get_remote_address)\n"
                "app.state.limiter = limiter\n"
            ),
        }
        report = verifier.verify(files, "fastapi")
        rate_results = [r for r in report.results if r.item.category == "rate_limiting"]
        assert len(rate_results) == 1
        assert rate_results[0].present is True

    def test_python_secure_code_passes_cors(self, verifier: SecurityChecklistVerifier):
        files = {
            "app/main.py": (
                "from fastapi.middleware.cors import CORSMiddleware\n"
                "app.add_middleware(CORSMiddleware, allow_origins=['https://example.com'])\n"
            ),
        }
        report = verifier.verify(files, "fastapi")
        cors_results = [r for r in report.results if r.item.category == "cors"]
        assert len(cors_results) == 1
        assert cors_results[0].present is True

    def test_python_secure_code_passes_password_hashing(self, verifier: SecurityChecklistVerifier):
        files = {
            "app/auth.py": (
                "from passlib.context import CryptContext\n"
                "pwd_context = CryptContext(schemes=['bcrypt'])\n"
                "def hash_password(plain: str) -> str:\n"
                "    return pwd_context.hash(plain)\n"
            ),
        }
        report = verifier.verify(files, "fastapi")
        pw_results = [r for r in report.results if r.item.category == "password_hashing"]
        assert len(pw_results) == 1
        assert pw_results[0].present is True

    def test_python_secure_code_passes_jwt_auth(self, verifier: SecurityChecklistVerifier):
        files = {
            "app/auth.py": (
                "from jose import jwt, JWTError\n"
                "def create_access_token(sub: str) -> str:\n"
                "    return jwt.encode({'sub': sub}, SECRET_KEY)\n"
                "def get_current_user(token: str):\n"
                "    payload = jwt.decode(token, SECRET_KEY)\n"
            ),
        }
        report = verifier.verify(files, "fastapi")
        jwt_results = [r for r in report.results if r.item.category == "jwt_auth"]
        assert len(jwt_results) == 1
        assert jwt_results[0].present is True

    def test_python_secure_code_passes_input_validation(self, verifier: SecurityChecklistVerifier):
        files = {
            "app/schemas.py": (
                "from pydantic import BaseModel, Field, EmailStr\n"
                "class UserCreate(BaseModel):\n"
                "    email: EmailStr\n"
                "    name: str = Field(..., min_length=1)\n"
            ),
        }
        report = verifier.verify(files, "fastapi")
        iv_results = [r for r in report.results if r.item.category == "input_validation"]
        assert len(iv_results) == 1
        assert iv_results[0].present is True

    def test_typescript_secure_code_passes_cors(self, verifier: SecurityChecklistVerifier):
        files = {
            "src/app.ts": (
                "import cors from 'cors';\n"
                "app.use(cors({ origin: 'https://example.com' }));\n"
            ),
        }
        report = verifier.verify(files, "express")
        cors_results = [r for r in report.results if r.item.category == "cors"]
        assert len(cors_results) == 1
        assert cors_results[0].present is True

    def test_typescript_secure_code_passes_headers(self, verifier: SecurityChecklistVerifier):
        files = {
            "src/app.ts": (
                "import helmet from 'helmet';\n"
                "app.use(helmet());\n"
            ),
        }
        report = verifier.verify(files, "express")
        hdr_results = [r for r in report.results if r.item.category == "security_headers"]
        assert len(hdr_results) == 1
        assert hdr_results[0].present is True

    def test_fully_secure_python_app(self, verifier: SecurityChecklistVerifier):
        """A fully secured FastAPI app should pass most checks."""
        files = {
            "app/main.py": (
                "from fastapi.middleware.cors import CORSMiddleware\n"
                "from slowapi import Limiter\n"
                "app.add_middleware(CORSMiddleware, allow_origins=['https://app.example.com'])\n"
                "limiter = Limiter(key_func=get_remote_address)\n"
                "# Security headers\n"
                "response.headers['Strict-Transport-Security'] = 'max-age=31536000'\n"
                "response.headers['X-Content-Type-Options'] = 'nosniff'\n"
                "response.headers['X-Frame-Options'] = 'DENY'\n"
            ),
            "app/auth.py": (
                "from passlib.context import CryptContext\n"
                "from jose import jwt, JWTError\n"
                "pwd_context = CryptContext(schemes=['bcrypt'])\n"
                "def hash_password(p): return pwd_context.hash(p)\n"
                "def verify_password(p, h): return pwd_context.verify(p, h)\n"
                "def create_access_token(sub): return jwt.encode({'sub': sub}, SECRET)\n"
                "def get_current_user(token): return jwt.decode(token, SECRET)\n"
                "oauth2_scheme = OAuth2PasswordBearer(tokenUrl='token')\n"
            ),
            "app/schemas.py": (
                "from pydantic import BaseModel, Field, EmailStr\n"
                "class UserCreate(BaseModel):\n"
                "    email: EmailStr\n"
                "    password: str = Field(..., min_length=8)\n"
            ),
            "app/security.py": (
                "csrf_token = generate_csrf()\n"
                "def validate_csrf(request): pass\n"
                "ALLOWED_MIME_TYPES = {'image/png', 'image/jpeg'}\n"
                "MAX_UPLOAD_SIZE = 5 * 1024 * 1024\n"
                "def check_role(user, role): return role in user.roles\n"
                "from cryptography.fernet import Fernet\n"
                "cipher = Fernet(key)\n"
                "encrypted = cipher.encrypt(data)\n"
            ),
            "app/config.py": (
                "import os\n"
                "SECRET_KEY = os.environ.get('SECRET_KEY')\n"
                "DATABASE_URL = os.getenv('DATABASE_URL')\n"
                "HTTPS_REDIRECT = True\n"
                "SESSION_COOKIE_SECURE = True\n"
                "SESSION_COOKIE_HTTPONLY = True\n"
                "SESSION_COOKIE_SAMESITE = 'Lax'\n"
            ),
            "app/middleware.py": (
                "from app.services.audit import log_action\n"
                "# audit logging for all actions\n"
            ),
            "requirements.txt": (
                "fastapi==0.109.0\nbcrypt==4.1.2\npydantic==2.5.3\n"
            ),
        }
        report = verifier.verify(files, "fastapi")
        assert report.score >= 60.0
        # Most critical items should be present
        assert report.passed_checks >= 10

    def test_report_critical_missing_populated(self, verifier: SecurityChecklistVerifier):
        report = verifier.verify({}, "fastapi")
        assert len(report.critical_missing) > 0
        # rate_limiting should be in critical missing
        assert "rate_limiting" in report.critical_missing

    def test_report_score_calculation(self, verifier: SecurityChecklistVerifier):
        report = verifier.verify({}, "fastapi")
        assert report.score == 0.0

    def test_matched_files_tracked(self, verifier: SecurityChecklistVerifier):
        files = {
            "app/main.py": "from slowapi import Limiter\n",
            "app/config.py": "rate_limit = 100\n",
        }
        report = verifier.verify(files, "fastapi")
        rate_results = [r for r in report.results if r.item.category == "rate_limiting"]
        if rate_results and rate_results[0].present:
            assert len(rate_results[0].matched_files) >= 1

    def test_matched_pattern_tracked(self, verifier: SecurityChecklistVerifier):
        files = {"app/main.py": "from slowapi import Limiter\n"}
        report = verifier.verify(files, "fastapi")
        rate_results = [r for r in report.results if r.item.category == "rate_limiting"]
        if rate_results and rate_results[0].present:
            assert rate_results[0].matched_pattern != ""


# ════════════════════════════════════════════════════════════════════
# Section 6 — Framework Resolution
# ════════════════════════════════════════════════════════════════════


class TestFrameworkResolution:
    """Test that different framework names resolve correctly."""

    @pytest.fixture
    def verifier(self) -> SecurityChecklistVerifier:
        return SecurityChecklistVerifier()

    @pytest.mark.parametrize("framework", ["fastapi", "django", "flask", "rails"])
    def test_python_frameworks_resolve(self, verifier: SecurityChecklistVerifier, framework: str):
        report = verifier.verify({}, framework)
        assert report.total_checks >= 15

    @pytest.mark.parametrize("framework", ["express", "nestjs", "nextjs"])
    def test_typescript_frameworks_resolve(self, verifier: SecurityChecklistVerifier, framework: str):
        report = verifier.verify({}, framework)
        assert report.total_checks >= 15


# ════════════════════════════════════════════════════════════════════
# Section 7 — Singleton & Integration
# ════════════════════════════════════════════════════════════════════


class TestSingletonAndIntegration:
    """Test singletons and cross-module integration."""

    def test_verifier_singleton(self):
        v1 = get_security_checklist_verifier()
        v2 = get_security_checklist_verifier()
        assert v1 is v2
        assert isinstance(v1, SecurityChecklistVerifier)

    def test_template_registry_singleton(self):
        r1 = get_security_template_registry()
        r2 = get_security_template_registry()
        assert r1 is r2

    def test_templates_and_checklist_alignment(self):
        """Template categories should overlap with checklist categories."""
        template_cats = set()
        for fw in list_supported_frameworks():
            for t in get_security_templates(fw):
                template_cats.add(t.category)

        checklist_cats = set(SECURITY_CHECKLIST.keys())
        # Templates should cover most checklist categories
        overlap = template_cats & checklist_cats
        assert len(overlap) >= 8, f"Only {len(overlap)} categories overlap: {overlap}"


# ════════════════════════════════════════════════════════════════════
# Section 8 — Edge Cases
# ════════════════════════════════════════════════════════════════════


class TestSecurityEdgeCases:
    """Edge cases for security templates and checklist."""

    def test_empty_code_template_still_valid(self):
        # Category is valid, but code could be minimal
        t = SecurityTemplate(
            name="T", category="cors", framework="f",
            code="# TODO", description="d",
        )
        assert t.code == "# TODO"

    def test_unicode_in_template(self):
        t = SecurityTemplate(
            name="日本語テンプレート", category="cors", framework="f",
            code="# 日本語", description="日本語の説明",
        )
        assert "日本語" in t.name

    def test_verifier_with_binary_looking_content(self):
        verifier = SecurityChecklistVerifier()
        files = {"app/main.py": "\x00\x01\x02 from slowapi import Limiter\n"}
        # Should not crash
        report = verifier.verify(files, "fastapi")
        assert isinstance(report, SecurityChecklistReport)

    def test_verifier_large_files(self):
        verifier = SecurityChecklistVerifier()
        large_content = "x = 1\n" * 10000 + "from slowapi import Limiter\n"
        files = {"app/big.py": large_content}
        report = verifier.verify(files, "fastapi")
        rate_results = [r for r in report.results if r.item.category == "rate_limiting"]
        assert rate_results[0].present is True

    def test_checklist_report_all_passed(self):
        """Manually create a report where all items pass."""
        item = ChecklistItem(
            category="cors", description="d", severity="critical",
            patterns=(r"CORSMiddleware",),
        )
        result = ChecklistResult(
            item=item, present=True,
            matched_files=["app/main.py"], matched_pattern=r"CORSMiddleware",
        )
        report = SecurityChecklistReport(
            results=[result],
            total_checks=1,
            passed_checks=1,
            failed_checks=0,
            critical_missing=[],
            score=100.0,
            passed=True,
        )
        assert report.passed is True
        assert report.score == 100.0

    def test_checklist_15_items_for_python(self):
        verifier = SecurityChecklistVerifier()
        report = verifier.verify({}, "fastapi")
        assert report.total_checks == 15

    def test_checklist_15_items_for_typescript(self):
        verifier = SecurityChecklistVerifier()
        report = verifier.verify({}, "express")
        assert report.total_checks == 15
