"""Phase 10 tests: Cloud/Hosting Support (Gaps 183-192).

Covers 10 cloud platform configs:
- Vercel, AWS ECS, GCP Cloud Run, Railway, Azure App Service,
  DigitalOcean, Netlify, Fly.io, Render, Heroku

Tests validate: structure, frozen dataclass, registry, aliases,
categories, config files, deploy commands, rules quality,
feature flags, and edge cases.
"""

from __future__ import annotations

import pytest

from app.agents.cloud_configs import (
    CloudConfig,
    get_cloud_config,
    list_clouds,
)


# -- All 10 cloud platforms ---------------------------------------------------

ALL_CLOUDS = [
    "vercel", "aws_ecs", "gcp_cloud_run", "railway",
    "azure_app_service", "digitalocean", "netlify",
    "fly_io", "render", "heroku",
]

EXPECTED_DISPLAY_NAMES = {
    "vercel": "Vercel",
    "aws_ecs": "AWS ECS",
    "gcp_cloud_run": "GCP Cloud Run",
    "railway": "Railway",
    "azure_app_service": "Azure App Service",
    "digitalocean": "DigitalOcean App Platform",
    "netlify": "Netlify",
    "fly_io": "Fly.io",
    "render": "Render",
    "heroku": "Heroku",
}

EXPECTED_CATEGORIES = {
    "vercel": "serverless",
    "aws_ecs": "container",
    "gcp_cloud_run": "container",
    "railway": "paas",
    "azure_app_service": "container",
    "digitalocean": "paas",
    "netlify": "serverless",
    "fly_io": "container",
    "render": "paas",
    "heroku": "paas",
}

FREE_TIER_PLATFORMS = {"vercel", "railway", "netlify", "render", "gcp_cloud_run"}
PAID_ONLY_PLATFORMS = {"aws_ecs", "azure_app_service", "digitalocean", "fly_io", "heroku"}

VALID_CATEGORIES = {"serverless", "container", "paas", "static"}


# -- TestAllCloudsRegistered --------------------------------------------------


class TestAllCloudsRegistered:
    """Verify all 10 cloud platforms are registered."""

    def test_list_returns_all_10(self):
        clouds = list_clouds()
        assert len(clouds) >= 10, f"Expected >= 10, got {len(clouds)}: {clouds}"

    @pytest.mark.parametrize("name", ALL_CLOUDS)
    def test_cloud_exists(self, name: str):
        config = get_cloud_config(name)
        assert config.name == name

    @pytest.mark.parametrize("name", ALL_CLOUDS)
    def test_cloud_is_correct_type(self, name: str):
        config = get_cloud_config(name)
        assert isinstance(config, CloudConfig)


# -- TestCloudConfigFrozen ----------------------------------------------------


class TestCloudConfigFrozen:
    """CloudConfig must be immutable (frozen dataclass)."""

    @pytest.mark.parametrize("name", ALL_CLOUDS)
    def test_frozen_name(self, name: str):
        config = get_cloud_config(name)
        with pytest.raises(Exception):
            config.name = "hacked"  # type: ignore[misc]

    @pytest.mark.parametrize("name", ALL_CLOUDS)
    def test_frozen_category(self, name: str):
        config = get_cloud_config(name)
        with pytest.raises(Exception):
            config.category = "hacked"  # type: ignore[misc]

    def test_frozen_deploy_command(self):
        config = get_cloud_config("vercel")
        with pytest.raises(Exception):
            config.deploy_command = "rm -rf /"  # type: ignore[misc]


# -- TestCloudCategories ------------------------------------------------------


class TestCloudCategories:
    """All clouds must have correct categories."""

    @pytest.mark.parametrize("name,category", EXPECTED_CATEGORIES.items())
    def test_category_correct(self, name: str, category: str):
        config = get_cloud_config(name)
        assert config.category == category, \
            f"{name} category is '{config.category}', expected '{category}'"

    @pytest.mark.parametrize("name", ALL_CLOUDS)
    def test_category_is_valid(self, name: str):
        config = get_cloud_config(name)
        assert config.category in VALID_CATEGORIES, \
            f"{name} has invalid category '{config.category}'"


# -- TestCloudDisplayNames ----------------------------------------------------


class TestCloudDisplayNames:
    """Display names must be present and meaningful."""

    @pytest.mark.parametrize("name", ALL_CLOUDS)
    def test_display_name_non_empty(self, name: str):
        config = get_cloud_config(name)
        assert len(config.display_name) >= 3, \
            f"{name} display_name too short: '{config.display_name}'"

    @pytest.mark.parametrize("name,expected", EXPECTED_DISPLAY_NAMES.items())
    def test_display_name_matches(self, name: str, expected: str):
        config = get_cloud_config(name)
        assert config.display_name == expected, \
            f"{name} display_name is '{config.display_name}', expected '{expected}'"


# -- TestCloudSupportedFrameworks ---------------------------------------------


class TestCloudSupportedFrameworks:
    """Every cloud must declare supported frameworks."""

    @pytest.mark.parametrize("name", ALL_CLOUDS)
    def test_supported_frameworks_non_empty(self, name: str):
        config = get_cloud_config(name)
        assert len(config.supported_frameworks) >= 3, \
            f"{name} has only {len(config.supported_frameworks)} supported frameworks"

    @pytest.mark.parametrize("name", ALL_CLOUDS)
    def test_supported_frameworks_is_tuple(self, name: str):
        config = get_cloud_config(name)
        assert isinstance(config.supported_frameworks, tuple), \
            f"{name} supported_frameworks is not a tuple"

    @pytest.mark.parametrize("name", ALL_CLOUDS)
    def test_supported_frameworks_are_strings(self, name: str):
        config = get_cloud_config(name)
        for fw in config.supported_frameworks:
            assert isinstance(fw, str)
            assert len(fw) >= 2, f"{name} has empty framework entry"


# -- TestCloudConfigFiles -----------------------------------------------------


class TestCloudConfigFiles:
    """Every cloud must have at least one config file template."""

    @pytest.mark.parametrize("name", ALL_CLOUDS)
    def test_config_files_non_empty(self, name: str):
        config = get_cloud_config(name)
        assert len(config.config_files) >= 1, \
            f"{name} has no config files"

    @pytest.mark.parametrize("name", ALL_CLOUDS)
    def test_config_files_are_dicts(self, name: str):
        config = get_cloud_config(name)
        assert isinstance(config.config_files, dict)

    @pytest.mark.parametrize("name", ALL_CLOUDS)
    def test_config_file_content_substantial(self, name: str):
        config = get_cloud_config(name)
        for filename, content in config.config_files.items():
            assert isinstance(filename, str)
            assert len(filename) >= 3, f"{name} config file name too short: '{filename}'"
            assert isinstance(content, str)
            assert len(content) >= 30, \
                f"{name}/{filename} content too short ({len(content)} chars)"

    @pytest.mark.parametrize("name", ALL_CLOUDS)
    def test_config_files_have_multiple_lines(self, name: str):
        config = get_cloud_config(name)
        for filename, content in config.config_files.items():
            lines = [line for line in content.strip().split("\n") if line.strip()]
            assert len(lines) >= 3, \
                f"{name}/{filename} has only {len(lines)} non-empty lines"


# -- TestCloudDeployCommands --------------------------------------------------


class TestCloudDeployCommands:
    """Every cloud must have a deploy command."""

    @pytest.mark.parametrize("name", ALL_CLOUDS)
    def test_deploy_command_non_empty(self, name: str):
        config = get_cloud_config(name)
        assert isinstance(config.deploy_command, str)
        assert len(config.deploy_command) >= 5, \
            f"{name} deploy_command too short: '{config.deploy_command}'"


# -- TestCloudEnvironmentVariables --------------------------------------------


class TestCloudEnvironmentVariables:
    """Every cloud must declare required environment variables."""

    @pytest.mark.parametrize("name", ALL_CLOUDS)
    def test_env_vars_non_empty(self, name: str):
        config = get_cloud_config(name)
        assert len(config.environment_variables) >= 2, \
            f"{name} has only {len(config.environment_variables)} env vars"

    @pytest.mark.parametrize("name", ALL_CLOUDS)
    def test_env_vars_are_uppercase(self, name: str):
        config = get_cloud_config(name)
        for var in config.environment_variables:
            assert var == var.upper(), \
                f"{name} env var '{var}' should be uppercase"

    @pytest.mark.parametrize("name", ALL_CLOUDS)
    def test_env_vars_is_tuple(self, name: str):
        config = get_cloud_config(name)
        assert isinstance(config.environment_variables, tuple)


# -- TestCloudRules -----------------------------------------------------------


class TestCloudRules:
    """Verify rules quality for all cloud platforms."""

    @pytest.mark.parametrize("name", ALL_CLOUDS)
    def test_has_minimum_3_rules(self, name: str):
        config = get_cloud_config(name)
        assert len(config.rules) >= 3, \
            f"{name} has only {len(config.rules)} rules"

    @pytest.mark.parametrize("name", ALL_CLOUDS)
    def test_rules_are_substantial(self, name: str):
        config = get_cloud_config(name)
        for i, rule in enumerate(config.rules):
            assert isinstance(rule, str)
            assert len(rule) > 20, \
                f"{name} rule {i} too short ({len(rule)} chars): '{rule[:40]}...'"

    @pytest.mark.parametrize("name", ALL_CLOUDS)
    def test_rules_is_tuple(self, name: str):
        config = get_cloud_config(name)
        assert isinstance(config.rules, tuple), f"{name} rules is not a tuple"

    @pytest.mark.parametrize("name", ALL_CLOUDS)
    def test_has_at_least_5_rules(self, name: str):
        config = get_cloud_config(name)
        assert len(config.rules) >= 5, \
            f"{name} has only {len(config.rules)} rules, expected >= 5"


# -- TestCloudFreeTier --------------------------------------------------------


class TestCloudFreeTier:
    """Validate free_tier flags for all platforms."""

    @pytest.mark.parametrize("name", sorted(FREE_TIER_PLATFORMS))
    def test_free_tier_true(self, name: str):
        config = get_cloud_config(name)
        assert config.free_tier is True, \
            f"{name} should have free_tier=True"

    @pytest.mark.parametrize("name", sorted(PAID_ONLY_PLATFORMS))
    def test_paid_only_no_free_tier(self, name: str):
        config = get_cloud_config(name)
        assert config.free_tier is False, \
            f"{name} should have free_tier=False"


# -- TestCloudPreviewSupport --------------------------------------------------


class TestCloudPreviewSupport:
    """Validate supports_preview flags."""

    PREVIEW_PLATFORMS = [
        "vercel", "gcp_cloud_run", "railway", "azure_app_service",
        "digitalocean", "netlify", "render", "heroku",
    ]

    @pytest.mark.parametrize("name", PREVIEW_PLATFORMS)
    def test_supports_preview(self, name: str):
        config = get_cloud_config(name)
        assert config.supports_preview is True, \
            f"{name} should support preview deployments"

    @pytest.mark.parametrize("name", ALL_CLOUDS)
    def test_supports_preview_is_bool(self, name: str):
        config = get_cloud_config(name)
        assert isinstance(config.supports_preview, bool)

    @pytest.mark.parametrize("name", ALL_CLOUDS)
    def test_supports_custom_domain_is_bool(self, name: str):
        config = get_cloud_config(name)
        assert isinstance(config.supports_custom_domain, bool)


# -- TestCloudAliases ---------------------------------------------------------


class TestCloudAliases:
    """Test alias resolution for cloud platforms."""

    ALIAS_TESTS = [
        ("aws", "aws_ecs"),
        ("ecs", "aws_ecs"),
        ("gcp", "gcp_cloud_run"),
        ("cloud_run", "gcp_cloud_run"),
        ("cloudrun", "gcp_cloud_run"),
        ("azure", "azure_app_service"),
        ("app_service", "azure_app_service"),
        ("do", "digitalocean"),
        ("digital_ocean", "digitalocean"),
        ("fly", "fly_io"),
        ("flyio", "fly_io"),
    ]

    @pytest.mark.parametrize("alias,canonical", ALIAS_TESTS)
    def test_alias_resolves(self, alias: str, canonical: str):
        config = get_cloud_config(alias)
        assert config.name == canonical, \
            f"Alias '{alias}' resolved to '{config.name}', expected '{canonical}'"


# -- TestCloudEdgeCases -------------------------------------------------------


class TestCloudEdgeCases:
    """Edge cases: unknown names, empty strings, normalization."""

    def test_unknown_config_raises_key_error(self):
        with pytest.raises(KeyError):
            get_cloud_config("nonexistent_cloud_xyz")

    def test_empty_string_raises_key_error(self):
        with pytest.raises(KeyError):
            get_cloud_config("")

    def test_case_insensitive_lookup(self):
        config = get_cloud_config("VERCEL")
        assert config.name == "vercel"

    def test_hyphen_normalization(self):
        config = get_cloud_config("fly-io")
        assert config.name == "fly_io"

    def test_space_normalization(self):
        config = get_cloud_config("fly io")
        assert config.name == "fly_io"

    def test_list_clouds_returns_sorted(self):
        clouds = list_clouds()
        assert clouds == sorted(clouds), "list_clouds() should return sorted names"

    def test_list_clouds_all_strings(self):
        clouds = list_clouds()
        for name in clouds:
            assert isinstance(name, str)
            assert len(name) >= 3


# -- TestCloudCustomDomain ---------------------------------------------------


class TestCloudCustomDomain:
    """All platforms should support custom domains."""

    @pytest.mark.parametrize("name", ALL_CLOUDS)
    def test_supports_custom_domain(self, name: str):
        config = get_cloud_config(name)
        assert config.supports_custom_domain is True, \
            f"{name} should support custom domains"
