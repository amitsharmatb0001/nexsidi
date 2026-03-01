"""Tests for tech stack validation, suggestions, and registry."""

from __future__ import annotations

import pytest

from app.services.tech_stack import (
    ALL_STACKS,
    BACKEND_STACKS,
    DATABASE_STACKS,
    DESKTOP_STACKS,
    FRONTEND_STACKS,
    INFRA_STACKS,
    MOBILE_STACKS,
    Maturity,
    TechEntry,
    ValidationResult,
    get_maturity,
    get_supported_stacks,
    resolve_tech_name,
    suggest_tech_stack,
    validate_tech_stack,
)


class TestRegistry:
    """Test the tech stack registry."""

    def test_backend_stacks_exist(self):
        assert len(BACKEND_STACKS) >= 5
        assert "fastapi" in BACKEND_STACKS
        assert "django" in BACKEND_STACKS
        assert "express" in BACKEND_STACKS

    def test_frontend_stacks_exist(self):
        assert len(FRONTEND_STACKS) >= 4
        assert "nextjs" in FRONTEND_STACKS
        assert "react" in FRONTEND_STACKS

    def test_database_stacks_exist(self):
        assert len(DATABASE_STACKS) >= 4
        assert "postgresql" in DATABASE_STACKS
        assert "mysql" in DATABASE_STACKS

    def test_infra_stacks_exist(self):
        assert "redis" in INFRA_STACKS

    def test_all_stacks_combined(self):
        total = (
            len(BACKEND_STACKS)
            + len(FRONTEND_STACKS)
            + len(DATABASE_STACKS)
            + len(INFRA_STACKS)
            + len(MOBILE_STACKS)
            + len(DESKTOP_STACKS)
        )
        assert len(ALL_STACKS) == total

    def test_fastapi_is_production(self):
        assert ALL_STACKS["fastapi"].maturity == Maturity.PRODUCTION

    def test_django_is_production(self):
        assert ALL_STACKS["django"].maturity == Maturity.PRODUCTION

    def test_spring_is_planned(self):
        assert ALL_STACKS["spring"].maturity == Maturity.PLANNED


class TestResolve:
    """Test tech name resolution (aliases)."""

    def test_resolve_canonical(self):
        assert resolve_tech_name("fastapi") == "fastapi"
        assert resolve_tech_name("nextjs") == "nextjs"

    def test_resolve_alias(self):
        assert resolve_tech_name("fast-api") == "fastapi"
        assert resolve_tech_name("fast_api") == "fastapi"

    def test_resolve_display_name(self):
        assert resolve_tech_name("FastAPI") == "fastapi"
        assert resolve_tech_name("Next.js") == "nextjs"
        assert resolve_tech_name("PostgreSQL") == "postgresql"

    def test_resolve_common_aliases(self):
        assert resolve_tech_name("postgres") == "postgresql"
        assert resolve_tech_name("pg") == "postgresql"
        assert resolve_tech_name("node") == "express"
        assert resolve_tech_name("drf") == "django"
        assert resolve_tech_name("mongo") == "mongodb"
        assert resolve_tech_name("valkey") == "redis"

    def test_resolve_unknown(self):
        assert resolve_tech_name("cobol") is None
        assert resolve_tech_name("") is None

    def test_resolve_case_insensitive(self):
        assert resolve_tech_name("FASTAPI") == "fastapi"
        assert resolve_tech_name("Django") == "django"
        assert resolve_tech_name("POSTGRESQL") == "postgresql"

    def test_resolve_with_whitespace(self):
        assert resolve_tech_name("  fastapi  ") == "fastapi"


class TestValidation:
    """Test tech stack validation."""

    def test_validate_all_production(self):
        result = validate_tech_stack({
            "backend": "FastAPI",
            "frontend": "Next.js",
            "database": "PostgreSQL",
        })
        assert result.supported is True
        assert len(result.warnings) == 0
        assert result.resolved["backend"] == "FastAPI"

    def test_validate_production_no_warnings(self):
        result = validate_tech_stack({"backend": "Django"})
        assert result.supported is True
        assert len(result.warnings) == 0

    def test_validate_alpha_warns(self):
        result = validate_tech_stack({"frontend": "Vue.js"})
        assert any("alpha" in w.lower() for w in result.warnings)

    def test_validate_planned_substitutes(self):
        result = validate_tech_stack({"backend": "Spring Boot"})
        assert result.supported is False
        assert "backend" in result.substitutions
        assert "FastAPI" in result.substitutions["backend"]

    def test_validate_unknown_substitutes(self):
        result = validate_tech_stack({"backend": "Cobol"})
        assert "backend" in result.substitutions
        assert len(result.warnings) > 0

    def test_validate_mixed_stack(self):
        result = validate_tech_stack({
            "backend": "FastAPI",
            "frontend": "Angular",  # Planned
            "database": "PostgreSQL",
        })
        assert result.supported is False  # Angular is planned
        assert result.resolved["backend"] == "FastAPI"
        assert result.resolved["database"] == "PostgreSQL"
        assert "Next.js" in result.substitutions.get("frontend", "")

    def test_validate_aliases_resolve(self):
        result = validate_tech_stack({"database": "postgres"})
        assert result.resolved["database"] == "PostgreSQL"
        assert len(result.warnings) == 0

    def test_validate_empty_stack(self):
        result = validate_tech_stack({})
        assert result.supported is True
        assert len(result.warnings) == 0


class TestSuggestions:
    """Test tech stack suggestions."""

    def test_suggest_financial_app(self):
        suggestion = suggest_tech_stack("ACID-heavy banking application with payments")
        assert suggestion.stack["database"] == "PostgreSQL"
        assert "ACID" in suggestion.reasoning["database"]

    def test_suggest_realtime_app(self):
        suggestion = suggest_tech_stack("Real-time chat application with WebSocket")
        assert suggestion.stack["backend"] == "Express.js"

    def test_suggest_enterprise_app(self):
        suggestion = suggest_tech_stack("Enterprise CMS with admin panel")
        assert suggestion.stack["backend"] == "Django"

    def test_suggest_ml_app(self):
        suggestion = suggest_tech_stack("Machine learning API for data science")
        assert suggestion.stack["backend"] == "FastAPI"

    def test_suggest_seo_app(self):
        suggestion = suggest_tech_stack("Marketing blog with SEO")
        assert suggestion.stack["frontend"] == "Next.js"

    def test_suggest_dashboard(self):
        suggestion = suggest_tech_stack("Internal admin dashboard CRM")
        assert suggestion.stack["frontend"] == "React"

    def test_suggest_default(self):
        suggestion = suggest_tech_stack("")
        assert suggestion.stack["backend"] == "FastAPI"
        assert suggestion.stack["frontend"] == "Next.js"
        assert suggestion.stack["database"] == "PostgreSQL"

    def test_suggest_with_cache(self):
        suggestion = suggest_tech_stack("App with caching and sessions")
        assert suggestion.stack.get("cache") == "Redis/Valkey"

    def test_suggest_large_scale_gets_cache(self):
        suggestion = suggest_tech_stack("Simple app", scale="enterprise")
        assert "cache" in suggestion.stack

    def test_suggest_has_reasoning(self):
        suggestion = suggest_tech_stack("A web app")
        assert "backend" in suggestion.reasoning
        assert "frontend" in suggestion.reasoning
        assert "database" in suggestion.reasoning

    def test_suggest_document_db(self):
        suggestion = suggest_tech_stack("Flexible schema document storage nosql")
        assert suggestion.stack["database"] == "MongoDB"


class TestConvenience:
    """Test convenience functions."""

    def test_get_supported_stacks(self):
        stacks = get_supported_stacks()
        assert "backend" in stacks
        assert "frontend" in stacks
        assert "database" in stacks
        assert len(stacks["backend"]) >= 5

    def test_get_maturity(self):
        assert get_maturity("FastAPI") == Maturity.PRODUCTION
        assert get_maturity("Django") == Maturity.PRODUCTION
        assert get_maturity("Express.js") == Maturity.PRODUCTION
        assert get_maturity("Vue.js") == Maturity.ALPHA
        assert get_maturity("Spring Boot") == Maturity.PLANNED
        assert get_maturity("Cobol") is None
