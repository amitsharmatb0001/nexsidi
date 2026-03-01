"""Phase 3 tests: All frontend frameworks (Gaps 159-165).

Covers 6 frontend framework configs:
- Vue.js, Angular, SvelteKit, Remix, Astro, Solid.js

Tests are BRUTAL: structure validation, content quality, language correctness,
rule count, golden example presence, generation order, alias resolution.
"""

from __future__ import annotations

import pytest

from app.agents.frontend_frameworks import (
    FrontendFrameworkConfig,
    get_frontend_framework_config,
    list_frontend_frameworks,
)


# ── All 6 frontend frameworks ──────────────────────────────────────

ALL_FRONTEND_FRAMEWORKS = [
    "vue", "angular", "svelte", "remix", "astro", "solid",
]

EXPECTED_DISPLAY_NAMES = {
    "vue": "Vue",
    "angular": "Angular",
    "svelte": "Svelte",
    "remix": "Remix",
    "astro": "Astro",
    "solid": "Solid",
}

EXPECTED_EXTENSIONS = {
    "vue": ".vue",
    "angular": ".ts",
    "svelte": ".svelte",
    "remix": ".tsx",
    "astro": ".astro",
    "solid": ".tsx",
}


# ── TestAllFrontendFrameworksRegistered ─────────────────────────────


class TestAllFrontendFrameworksRegistered:
    """Verify all 6 frontend frameworks are registered."""

    def test_list_returns_all_6(self):
        frameworks = list_frontend_frameworks()
        assert len(frameworks) >= 6, f"Expected >= 6, got {len(frameworks)}: {frameworks}"

    @pytest.mark.parametrize("name", ALL_FRONTEND_FRAMEWORKS)
    def test_framework_exists(self, name: str):
        config = get_frontend_framework_config(name)
        assert config.name == name

    @pytest.mark.parametrize("name", ALL_FRONTEND_FRAMEWORKS)
    def test_framework_is_frozen(self, name: str):
        config = get_frontend_framework_config(name)
        with pytest.raises(Exception):
            config.name = "hacked"  # type: ignore[misc]

    @pytest.mark.parametrize("name", ALL_FRONTEND_FRAMEWORKS)
    def test_framework_is_correct_type(self, name: str):
        config = get_frontend_framework_config(name)
        assert isinstance(config, FrontendFrameworkConfig)


# ── TestFrontendLanguages ──────────────────────────────────────────


class TestFrontendLanguages:
    """All frontend frameworks use TypeScript."""

    @pytest.mark.parametrize("name", ALL_FRONTEND_FRAMEWORKS)
    def test_language_is_typescript(self, name: str):
        config = get_frontend_framework_config(name)
        assert config.language == "typescript"

    @pytest.mark.parametrize("name,ext", EXPECTED_EXTENSIONS.items())
    def test_component_extension_correct(self, name: str, ext: str):
        config = get_frontend_framework_config(name)
        assert config.component_extension == ext

    @pytest.mark.parametrize("name", ALL_FRONTEND_FRAMEWORKS)
    def test_code_block_lang_is_string(self, name: str):
        config = get_frontend_framework_config(name)
        assert isinstance(config.code_block_lang, str)
        assert len(config.code_block_lang) >= 2


# ── TestFrontendRules ──────────────────────────────────────────────


class TestFrontendRules:
    """Verify rules quality for all frontend frameworks."""

    @pytest.mark.parametrize("name", ALL_FRONTEND_FRAMEWORKS)
    def test_has_minimum_11_rules(self, name: str):
        config = get_frontend_framework_config(name)
        assert len(config.rules) >= 11, f"{name} has only {len(config.rules)} rules"

    @pytest.mark.parametrize("name", ALL_FRONTEND_FRAMEWORKS)
    def test_rules_are_substantial(self, name: str):
        config = get_frontend_framework_config(name)
        for i, rule in enumerate(config.rules):
            assert isinstance(rule, str)
            assert len(rule) >= 15, f"{name} rule {i} too short"

    @pytest.mark.parametrize("name", ALL_FRONTEND_FRAMEWORKS)
    def test_rules_mention_typescript(self, name: str):
        config = get_frontend_framework_config(name)
        rules_text = "\n".join(config.rules).lower()
        assert "typescript" in rules_text or "type" in rules_text


# ── TestFrontendGoldenExamples ─────────────────────────────────────


class TestFrontendGoldenExamples:
    """Verify golden examples for all frontend frameworks."""

    @pytest.mark.parametrize("name", ALL_FRONTEND_FRAMEWORKS)
    def test_has_minimum_3_examples(self, name: str):
        config = get_frontend_framework_config(name)
        assert len(config.golden_examples) >= 3, \
            f"{name} has only {len(config.golden_examples)} examples"

    @pytest.mark.parametrize("name", ALL_FRONTEND_FRAMEWORKS)
    def test_examples_are_substantial(self, name: str):
        config = get_frontend_framework_config(name)
        for step, code in config.golden_examples.items():
            assert len(code) >= 80, f"{name}/{step} example too short ({len(code)} chars)"

    @pytest.mark.parametrize("name", ALL_FRONTEND_FRAMEWORKS)
    def test_examples_have_multiple_lines(self, name: str):
        config = get_frontend_framework_config(name)
        for step, code in config.golden_examples.items():
            lines = [l for l in code.strip().split("\n") if l.strip()]
            assert len(lines) >= 5, f"{name}/{step} has only {len(lines)} lines"


# ── TestFrontendFileStructure ──────────────────────────────────────


class TestFrontendFileStructure:
    """Verify file structure maps are complete."""

    @pytest.mark.parametrize("name", ALL_FRONTEND_FRAMEWORKS)
    def test_has_minimum_4_paths(self, name: str):
        config = get_frontend_framework_config(name)
        assert len(config.file_structure) >= 4, \
            f"{name} has only {len(config.file_structure)} file structure entries"

    @pytest.mark.parametrize("name", ALL_FRONTEND_FRAMEWORKS)
    def test_paths_are_strings(self, name: str):
        config = get_frontend_framework_config(name)
        for step, path in config.file_structure.items():
            assert isinstance(path, str)
            assert len(path) > 3


# ── TestFrontendGenerationOrder ────────────────────────────────────


class TestFrontendGenerationOrder:
    """Verify generation orders are valid."""

    @pytest.mark.parametrize("name", ALL_FRONTEND_FRAMEWORKS)
    def test_has_minimum_3_steps(self, name: str):
        config = get_frontend_framework_config(name)
        assert len(config.generation_order) >= 3, \
            f"{name} has only {len(config.generation_order)} generation steps"

    @pytest.mark.parametrize("name", ALL_FRONTEND_FRAMEWORKS)
    def test_steps_have_required_fields(self, name: str):
        config = get_frontend_framework_config(name)
        for step in config.generation_order:
            assert "name" in step, f"{name}: step missing 'name'"
            assert "path" in step, f"{name}: step missing 'path'"
            assert "description" in step, f"{name}: step missing 'description'"

    @pytest.mark.parametrize("name", ALL_FRONTEND_FRAMEWORKS)
    def test_step_names_unique(self, name: str):
        config = get_frontend_framework_config(name)
        names = [s["name"] for s in config.generation_order]
        assert len(names) == len(set(names)), f"{name} has duplicate steps"


# ── TestFrontendSpecificContent ────────────────────────────────────


class TestFrontendSpecificContent:
    """Test framework-specific technology mentions."""

    def test_vue_mentions_composition_api(self):
        config = get_frontend_framework_config("vue")
        rules = "\n".join(config.rules).lower()
        assert "composition" in rules or "setup" in rules or "pinia" in rules

    def test_angular_mentions_component_or_service(self):
        config = get_frontend_framework_config("angular")
        rules = "\n".join(config.rules).lower()
        assert "component" in rules or "service" in rules or "injectable" in rules

    def test_svelte_mentions_svelte_concepts(self):
        config = get_frontend_framework_config("svelte")
        rules = "\n".join(config.rules).lower()
        assert "svelte" in rules or "store" in rules or "reactive" in rules

    def test_remix_mentions_loader_or_action(self):
        config = get_frontend_framework_config("remix")
        rules = "\n".join(config.rules).lower()
        assert "loader" in rules or "action" in rules

    def test_astro_mentions_island_or_astro(self):
        config = get_frontend_framework_config("astro")
        rules = "\n".join(config.rules).lower()
        assert "astro" in rules or "island" in rules or "client:" in rules

    def test_solid_mentions_signal(self):
        config = get_frontend_framework_config("solid")
        rules = "\n".join(config.rules).lower()
        assert "signal" in rules or "createsignal" in rules or "createeffect" in rules


# ── TestFrontendAliases ────────────────────────────────────────────


class TestFrontendAliases:
    """Test alias resolution for frontend frameworks."""

    ALIAS_TESTS = [
        ("vuejs", "vue"),
        ("nuxt", "vue"),
        ("nuxtjs", "vue"),
        ("ng", "angular"),
        ("sveltekit", "svelte"),
        ("solidjs", "solid"),
    ]

    @pytest.mark.parametrize("alias,canonical", ALIAS_TESTS)
    def test_alias_resolves(self, alias: str, canonical: str):
        config = get_frontend_framework_config(alias)
        assert config.name == canonical


# ── TestFrontendDisplayNames ───────────────────────────────────────


class TestFrontendDisplayNames:
    """Verify display names contain the framework name."""

    @pytest.mark.parametrize("name", ALL_FRONTEND_FRAMEWORKS)
    def test_display_name_contains_framework(self, name: str):
        config = get_frontend_framework_config(name)
        # Display name should contain something meaningful
        assert len(config.display_name) >= 3
        # At least the first few chars should match
        expected_part = EXPECTED_DISPLAY_NAMES.get(name, name.capitalize())
        assert expected_part.lower() in config.display_name.lower(), \
            f"{name} display_name '{config.display_name}' should contain '{expected_part}'"
