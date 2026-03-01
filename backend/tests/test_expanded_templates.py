"""Tests for expanded tech stack templates (Django, Express) and framework selection."""

from __future__ import annotations

import pytest

from app.agents.shubham import (
    BACKEND_GENERATION_ORDER,
    DJANGO_GENERATION_ORDER,
    EXPRESS_GENERATION_ORDER,
    FASTAPI_GENERATION_ORDER,
    get_backend_generation_order,
)
from app.engine.template_engine import FileTemplate, TemplateEngine, get_template_engine


class TestTemplateRegistration:
    """Test that framework-specific templates are registered."""

    def test_fastapi_templates_exist(self):
        engine = TemplateEngine()
        names = engine.list_templates(category="backend")
        fastapi_names = [n for n in names if engine._templates[n].framework == "fastapi"]
        assert len(fastapi_names) >= 3  # Dockerfile, requirements.txt, .env.example

    def test_django_templates_exist(self):
        engine = TemplateEngine()
        names = engine.list_templates(category="backend")
        django_names = [n for n in names if engine._templates[n].framework == "django"]
        assert len(django_names) >= 4  # Dockerfile, requirements, settings, .env

    def test_express_templates_exist(self):
        engine = TemplateEngine()
        names = engine.list_templates(category="backend")
        express_names = [n for n in names if engine._templates[n].framework == "express"]
        assert len(express_names) >= 4  # Dockerfile, package.json, tsconfig, .env

    def test_universal_templates_exist(self):
        """Templates without framework should still be registered."""
        engine = TemplateEngine()
        universal = [
            n for n in engine.list_templates()
            if engine._templates[n].framework is None
        ]
        assert len(universal) >= 1  # docker-compose, frontend templates

    def test_file_template_framework_field(self):
        tmpl = FileTemplate(
            name="test",
            output_path="test.txt",
            content="hello",
            framework="django",
        )
        assert tmpl.framework == "django"

    def test_file_template_framework_default_none(self):
        tmpl = FileTemplate(
            name="test",
            output_path="test.txt",
            content="hello",
        )
        assert tmpl.framework is None


class TestFrameworkFiltering:
    """Test that render_all filters by framework correctly."""

    def test_render_fastapi_excludes_django(self):
        engine = TemplateEngine()
        contract = {"project_name": "Test", "tech_stack": {"backend": "fastapi"}}
        files = engine.render_all(contract, categories=["backend"], framework="fastapi")
        file_names = [f.template_name for f in files]
        # Should include FastAPI templates
        assert any("backend.dockerfile" in n or "backend.requirements" in n for n in file_names)
        # Should NOT include Django or Express templates
        assert not any("django" in n for n in file_names)
        assert not any("express" in n for n in file_names)

    def test_render_django_excludes_fastapi(self):
        engine = TemplateEngine()
        contract = {"project_name": "Test", "tech_stack": {"backend": "django"}}
        files = engine.render_all(contract, categories=["backend"], framework="django")
        file_names = [f.template_name for f in files]
        # Should include Django templates
        assert any("django" in n for n in file_names)
        # Should NOT include FastAPI-specific templates
        assert "backend.dockerfile" not in file_names
        assert "backend.requirements" not in file_names

    def test_render_express_excludes_python(self):
        engine = TemplateEngine()
        contract = {"project_name": "Test", "tech_stack": {"backend": "express"}}
        files = engine.render_all(contract, categories=["backend"], framework="express")
        file_names = [f.template_name for f in files]
        assert any("express" in n for n in file_names)
        assert not any("django" in n for n in file_names)
        assert "backend.dockerfile" not in file_names

    def test_render_no_framework_filter_includes_all(self):
        """Without framework filter, all templates render (for backward compat)."""
        engine = TemplateEngine()
        contract = {"project_name": "Test", "tech_stack": {}}
        files = engine.render_all(contract, categories=["backend"])
        file_names = [f.template_name for f in files]
        # Should include templates from all frameworks
        assert len(file_names) >= 7  # 3 fastapi + 4 django + 4 express

    def test_universal_templates_always_included(self):
        """Templates with framework=None should render for any framework."""
        engine = TemplateEngine()
        contract = {"project_name": "Test", "tech_stack": {}}
        # docker-compose has no framework, should appear for all
        files = engine.render_all(contract, categories=["config"], framework="django")
        file_names = [f.template_name for f in files]
        assert "backend.docker_compose" in file_names


class TestDjangoTemplateRendering:
    """Test Django template content renders correctly."""

    def test_django_settings_renders(self):
        engine = TemplateEngine()
        contract = {
            "project_name": "My Shop",
            "security": {"cors": {"origins": ["http://localhost:3000"]}},
            "tech_stack": {"backend": "django"},
        }
        result = engine.render_one("backend.django.settings", contract)
        assert result is not None
        assert "my-shop" in result.path  # project_slug in output path
        assert "INSTALLED_APPS" in result.content
        assert "rest_framework" in result.content
        assert "corsheaders" in result.content
        assert "whitenoise" in result.content

    def test_django_requirements_renders(self):
        engine = TemplateEngine()
        contract = {"project_name": "Test"}
        result = engine.render_one("backend.django.requirements", contract)
        assert result is not None
        assert "django>=" in result.content.lower()
        assert "djangorestframework" in result.content
        assert "gunicorn" in result.content
        assert "psycopg" in result.content

    def test_django_dockerfile_renders(self):
        engine = TemplateEngine()
        contract = {"project_name": "Test"}
        result = engine.render_one("backend.django.dockerfile", contract)
        assert result is not None
        assert "gunicorn" in result.content
        assert "collectstatic" in result.content


class TestExpressTemplateRendering:
    """Test Express template content renders correctly."""

    def test_express_package_json_renders(self):
        engine = TemplateEngine()
        contract = {"project_name": "My API"}
        result = engine.render_one("backend.express.package_json", contract)
        assert result is not None
        assert "my-api" in result.content  # project_slug
        assert '"express"' in result.content
        assert '"drizzle-orm"' in result.content
        assert '"zod"' in result.content
        assert '"typescript"' in result.content

    def test_express_tsconfig_renders(self):
        engine = TemplateEngine()
        contract = {"project_name": "Test"}
        result = engine.render_one("backend.express.tsconfig", contract)
        assert result is not None
        assert '"NodeNext"' in result.content
        assert '"strict": true' in result.content

    def test_express_dockerfile_renders(self):
        engine = TemplateEngine()
        contract = {"project_name": "Test"}
        result = engine.render_one("backend.express.dockerfile", contract)
        assert result is not None
        assert "node:22-slim" in result.content
        assert "npm ci" in result.content
        assert "npm run build" in result.content

    def test_express_env_example_renders(self):
        engine = TemplateEngine()
        contract = {"project_name": "My API"}
        result = engine.render_one("backend.express.env_example", contract)
        assert result is not None
        assert "my-api" in result.content
        assert "JWT_SECRET" in result.content
        assert "NODE_ENV" in result.content


class TestGenerationOrders:
    """Test framework-specific generation orders for Shubham."""

    def test_fastapi_order_exists(self):
        assert len(FASTAPI_GENERATION_ORDER) >= 7
        names = [s["name"] for s in FASTAPI_GENERATION_ORDER]
        assert "models" in names
        assert "schemas" in names
        assert "security" in names
        assert "routers" in names

    def test_django_order_exists(self):
        assert len(DJANGO_GENERATION_ORDER) >= 7
        names = [s["name"] for s in DJANGO_GENERATION_ORDER]
        assert "models" in names
        assert "serializers" in names
        assert "permissions" in names
        assert "views" in names
        assert "urls" in names

    def test_express_order_exists(self):
        assert len(EXPRESS_GENERATION_ORDER) >= 7
        names = [s["name"] for s in EXPRESS_GENERATION_ORDER]
        assert "db_schema" in names
        assert "types" in names
        assert "middleware" in names
        assert "routes" in names

    def test_backward_compat_alias(self):
        assert BACKEND_GENERATION_ORDER is FASTAPI_GENERATION_ORDER

    def test_get_backend_generation_order_fastapi(self):
        order = get_backend_generation_order("fastapi")
        assert order is FASTAPI_GENERATION_ORDER

    def test_get_backend_generation_order_django(self):
        order = get_backend_generation_order("django")
        assert order is DJANGO_GENERATION_ORDER

    def test_get_backend_generation_order_express(self):
        order = get_backend_generation_order("express")
        assert order is EXPRESS_GENERATION_ORDER

    def test_get_backend_generation_order_unknown_defaults_fastapi(self):
        order = get_backend_generation_order("cobol")
        assert order is FASTAPI_GENERATION_ORDER

    def test_get_backend_generation_order_case_insensitive(self):
        order = get_backend_generation_order("Django")
        assert order is DJANGO_GENERATION_ORDER

    def test_all_orders_have_auth_step(self):
        """Every framework should have a security/auth generation step."""
        for name, order in [
            ("fastapi", FASTAPI_GENERATION_ORDER),
            ("django", DJANGO_GENERATION_ORDER),
            ("express", EXPRESS_GENERATION_ORDER),
        ]:
            auth_steps = [s for s in order if s["task_type"] == "auth_code"]
            assert len(auth_steps) >= 1, f"{name} missing auth_code step"


class TestMaturityUpdates:
    """Test that Django and Express are now production maturity."""

    def test_django_production(self):
        from app.services.tech_stack import ALL_STACKS, Maturity
        assert ALL_STACKS["django"].maturity == Maturity.PRODUCTION

    def test_express_production(self):
        from app.services.tech_stack import ALL_STACKS, Maturity
        assert ALL_STACKS["express"].maturity == Maturity.PRODUCTION

    def test_fastapi_still_production(self):
        from app.services.tech_stack import ALL_STACKS, Maturity
        assert ALL_STACKS["fastapi"].maturity == Maturity.PRODUCTION
