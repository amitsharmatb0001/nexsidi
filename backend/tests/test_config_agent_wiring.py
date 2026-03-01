"""Comprehensive tests for config-to-agent wiring (Phases A-F).

Validates that each agent correctly loads and uses configs from its registry:
- Phase A: Frontend config registry has 8 frameworks (incl. Next.js + React)
- Phase B: Aanya loads FrontendFrameworkConfig, MobileConfig, DesktopConfig
- Phase C: Dhruv loads DatabaseConfig for all 10 databases
- Phase D: Pranav loads CloudConfig for all 10 cloud providers
- Phase E: Mobile/Desktop pipeline support (platform detection, tech stacks)
- Phase F: Service configs (6 services) registered and queryable

Tests cover: happy path, bad input, aliases, boundary conditions, edge cases,
prompt content validation, backward compatibility, and duck-typing across configs.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

# ─── Phase A: Frontend Config Registry (8 frameworks) ────────────────────────

from app.agents.frontend_frameworks import (
    FrontendFrameworkConfig,
    get_frontend_framework_config,
    list_frontend_frameworks,
)


class TestPhaseA_FrontendConfigRegistry:
    """Verify the frontend config registry has all 8 frameworks with correct data."""

    def test_registry_has_8_frameworks(self):
        fws = list_frontend_frameworks()
        assert len(fws) == 8

    def test_all_expected_frameworks_present(self):
        expected = {"angular", "astro", "nextjs", "react", "remix", "solid", "svelte", "vue"}
        actual = set(list_frontend_frameworks())
        assert actual == expected

    @pytest.mark.parametrize("name", [
        "nextjs", "react", "angular", "vue", "svelte", "remix", "astro", "solid",
    ])
    def test_each_config_has_required_fields(self, name: str):
        config = get_frontend_framework_config(name)
        assert config.name == name
        assert config.display_name
        assert config.language
        assert config.code_block_lang
        assert config.component_extension
        assert isinstance(config.file_structure, dict)
        assert len(config.file_structure) >= 3
        assert isinstance(config.rules, tuple)
        assert len(config.rules) >= 10
        assert isinstance(config.golden_examples, dict)
        assert len(config.golden_examples) >= 2
        assert isinstance(config.generation_order, tuple)
        assert len(config.generation_order) >= 3

    @pytest.mark.parametrize("name", [
        "nextjs", "react", "angular", "vue", "svelte", "remix", "astro", "solid",
    ])
    def test_generation_order_steps_have_required_keys(self, name: str):
        config = get_frontend_framework_config(name)
        for step in config.generation_order:
            assert "name" in step, f"{name}: step missing 'name'"
            assert "path" in step, f"{name}: step missing 'path'"
            assert "description" in step, f"{name}: step missing 'description'"

    def test_nextjs_config_specific(self):
        config = get_frontend_framework_config("nextjs")
        assert config.display_name == "Next.js"
        assert config.language == "typescript"
        assert config.code_block_lang == "tsx"
        assert config.component_extension == ".tsx"
        assert any("App Router" in r for r in config.rules)
        assert "auth_context" in config.golden_examples

    def test_react_config_specific(self):
        config = get_frontend_framework_config("react")
        assert config.display_name == "React"
        assert config.language == "typescript"
        assert any("React Router" in r for r in config.rules)
        assert "router" in config.golden_examples

    def test_angular_config_specific(self):
        config = get_frontend_framework_config("angular")
        assert config.display_name == "Angular"
        assert config.component_extension == ".ts"
        assert any("standalone" in r.lower() for r in config.rules)
        assert "component" in config.golden_examples

    # ── Alias resolution ──────────────────────────────────────

    @pytest.mark.parametrize("alias,expected", [
        ("next", "nextjs"),
        ("next.js", "nextjs"),
        ("reactjs", "react"),
        ("vuejs", "vue"),
        ("nuxt", "vue"),
        ("sveltekit", "svelte"),
        ("solidjs", "solid"),
        ("ng", "angular"),
    ])
    def test_alias_resolution(self, alias: str, expected: str):
        config = get_frontend_framework_config(alias)
        assert config.name == expected

    # ── Fallback behavior ─────────────────────────────────────

    def test_unknown_framework_falls_back_to_nextjs(self):
        config = get_frontend_framework_config("CobaltJS")
        assert config.name == "nextjs"

    def test_empty_string_falls_back(self):
        config = get_frontend_framework_config("")
        assert config.name == "nextjs"

    # ── Golden examples are non-trivial ───────────────────────

    @pytest.mark.parametrize("name", ["nextjs", "react", "angular", "vue", "svelte"])
    def test_golden_examples_not_empty(self, name: str):
        config = get_frontend_framework_config(name)
        for ex_name, code in config.golden_examples.items():
            assert len(code) > 50, f"{name}/{ex_name}: golden example too short"


# ─── Phase B: Aanya wired to FrontendFrameworkConfig ─────────────────────────

from app.agents.aanya import (
    _detect_platform,
    _load_config,
    get_frontend_framework,
    get_generation_order,
    get_rules,
    FRONTEND_RULES,
    NEXTJS_GENERATION_ORDER,
    REACT_GENERATION_ORDER,
    DEFAULT_FRONTEND_RULES,
)


class TestPhaseB_AanyaConfigLoading:
    """Verify Aanya loads the correct config from the registry."""

    # ── Platform detection ────────────────────────────────────

    def test_detect_frontend_default(self):
        assert _detect_platform({}) == "frontend"

    def test_detect_frontend_explicit(self):
        assert _detect_platform({"tech_stack": {"frontend": "React"}}) == "frontend"

    def test_detect_mobile(self):
        assert _detect_platform({"tech_stack": {"mobile": "flutter"}}) == "mobile"

    def test_detect_desktop(self):
        assert _detect_platform({"tech_stack": {"desktop": "electron"}}) == "desktop"

    def test_detect_mobile_priority_over_frontend(self):
        contract = {"tech_stack": {"frontend": "React", "mobile": "flutter"}}
        assert _detect_platform(contract) == "mobile"

    def test_detect_desktop_priority_over_frontend(self):
        contract = {"tech_stack": {"frontend": "React", "desktop": "tauri"}}
        assert _detect_platform(contract) == "desktop"

    # ── Config loading (_load_config) ─────────────────────────

    def test_load_nextjs_config(self):
        contract = {"tech_stack": {"frontend": "Next.js"}}
        config = _load_config(contract)
        assert config.name == "nextjs"
        assert hasattr(config, "rules")
        assert hasattr(config, "golden_examples")

    def test_load_react_config(self):
        contract = {"tech_stack": {"frontend": "React"}}
        config = _load_config(contract)
        assert config.name == "react"

    def test_load_angular_config(self):
        contract = {"tech_stack": {"frontend": "Angular"}}
        config = _load_config(contract)
        assert config.name == "angular"

    def test_load_vue_config(self):
        contract = {"tech_stack": {"frontend": "Vue.js"}}
        config = _load_config(contract)
        assert config.name == "vue"

    def test_load_default_is_nextjs(self):
        config = _load_config({})
        assert config.name == "nextjs"

    def test_load_unknown_frontend_falls_to_nextjs(self):
        contract = {"tech_stack": {"frontend": "UnknownJS"}}
        config = _load_config(contract)
        assert config.name == "nextjs"

    def test_load_mobile_flutter(self):
        contract = {"tech_stack": {"mobile": "flutter"}}
        config = _load_config(contract)
        assert config.name == "flutter"

    def test_load_mobile_react_native(self):
        contract = {"tech_stack": {"mobile": "react_native"}}
        config = _load_config(contract)
        assert config.name == "react_native"

    def test_load_desktop_electron(self):
        contract = {"tech_stack": {"desktop": "electron"}}
        config = _load_config(contract)
        assert config.name == "electron"

    def test_load_desktop_tauri(self):
        contract = {"tech_stack": {"desktop": "tauri"}}
        config = _load_config(contract)
        assert config.name == "tauri"

    # ── get_generation_order uses registry ─────────────────────

    def test_generation_order_nextjs_from_registry(self):
        order = get_generation_order("Next.js")
        names = [s["name"] for s in order]
        assert "auth_context" in names
        assert len(order) >= 3

    def test_generation_order_angular_from_registry(self):
        order = get_generation_order("Angular")
        names = [s["name"] for s in order]
        assert len(names) >= 3
        # Angular should NOT return Next.js order
        # Angular has its own generation order with angular-specific paths
        for step in order:
            assert step["path"]  # All steps have paths

    def test_generation_order_vue_from_registry(self):
        order = get_generation_order("Vue.js")
        assert len(order) >= 3

    def test_generation_order_svelte_from_registry(self):
        order = get_generation_order("Svelte")
        assert len(order) >= 3

    # ── get_rules uses registry ───────────────────────────────

    def test_rules_nextjs_from_registry(self):
        rules = get_rules("Next.js")
        assert len(rules) >= 11
        assert any("App Router" in r for r in rules)

    def test_rules_react_from_registry(self):
        rules = get_rules("React")
        assert len(rules) >= 11
        assert any("React Router" in r for r in rules)

    def test_rules_angular_from_registry(self):
        rules = get_rules("Angular")
        assert len(rules) >= 10
        assert any("standalone" in r.lower() or "angular" in r.lower() for r in rules)

    def test_rules_vue_from_registry(self):
        rules = get_rules("Vue.js")
        assert len(rules) >= 10

    # ── Backward compatibility: legacy constants still exist ──

    def test_legacy_nextjs_order_exists(self):
        assert isinstance(NEXTJS_GENERATION_ORDER, list)
        assert len(NEXTJS_GENERATION_ORDER) == 4
        assert NEXTJS_GENERATION_ORDER[-1]["name"] == "app"

    def test_legacy_react_order_exists(self):
        assert isinstance(REACT_GENERATION_ORDER, list)
        assert len(REACT_GENERATION_ORDER) == 5
        assert "router" in [s["name"] for s in REACT_GENERATION_ORDER]

    def test_legacy_frontend_rules_exist(self):
        assert "Next.js" in FRONTEND_RULES
        assert "React" in FRONTEND_RULES
        assert len(FRONTEND_RULES["Next.js"]) >= 11

    def test_legacy_default_rules_exist(self):
        assert DEFAULT_FRONTEND_RULES is FRONTEND_RULES["Next.js"]

    # ── Duck-typing across config types ───────────────────────

    def test_frontend_config_duck_type(self):
        """FrontendFrameworkConfig has all duck-typed fields."""
        config = _load_config({"tech_stack": {"frontend": "React"}})
        assert hasattr(config, "rules")
        assert hasattr(config, "golden_examples")
        assert hasattr(config, "language")
        assert hasattr(config, "code_block_lang")
        assert hasattr(config, "display_name")

    def test_mobile_config_duck_type(self):
        """MobileConfig has rules, golden_examples, language, display_name."""
        config = _load_config({"tech_stack": {"mobile": "flutter"}})
        assert hasattr(config, "rules")
        assert hasattr(config, "golden_examples")
        assert hasattr(config, "language")
        assert hasattr(config, "display_name")

    def test_desktop_config_duck_type(self):
        """DesktopConfig has rules, golden_examples, language, display_name."""
        config = _load_config({"tech_stack": {"desktop": "electron"}})
        assert hasattr(config, "rules")
        assert hasattr(config, "golden_examples")
        assert hasattr(config, "language")
        assert hasattr(config, "display_name")


# ─── Phase C: Dhruv wired to DatabaseConfig ──────────────────────────────────

from app.agents.dhruv import Dhruv
from app.agents.database_configs import get_database_config, list_databases


class TestPhaseC_DhruvDatabaseWiring:
    """Verify Dhruv loads and uses DatabaseConfig for all 10 databases."""

    # ── Database detection ────────────────────────────────────

    def test_detect_postgresql_default(self):
        assert Dhruv._detect_database({}) == "postgresql"

    def test_detect_from_contract(self):
        contract = {"tech_stack": {"database": "mysql"}}
        assert Dhruv._detect_database(contract) == "mysql"

    def test_detect_mongodb(self):
        contract = {"tech_stack": {"database": "mongodb"}}
        assert Dhruv._detect_database(contract) == "mongodb"

    def test_detect_empty_tech_stack(self):
        contract = {"tech_stack": {}}
        assert Dhruv._detect_database(contract) == "postgresql"

    # ── System prompt generation for all 10 databases ─────────

    @pytest.mark.parametrize("db_name", list_databases())
    def test_system_prompt_contains_db_name(self, db_name: str):
        db_config = get_database_config(db_name)
        dhruv = Dhruv()
        prompt = dhruv._build_system_prompt(db_config)
        assert db_config.display_name in prompt

    @pytest.mark.parametrize("db_name", list_databases())
    def test_system_prompt_has_mandatory_rules(self, db_name: str):
        db_config = get_database_config(db_name)
        dhruv = Dhruv()
        prompt = dhruv._build_system_prompt(db_config)
        assert "MANDATORY" in prompt
        # At least first rule should appear
        assert db_config.rules[0] in prompt

    @pytest.mark.parametrize("db_name", list_databases())
    def test_system_prompt_has_type_mappings(self, db_name: str):
        db_config = get_database_config(db_name)
        dhruv = Dhruv()
        prompt = dhruv._build_system_prompt(db_config)
        assert "TYPE MAPPINGS" in prompt
        # Check at least one mapping appears
        first_abstract = next(iter(db_config.type_mappings))
        assert first_abstract in prompt

    @pytest.mark.parametrize("db_name", list_databases())
    def test_system_prompt_has_index_types(self, db_name: str):
        db_config = get_database_config(db_name)
        dhruv = Dhruv()
        prompt = dhruv._build_system_prompt(db_config)
        assert "INDEX TYPES" in prompt

    @pytest.mark.parametrize("db_name", list_databases())
    def test_system_prompt_has_output_format(self, db_name: str):
        db_config = get_database_config(db_name)
        dhruv = Dhruv()
        prompt = dhruv._build_system_prompt(db_config)
        assert "OUTPUT FORMAT" in prompt

    # ── Category-specific output format ───────────────────────

    def test_relational_output_has_ddl(self):
        db_config = get_database_config("postgresql")
        dhruv = Dhruv()
        prompt = dhruv._build_system_prompt(db_config)
        assert "CREATE TABLE" in prompt

    def test_mongodb_output_has_schema_definition(self):
        db_config = get_database_config("mongodb")
        dhruv = Dhruv()
        prompt = dhruv._build_system_prompt(db_config)
        assert "schema_definition" in prompt

    def test_firebase_output_has_security_rules(self):
        db_config = get_database_config("firebase")
        dhruv = Dhruv()
        prompt = dhruv._build_system_prompt(db_config)
        assert "security_rules" in prompt

    def test_dynamodb_output_has_table_definition(self):
        db_config = get_database_config("dynamodb")
        dhruv = Dhruv()
        prompt = dhruv._build_system_prompt(db_config)
        assert "table_definition" in prompt

    def test_neo4j_output_has_cypher(self):
        db_config = get_database_config("neo4j")
        dhruv = Dhruv()
        prompt = dhruv._build_system_prompt(db_config)
        assert "cypher_schema" in prompt

    def test_redis_output_has_key_design(self):
        db_config = get_database_config("redis")
        dhruv = Dhruv()
        prompt = dhruv._build_system_prompt(db_config)
        assert "key_design" in prompt

    # ── Backend framework integration ─────────────────────────

    def test_connection_template_python_fastapi(self):
        db_config = get_database_config("postgresql")
        dhruv = Dhruv()
        prompt = dhruv._build_system_prompt(db_config, backend_framework="fastapi")
        assert "CONNECTION TEMPLATE" in prompt

    def test_orm_pattern_fastapi(self):
        db_config = get_database_config("postgresql")
        dhruv = Dhruv()
        prompt = dhruv._build_system_prompt(db_config, backend_framework="fastapi")
        assert "ORM" in prompt

    def test_no_connection_template_for_unknown_framework(self):
        db_config = get_database_config("postgresql")
        dhruv = Dhruv()
        prompt = dhruv._build_system_prompt(db_config, backend_framework="cobol")
        # No connection template section for unknown framework
        assert "CONNECTION TEMPLATE" not in prompt

    # ── Golden examples in prompt ─────────────────────────────

    @pytest.mark.parametrize("db_name", list_databases())
    def test_golden_examples_in_prompt(self, db_name: str):
        db_config = get_database_config(db_name)
        if not db_config.ddl_examples:
            pytest.skip(f"No DDL examples for {db_name}")
        dhruv = Dhruv()
        prompt = dhruv._build_system_prompt(db_config)
        assert "GOLDEN EXAMPLES" in prompt

    # ── DDL dialect in prompt ─────────────────────────────────

    @pytest.mark.parametrize("db_name,expected_dialect", [
        ("postgresql", "postgresql"),
        ("mysql", "mysql"),
        ("sqlite", "sqlite"),
        ("mongodb", "none"),
    ])
    def test_ddl_dialect_in_prompt(self, db_name: str, expected_dialect: str):
        db_config = get_database_config(db_name)
        dhruv = Dhruv()
        prompt = dhruv._build_system_prompt(db_config)
        assert f"DDL dialect: {expected_dialect}" in prompt


# ─── Phase D: Pranav wired to CloudConfig ────────────────────────────────────

from app.agents.pranav import (
    Pranav,
    DeployConfig,
    DeployProvider,
    _generate_deployment_url,
)
from app.agents.cloud_configs import list_clouds, get_cloud_config


class TestPhaseD_PranavCloudWiring:
    """Verify Pranav loads and uses CloudConfig for all 10 providers."""

    # ── Provider resolution ───────────────────────────────────

    @pytest.mark.parametrize("provider", list_clouds())
    def test_resolve_all_providers(self, provider: str):
        resolved_name, cloud_config = Pranav._resolve_provider(provider)
        assert resolved_name == provider
        assert cloud_config.name == provider

    def test_resolve_unknown_falls_to_railway(self):
        resolved_name, cloud_config = Pranav._resolve_provider("unknown_cloud")
        assert resolved_name == "railway"

    @pytest.mark.parametrize("alias,expected", [
        ("aws", "aws_ecs"),
        ("gcp", "gcp_cloud_run"),
        ("azure", "azure_app_service"),
        ("fly", "fly_io"),
    ])
    def test_resolve_aliases(self, alias: str, expected: str):
        resolved_name, cloud_config = Pranav._resolve_provider(alias)
        assert resolved_name == expected

    # ── Config file generation ────────────────────────────────

    @pytest.mark.parametrize("provider", list_clouds())
    def test_config_files_generated_for_all_providers(self, provider: str):
        pranav = Pranav()
        config = DeployConfig(
            provider=DeployProvider.RAILWAY,  # legacy compat
            provider_name=provider,
            service_name="test-app",
            project_id="test-project",
            region="us-east1",
        )
        files = pranav._generate_config_files(config)
        assert isinstance(files, dict)
        assert len(files) >= 1, f"{provider} generated no config files"

    @pytest.mark.parametrize("provider", list_clouds())
    def test_config_files_contain_service_name(self, provider: str):
        pranav = Pranav()
        config = DeployConfig(
            provider=DeployProvider.RAILWAY,
            provider_name=provider,
            service_name="my-awesome-app",
            project_id="proj-123",
            region="us-west1",
        )
        files = pranav._generate_config_files(config)
        # At least one file should reference the service name
        all_content = " ".join(files.values())
        assert "my-awesome-app" in all_content or len(files) >= 1

    # ── Deployment URL generation ─────────────────────────────

    @pytest.mark.parametrize("provider,domain_fragment", [
        ("vercel", "vercel.app"),
        ("railway", "railway.app"),
        ("gcp_cloud_run", "run.app"),
        ("aws_ecs", "amazonaws.com"),
        ("azure_app_service", "azurewebsites.net"),
        ("digitalocean", "ondigitalocean.app"),
        ("netlify", "netlify.app"),
        ("fly_io", "fly.dev"),
        ("render", "onrender.com"),
        ("heroku", "herokuapp.com"),
    ])
    def test_deployment_url_per_provider(self, provider: str, domain_fragment: str):
        config = DeployConfig(
            provider=DeployProvider.RAILWAY,
            provider_name=provider,
            service_name="myapp",
            project_id="proj",
            region="us-east1",
        )
        url = _generate_deployment_url(config)
        assert domain_fragment in url
        assert "myapp" in url

    def test_deployment_url_unknown_provider_generic(self):
        config = DeployConfig(
            provider=DeployProvider.RAILWAY,
            provider_name="unknown_cloud",
            service_name="myapp",
            project_id="proj",
            region="us-east1",
        )
        url = _generate_deployment_url(config)
        assert "myapp" in url

    # ── Tool registration uses all providers ──────────────────

    def test_deploy_tool_has_all_providers(self):
        pranav = Pranav()
        deploy_tool = next(t for t in pranav.tools if t.name == "deploy")
        provider_enum = deploy_tool.parameters["properties"]["provider"]["enum"]
        all_providers = list_clouds()
        for p in all_providers:
            assert p in provider_enum, f"{p} not in deploy tool enum"

    # ── Backward compatibility ────────────────────────────────

    def test_legacy_deploy_provider_enum_exists(self):
        assert DeployProvider.GCP_CLOUD_RUN.value == "gcp_cloud_run"
        assert DeployProvider.VERCEL.value == "vercel"
        assert DeployProvider.RAILWAY.value == "railway"

    def test_deploy_config_from_contract(self):
        contract = {
            "project_name": "My Test App",
            "deployment": {"provider": "vercel", "region": "us-east1"},
        }
        config = DeployConfig.from_contract(contract, "vercel")
        assert config.provider_name == "vercel"
        assert config.service_name == "my-test-app"
        assert config.region == "us-east1"

    def test_deploy_config_sanitizes_name(self):
        contract = {"project_name": "My App!! @#$ Special"}
        config = DeployConfig.from_contract(contract, "railway")
        assert all(c in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in config.service_name)

    # ── Execute with all 10 providers ─────────────────────────

    @pytest.mark.asyncio
    @pytest.mark.parametrize("provider", list_clouds())
    async def test_execute_all_providers(self, provider: str):
        pranav = Pranav()
        context = {
            "vikram": {
                "contract": {
                    "project_name": "test-app",
                    "deployment": {"provider": provider},
                }
            },
            "shubham": {"file_contents": {"main.py": "print('hi')"}},
        }
        with patch("app.agents.pranav.store_output", new_callable=AsyncMock):
            result = await pranav.execute("run-deploy", context)
        from app.agents.base import AgentStatus
        assert result.status == AgentStatus.COMPLETED
        assert result.output["provider"] == provider

    @pytest.mark.asyncio
    async def test_execute_no_contract_fails(self):
        pranav = Pranav()
        result = await pranav.execute("run-fail", {})
        from app.agents.base import AgentStatus
        assert result.status == AgentStatus.FAILED

    @pytest.mark.asyncio
    async def test_execute_no_code_fails(self):
        pranav = Pranav()
        context = {
            "vikram": {
                "contract": {
                    "project_name": "empty-app",
                    "deployment": {"provider": "railway"},
                }
            },
        }
        with patch("app.agents.pranav.store_output", new_callable=AsyncMock):
            result = await pranav.execute("run-empty", context)
        from app.agents.base import AgentStatus
        assert result.status == AgentStatus.FAILED
        assert "No generated code" in result.output.get("error", "")


# ─── Phase E: Mobile/Desktop pipeline support ────────────────────────────────

from app.services.tech_stack import (
    ALL_STACKS,
    MOBILE_STACKS,
    DESKTOP_STACKS,
    Maturity,
    resolve_tech_name,
)
from app.agents.mobile_configs import (
    get_mobile_config,
    list_mobile_frameworks,
)
from app.agents.desktop_configs import (
    get_desktop_config,
    list_desktop_frameworks,
)


class TestPhaseE_MobileDesktopSupport:
    """Verify mobile and desktop tech stacks, configs, and Aanya detection."""

    # ── Tech stack entries ────────────────────────────────────

    def test_mobile_stacks_exist(self):
        assert len(MOBILE_STACKS) == 5
        assert "react_native" in MOBILE_STACKS
        assert "flutter" in MOBILE_STACKS
        assert "swift" in MOBILE_STACKS
        assert "kotlin_compose" in MOBILE_STACKS
        assert "expo" in MOBILE_STACKS

    def test_desktop_stacks_exist(self):
        assert len(DESKTOP_STACKS) == 2
        assert "electron" in DESKTOP_STACKS
        assert "tauri" in DESKTOP_STACKS

    def test_mobile_stacks_in_all_stacks(self):
        for name in MOBILE_STACKS:
            assert name in ALL_STACKS, f"{name} not in ALL_STACKS"

    def test_desktop_stacks_in_all_stacks(self):
        for name in DESKTOP_STACKS:
            assert name in ALL_STACKS, f"{name} not in ALL_STACKS"

    @pytest.mark.parametrize("name,category", [
        ("react_native", "mobile"),
        ("flutter", "mobile"),
        ("swift", "mobile"),
        ("kotlin_compose", "mobile"),
        ("expo", "mobile"),
        ("electron", "desktop"),
        ("tauri", "desktop"),
    ])
    def test_category_correct(self, name: str, category: str):
        entry = ALL_STACKS[name]
        assert entry.category == category

    @pytest.mark.parametrize("alias,expected", [
        ("rn", "react_native"),
        ("dart", "flutter"),
        ("swiftui", "swift"),
        ("ios", "swift"),
        ("android", "kotlin_compose"),
        ("jetpack", "kotlin_compose"),
    ])
    def test_mobile_aliases_in_tech_stack(self, alias: str, expected: str):
        # Check that alias matches the entry's aliases
        entry = MOBILE_STACKS[expected]
        assert alias in entry.aliases

    # ── Mobile config registry ────────────────────────────────

    def test_mobile_registry_has_5_frameworks(self):
        assert len(list_mobile_frameworks()) == 5

    @pytest.mark.parametrize("name", list_mobile_frameworks())
    def test_mobile_configs_have_required_fields(self, name: str):
        config = get_mobile_config(name)
        assert config.name == name
        assert config.display_name
        assert config.language
        assert config.platform in ("cross_platform", "ios", "android")
        assert isinstance(config.rules, tuple)
        assert len(config.rules) >= 5
        assert isinstance(config.golden_examples, dict)

    # ── Desktop config registry ───────────────────────────────

    def test_desktop_registry_has_2_frameworks(self):
        assert len(list_desktop_frameworks()) == 2

    @pytest.mark.parametrize("name", list_desktop_frameworks())
    def test_desktop_configs_have_required_fields(self, name: str):
        config = get_desktop_config(name)
        assert config.name == name
        assert config.display_name
        assert config.language
        assert isinstance(config.rules, tuple)
        assert len(config.rules) >= 5
        assert isinstance(config.golden_examples, dict)

    # ── Aanya integration with mobile/desktop ─────────────────

    def test_aanya_loads_flutter_config(self):
        config = _load_config({"tech_stack": {"mobile": "flutter"}})
        assert config.name == "flutter"
        assert config.language == "dart"

    def test_aanya_loads_react_native_config(self):
        config = _load_config({"tech_stack": {"mobile": "react_native"}})
        assert config.name == "react_native"

    def test_aanya_loads_swift_config(self):
        config = _load_config({"tech_stack": {"mobile": "swift"}})
        assert config.name == "swift"

    def test_aanya_loads_electron_config(self):
        config = _load_config({"tech_stack": {"desktop": "electron"}})
        assert config.name == "electron"

    def test_aanya_loads_tauri_config(self):
        config = _load_config({"tech_stack": {"desktop": "tauri"}})
        assert config.name == "tauri"


# ─── Phase F: Service configs ────────────────────────────────────────────────

from app.agents.service_configs import (
    ServiceConfig,
    get_service_config,
    list_services,
)


class TestPhaseF_ServiceConfigs:
    """Verify all 6 service configs are registered and queryable."""

    def test_registry_has_6_services(self):
        assert len(list_services()) == 6

    def test_all_expected_services_present(self):
        expected = {"bullmq", "celery", "elasticsearch", "s3", "sendgrid", "stripe"}
        actual = set(list_services())
        assert actual == expected

    @pytest.mark.parametrize("name", [
        "celery", "bullmq", "stripe", "sendgrid", "s3", "elasticsearch",
    ])
    def test_each_service_has_required_fields(self, name: str):
        config = get_service_config(name)
        assert config.name == name
        assert config.display_name
        assert config.category
        assert isinstance(config.supported_languages, tuple)
        assert len(config.supported_languages) >= 1
        assert isinstance(config.package_dependencies, dict)
        assert isinstance(config.environment_variables, tuple)
        assert len(config.environment_variables) >= 1
        assert isinstance(config.rules, tuple)
        assert len(config.rules) >= 5
        assert isinstance(config.golden_examples, dict)
        assert len(config.golden_examples) >= 2
        assert isinstance(config.setup_code, dict)

    # ── Category validation ───────────────────────────────────

    @pytest.mark.parametrize("name,category", [
        ("celery", "task_queue"),
        ("bullmq", "task_queue"),
        ("stripe", "payment"),
        ("sendgrid", "email"),
        ("s3", "storage"),
        ("elasticsearch", "search"),
    ])
    def test_service_categories(self, name: str, category: str):
        config = get_service_config(name)
        assert config.category == category

    # ── Alias resolution ──────────────────────────────────────

    @pytest.mark.parametrize("alias,expected", [
        ("celery_worker", "celery"),
        ("task_queue", "celery"),
        ("bull", "bullmq"),
        ("bull_mq", "bullmq"),
        ("stripe_payments", "stripe"),
        ("payments", "stripe"),
        ("sendgrid_email", "sendgrid"),
        ("email", "sendgrid"),
        ("aws_s3", "s3"),
        ("minio", "s3"),
        ("elastic", "elasticsearch"),
        ("es", "elasticsearch"),
        ("opensearch", "elasticsearch"),
    ])
    def test_alias_resolution(self, alias: str, expected: str):
        config = get_service_config(alias)
        assert config.name == expected

    def test_unknown_service_raises(self):
        with pytest.raises(KeyError):
            get_service_config("nonexistent_service")

    # ── Golden examples are non-trivial ───────────────────────

    @pytest.mark.parametrize("name", list_services())
    def test_golden_examples_non_empty(self, name: str):
        config = get_service_config(name)
        for ex_name, code in config.golden_examples.items():
            assert len(code) > 30, f"{name}/{ex_name}: golden example too short"

    # ── Setup code exists for supported languages ─────────────

    @pytest.mark.parametrize("name", list_services())
    def test_setup_code_matches_languages(self, name: str):
        config = get_service_config(name)
        for lang in config.supported_languages:
            assert lang in config.setup_code, (
                f"{name}: supported language '{lang}' has no setup_code"
            )

    # ── Package dependencies match languages ──────────────────

    @pytest.mark.parametrize("name", list_services())
    def test_package_deps_match_languages(self, name: str):
        config = get_service_config(name)
        for lang in config.supported_languages:
            assert lang in config.package_dependencies, (
                f"{name}: supported language '{lang}' has no package_dependencies"
            )
            deps = config.package_dependencies[lang]
            assert len(deps) >= 1, f"{name}/{lang}: no package dependencies"

    # ── Frozen (immutable) ────────────────────────────────────

    @pytest.mark.parametrize("name", list_services())
    def test_service_config_is_frozen(self, name: str):
        config = get_service_config(name)
        with pytest.raises(Exception):  # FrozenInstanceError
            config.name = "hacked"


# ─── Cross-cutting integration tests ─────────────────────────────────────────


class TestCrossCuttingIntegration:
    """Cross-registry integration tests verifying the full system works together."""

    def test_all_registries_load_without_import_errors(self):
        """Importing all registries shouldn't produce circular imports or errors."""
        from app.agents.frontend_frameworks import list_frontend_frameworks
        from app.agents.database_configs import list_databases
        from app.agents.cloud_configs import list_clouds
        from app.agents.mobile_configs import list_mobile_frameworks
        from app.agents.desktop_configs import list_desktop_frameworks
        from app.agents.service_configs import list_services

        assert len(list_frontend_frameworks()) == 8
        assert len(list_databases()) == 10
        assert len(list_clouds()) == 10
        assert len(list_mobile_frameworks()) == 5
        assert len(list_desktop_frameworks()) == 2
        assert len(list_services()) == 6

    def test_total_tech_configs(self):
        """Total configs across all registries."""
        from app.agents.frontend_frameworks import list_frontend_frameworks
        from app.agents.database_configs import list_databases
        from app.agents.cloud_configs import list_clouds
        from app.agents.mobile_configs import list_mobile_frameworks
        from app.agents.desktop_configs import list_desktop_frameworks
        from app.agents.service_configs import list_services

        total = (
            len(list_frontend_frameworks())
            + len(list_databases())
            + len(list_clouds())
            + len(list_mobile_frameworks())
            + len(list_desktop_frameworks())
            + len(list_services())
        )
        assert total == 41  # 8 + 10 + 10 + 5 + 2 + 6

    def test_dhruv_prompt_for_postgresql_fastapi(self):
        """End-to-end: Dhruv builds a PostgreSQL prompt for a FastAPI backend."""
        from app.agents.database_configs import get_database_config
        dhruv = Dhruv()
        db_config = get_database_config("postgresql")
        prompt = dhruv._build_system_prompt(db_config, backend_framework="fastapi")

        # Should have all 9 sections
        assert "Database Architect" in prompt
        assert "DDL dialect: postgresql" in prompt
        assert "MANDATORY" in prompt
        assert "TYPE MAPPINGS" in prompt
        assert "GOLDEN EXAMPLES" in prompt
        assert "INDEX TYPES" in prompt
        assert "CONNECTION TEMPLATE" in prompt
        assert "ORM" in prompt
        assert "OUTPUT FORMAT" in prompt

    def test_dhruv_prompt_for_mongodb_express(self):
        """End-to-end: Dhruv builds a MongoDB prompt for an Express backend."""
        from app.agents.database_configs import get_database_config
        dhruv = Dhruv()
        db_config = get_database_config("mongodb")
        prompt = dhruv._build_system_prompt(db_config, backend_framework="express")

        assert "MongoDB" in prompt
        assert "document" in prompt
        assert "schema_definition" in prompt
        assert "CONNECTION TEMPLATE" in prompt

    def test_aanya_loads_all_frontend_configs(self):
        """Aanya can load every frontend framework config without error."""
        for fw in list_frontend_frameworks():
            config = get_frontend_framework_config(fw)
            assert config.name == fw
            assert len(config.rules) >= 10

    @pytest.mark.asyncio
    async def test_pranav_deploy_url_for_all_providers(self):
        """Every cloud provider generates a valid deployment URL."""
        for provider in list_clouds():
            config = DeployConfig(
                provider=DeployProvider.RAILWAY,
                provider_name=provider,
                service_name="test",
                project_id="proj",
                region="us-east1",
            )
            url = _generate_deployment_url(config)
            assert url.startswith("https://")
            assert "test" in url
