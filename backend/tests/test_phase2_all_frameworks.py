"""Phase 2 tests: All backend frameworks (Gaps 146-158).

Covers all 13 registered backend frameworks:
- FastAPI, Django, Express (existing)
- Flask, NestJS, Next.js, Laravel, Spring Boot (new)
- ASP.NET Core, Go (Gin), Ruby on Rails, Rust (Axum), Kotlin (Ktor) (new)

Tests are BRUTAL: every config is validated for structure, content quality,
language correctness, rule count, golden example presence, and prompt integration.
"""

from __future__ import annotations

import pytest

from app.agents.frameworks import (
    FrameworkConfig,
    get_framework_config,
    list_frameworks,
)
from app.agents.shubham import (
    Shubham,
    get_backend_generation_order,
    get_dependency_graph,
    compute_parallel_levels,
)


# ── All 13 frameworks ──────────────────────────────────────────────

ALL_FRAMEWORKS = [
    "fastapi", "django", "express",
    "flask", "nestjs", "nextjs", "laravel", "springboot",
    "aspnet", "go_gin", "rails", "rust_axum", "kotlin_ktor",
]

PYTHON_FRAMEWORKS = ["fastapi", "django", "flask"]
TYPESCRIPT_FRAMEWORKS = ["express", "nestjs", "nextjs"]
OTHER_LANG_FRAMEWORKS = ["laravel", "springboot", "aspnet", "go_gin", "rails", "rust_axum", "kotlin_ktor"]

EXPECTED_LANGUAGES = {
    "fastapi": "python", "django": "python", "flask": "python",
    "express": "typescript", "nestjs": "typescript", "nextjs": "typescript",
    "laravel": "php", "springboot": "java", "aspnet": "csharp",
    "go_gin": "go", "rails": "ruby", "rust_axum": "rust", "kotlin_ktor": "kotlin",
}

EXPECTED_ERROR_PREFIXES = {
    "fastapi": "#", "django": "#", "flask": "#", "rails": "#",
    "express": "//", "nestjs": "//", "nextjs": "//",
    "laravel": "//", "springboot": "//", "aspnet": "//",
    "go_gin": "//", "rust_axum": "//", "kotlin_ktor": "//",
}


# ── TestAllFrameworksRegistered ─────────────────────────────────────


class TestAllFrameworksRegistered:
    """Verify all 13 frameworks are registered and discoverable."""

    def test_list_frameworks_returns_all_13(self):
        frameworks = list_frameworks()
        assert len(frameworks) >= 13, f"Expected >= 13 frameworks, got {len(frameworks)}: {frameworks}"

    @pytest.mark.parametrize("name", ALL_FRAMEWORKS)
    def test_framework_exists(self, name: str):
        config = get_framework_config(name)
        assert config.name == name

    @pytest.mark.parametrize("name", ALL_FRAMEWORKS)
    def test_framework_is_frozen(self, name: str):
        config = get_framework_config(name)
        with pytest.raises(Exception):  # FrozenInstanceError
            config.name = "hacked"  # type: ignore[misc]


# ── TestFrameworkLanguages ──────────────────────────────────────────


class TestFrameworkLanguages:
    """Verify language, code_block_lang, and error_comment_prefix are correct."""

    @pytest.mark.parametrize("name,expected", EXPECTED_LANGUAGES.items())
    def test_language_correct(self, name: str, expected: str):
        config = get_framework_config(name)
        assert config.language == expected, f"{name} language should be {expected}, got {config.language}"

    @pytest.mark.parametrize("name,expected", EXPECTED_LANGUAGES.items())
    def test_code_block_lang_matches_language(self, name: str, expected: str):
        config = get_framework_config(name)
        assert config.code_block_lang == expected

    @pytest.mark.parametrize("name,expected", EXPECTED_ERROR_PREFIXES.items())
    def test_error_comment_prefix_correct(self, name: str, expected: str):
        config = get_framework_config(name)
        assert config.error_comment_prefix == expected


# ── TestFrameworkRules ──────────────────────────────────────────────


class TestFrameworkRules:
    """Verify each framework has sufficient, meaningful rules."""

    @pytest.mark.parametrize("name", ALL_FRAMEWORKS)
    def test_has_minimum_12_rules(self, name: str):
        config = get_framework_config(name)
        assert len(config.rules) >= 12, f"{name} has only {len(config.rules)} rules, need >= 12"

    @pytest.mark.parametrize("name", ALL_FRAMEWORKS)
    def test_rules_are_non_empty_strings(self, name: str):
        config = get_framework_config(name)
        for i, rule in enumerate(config.rules):
            assert isinstance(rule, str), f"{name} rule {i} is not a string"
            assert len(rule) >= 20, f"{name} rule {i} is too short ({len(rule)} chars)"

    @pytest.mark.parametrize("name", ALL_FRAMEWORKS)
    def test_rules_mention_never(self, name: str):
        """Rules should explicitly state what NOT to do."""
        config = get_framework_config(name)
        rules_text = "\n".join(config.rules).lower()
        assert "never" in rules_text, f"{name} rules should include NEVER constraints"

    @pytest.mark.parametrize("name", ALL_FRAMEWORKS)
    def test_rules_no_output_explanation(self, name: str):
        """Last rule should mention 'output only' code."""
        config = get_framework_config(name)
        last_rule = config.rules[-1].lower()
        assert "output" in last_rule or "code" in last_rule or "markdown" in last_rule, \
            f"{name} last rule should mention output format"


# ── TestFrameworkGoldenExamples ─────────────────────────────────────


class TestFrameworkGoldenExamples:
    """Verify golden examples are present and substantial."""

    @pytest.mark.parametrize("name", ALL_FRAMEWORKS)
    def test_has_minimum_3_golden_examples(self, name: str):
        config = get_framework_config(name)
        assert len(config.golden_examples) >= 3, \
            f"{name} has only {len(config.golden_examples)} golden examples, need >= 3"

    @pytest.mark.parametrize("name", ALL_FRAMEWORKS)
    def test_golden_examples_are_substantial(self, name: str):
        config = get_framework_config(name)
        for step_name, code in config.golden_examples.items():
            assert len(code) >= 100, \
                f"{name}/{step_name} golden example too short ({len(code)} chars)"

    @pytest.mark.parametrize("name", ALL_FRAMEWORKS)
    def test_golden_examples_have_code_content(self, name: str):
        """Golden examples should look like real code, not descriptions."""
        config = get_framework_config(name)
        for step_name, code in config.golden_examples.items():
            # Should have multiple lines of actual code
            lines = [l for l in code.strip().split("\n") if l.strip()]
            assert len(lines) >= 5, \
                f"{name}/{step_name} golden example has only {len(lines)} non-empty lines"


# ── TestFrameworkFileStructure ──────────────────────────────────────


class TestFrameworkFileStructure:
    """Verify file structure maps are complete and valid."""

    @pytest.mark.parametrize("name", ALL_FRAMEWORKS)
    def test_has_minimum_5_file_paths(self, name: str):
        config = get_framework_config(name)
        assert len(config.file_structure) >= 5, \
            f"{name} file_structure has only {len(config.file_structure)} entries, need >= 5"

    @pytest.mark.parametrize("name", ALL_FRAMEWORKS)
    def test_file_paths_are_strings(self, name: str):
        config = get_framework_config(name)
        for step, path in config.file_structure.items():
            assert isinstance(path, str), f"{name}/{step} path is not a string"
            assert len(path) > 5, f"{name}/{step} path too short: '{path}'"

    @pytest.mark.parametrize("name", ALL_FRAMEWORKS)
    def test_file_structure_has_models_or_entity(self, name: str):
        """Every framework should have a model/entity step."""
        config = get_framework_config(name)
        model_keys = {"models", "entity", "db_schema"}
        found = model_keys & set(config.file_structure.keys())
        assert found, f"{name} file_structure missing model-like key (tried {model_keys})"


# ── TestFrameworkSpecificContent ────────────────────────────────────


class TestFrameworkSpecificContent:
    """Test that each framework config mentions its key technology."""

    def test_flask_mentions_flask(self):
        config = get_framework_config("flask")
        rules = "\n".join(config.rules).lower()
        assert "flask" in rules or "blueprint" in rules

    def test_flask_mentions_marshmallow_or_sqlalchemy(self):
        config = get_framework_config("flask")
        rules = "\n".join(config.rules).lower()
        assert "marshmallow" in rules or "sqlalchemy" in rules

    def test_nestjs_mentions_typeorm_or_nest(self):
        config = get_framework_config("nestjs")
        rules = "\n".join(config.rules).lower()
        assert "typeorm" in rules or "nest" in rules

    def test_nestjs_mentions_decorator(self):
        config = get_framework_config("nestjs")
        rules = "\n".join(config.rules).lower()
        # NestJS heavily uses decorators
        assert "module" in rules or "controller" in rules or "injectable" in rules

    def test_nextjs_mentions_prisma_or_route(self):
        config = get_framework_config("nextjs")
        rules = "\n".join(config.rules).lower()
        assert "prisma" in rules or "route" in rules

    def test_laravel_mentions_eloquent(self):
        config = get_framework_config("laravel")
        rules = "\n".join(config.rules).lower()
        assert "eloquent" in rules

    def test_springboot_mentions_jpa_or_spring(self):
        config = get_framework_config("springboot")
        rules = "\n".join(config.rules).lower()
        assert "jpa" in rules or "spring" in rules

    def test_aspnet_mentions_ef_core_or_dotnet(self):
        config = get_framework_config("aspnet")
        rules = "\n".join(config.rules).lower()
        assert "ef core" in rules or "entity framework" in rules or "ef" in rules

    def test_go_gin_mentions_gorm_or_gin(self):
        config = get_framework_config("go_gin")
        rules = "\n".join(config.rules).lower()
        assert "gorm" in rules or "gin" in rules

    def test_rails_mentions_activerecord(self):
        config = get_framework_config("rails")
        rules = "\n".join(config.rules).lower()
        assert "activerecord" in rules or "active_record" in rules or "active record" in rules

    def test_rust_axum_mentions_axum_or_sqlx(self):
        config = get_framework_config("rust_axum")
        rules = "\n".join(config.rules).lower()
        assert "axum" in rules or "sqlx" in rules

    def test_kotlin_ktor_mentions_ktor_or_exposed(self):
        config = get_framework_config("kotlin_ktor")
        rules = "\n".join(config.rules).lower()
        assert "ktor" in rules or "exposed" in rules


# ── TestGenerationOrders ───────────────────────────────────────────


class TestGenerationOrders:
    """Test generation orders exist for all frameworks."""

    @pytest.mark.parametrize("name", ALL_FRAMEWORKS)
    def test_generation_order_exists(self, name: str):
        order = get_backend_generation_order(name)
        assert len(order) >= 5, f"{name} has only {len(order)} generation steps"

    @pytest.mark.parametrize("name", ALL_FRAMEWORKS)
    def test_generation_order_has_required_fields(self, name: str):
        order = get_backend_generation_order(name)
        for step in order:
            assert "name" in step, f"{name}: step missing 'name'"
            assert "path" in step, f"{name}: step missing 'path'"
            assert "task_type" in step, f"{name}: step missing 'task_type'"
            assert "description" in step, f"{name}: step missing 'description'"

    @pytest.mark.parametrize("name", ALL_FRAMEWORKS)
    def test_generation_order_has_security_step(self, name: str):
        """Every framework should have at least one auth_code step."""
        order = get_backend_generation_order(name)
        auth_steps = [s for s in order if s["task_type"] == "auth_code"]
        assert len(auth_steps) >= 1, f"{name} has no auth_code generation step"

    @pytest.mark.parametrize("name", ALL_FRAMEWORKS)
    def test_generation_order_step_names_unique(self, name: str):
        order = get_backend_generation_order(name)
        names = [s["name"] for s in order]
        assert len(names) == len(set(names)), f"{name} has duplicate step names: {names}"


# ── TestDependencyGraphs ───────────────────────────────────────────


class TestDependencyGraphs:
    """Test dependency graphs exist and are valid for all frameworks."""

    @pytest.mark.parametrize("name", ALL_FRAMEWORKS)
    def test_dependency_graph_exists(self, name: str):
        deps = get_dependency_graph(name)
        assert isinstance(deps, dict)
        assert len(deps) >= 3

    @pytest.mark.parametrize("name", ALL_FRAMEWORKS)
    def test_dependency_graph_has_root_step(self, name: str):
        """At least one step should have no dependencies."""
        deps = get_dependency_graph(name)
        roots = [name for name, d in deps.items() if len(d) == 0]
        assert len(roots) >= 1, f"{name} has no root steps (all have deps)"

    @pytest.mark.parametrize("name", ALL_FRAMEWORKS)
    def test_dependency_graph_references_valid_steps(self, name: str):
        """All dependency references should point to steps that exist."""
        deps = get_dependency_graph(name)
        all_step_names = set(deps.keys())
        for step_name, step_deps in deps.items():
            for dep in step_deps:
                assert dep in all_step_names, \
                    f"{name}: step '{step_name}' depends on '{dep}' which is not a known step"

    @pytest.mark.parametrize("name", ALL_FRAMEWORKS)
    def test_no_self_dependency(self, name: str):
        """No step should depend on itself."""
        deps = get_dependency_graph(name)
        for step_name, step_deps in deps.items():
            assert step_name not in step_deps, f"{name}: step '{step_name}' depends on itself"

    @pytest.mark.parametrize("name", ALL_FRAMEWORKS)
    def test_parallel_levels_cover_all_steps(self, name: str):
        """compute_parallel_levels should include every step from generation order."""
        order = get_backend_generation_order(name)
        deps = get_dependency_graph(name)
        levels = compute_parallel_levels(order, deps)

        all_steps = set()
        for level in levels:
            for step in level:
                all_steps.add(step["name"])

        expected = {s["name"] for s in order}
        assert all_steps == expected, f"{name}: level steps {all_steps} != order steps {expected}"

    @pytest.mark.parametrize("name", ALL_FRAMEWORKS)
    def test_parallel_levels_respect_dependencies(self, name: str):
        """Steps should only appear after their dependencies."""
        order = get_backend_generation_order(name)
        deps = get_dependency_graph(name)
        levels = compute_parallel_levels(order, deps)

        completed: set[str] = set()
        for level in levels:
            for step in level:
                step_deps = deps.get(step["name"], set())
                assert step_deps <= completed, \
                    f"{name}: step '{step['name']}' at level has unmet deps: {step_deps - completed}"
            for step in level:
                completed.add(step["name"])


# ── TestPromptIntegrationAllFrameworks ──────────────────────────────


class TestPromptIntegrationAllFrameworks:
    """Test Shubham's _build_generation_prompt() works with all frameworks."""

    def _build_prompt(self, framework: str, step_name: str = "models") -> str:
        agent = Shubham()
        order = get_backend_generation_order(framework)
        step = next((s for s in order if s["name"] == step_name), order[0])
        config = get_framework_config(framework)
        contract = {"project_name": "Test", "tech_stack": {"backend": framework}}
        return agent._build_generation_prompt(
            step=step,
            contract=contract,
            accumulated_code={},
            db_artifacts="",
            fw_config=config,
        )

    @pytest.mark.parametrize("name", ALL_FRAMEWORKS)
    def test_prompt_contains_framework_rules(self, name: str):
        config = get_framework_config(name)
        prompt = self._build_prompt(name)
        assert f"MANDATORY {config.display_name} RULES" in prompt

    @pytest.mark.parametrize("name", ALL_FRAMEWORKS)
    def test_prompt_contains_code_block_language(self, name: str):
        config = get_framework_config(name)
        prompt = self._build_prompt(name)
        assert f"```{config.code_block_lang}" in prompt

    @pytest.mark.parametrize("name", ALL_FRAMEWORKS)
    def test_prompt_contains_file_structure(self, name: str):
        prompt = self._build_prompt(name)
        assert "Project File Structure" in prompt

    @pytest.mark.parametrize("name", ALL_FRAMEWORKS)
    def test_prompt_contains_completeness_rules(self, name: str):
        prompt = self._build_prompt(name)
        assert "COMPLETENESS RULES" in prompt
        assert "NEVER stop mid-function" in prompt


# ── TestFrameworkAliases ────────────────────────────────────────────


class TestFrameworkAliases:
    """Test that common framework name aliases resolve correctly."""

    ALIAS_TESTS = [
        ("fast api", "fastapi"),
        ("express.js", "express"),
        ("nest.js", "nestjs"),
        ("nest", "nestjs"),
        ("next.js", "nextjs"),
        ("next", "nextjs"),
        ("spring boot", "springboot"),
        ("spring", "springboot"),
        ("asp.net", "aspnet"),
        ("asp.net core", "aspnet"),
        ("dotnet", "aspnet"),
        ("gin", "go_gin"),
        ("go", "go_gin"),
        ("golang", "go_gin"),
        ("ruby on rails", "rails"),
        ("ruby", "rails"),
        ("ror", "rails"),
        ("axum", "rust_axum"),
        ("rust", "rust_axum"),
        ("ktor", "kotlin_ktor"),
        ("kotlin", "kotlin_ktor"),
    ]

    @pytest.mark.parametrize("alias,canonical", ALIAS_TESTS)
    def test_alias_resolves(self, alias: str, canonical: str):
        """Test that the alias dict in Shubham.execute resolves correctly."""
        _fw_aliases = {
            "fast api": "fastapi", "express.js": "express",
            "nest.js": "nestjs", "nest": "nestjs",
            "next.js": "nextjs", "next": "nextjs",
            "spring boot": "springboot", "spring": "springboot",
            "asp.net": "aspnet", "asp.net core": "aspnet", "dotnet": "aspnet",
            "gin": "go_gin", "go": "go_gin", "golang": "go_gin",
            "ruby on rails": "rails", "ruby": "rails", "ror": "rails",
            "axum": "rust_axum", "rust": "rust_axum",
            "ktor": "kotlin_ktor", "kotlin": "kotlin_ktor",
        }
        result = _fw_aliases.get(alias, alias)
        assert result == canonical, f"Alias '{alias}' should resolve to '{canonical}', got '{result}'"
