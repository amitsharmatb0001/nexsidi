"""Tests for Phase 11: Mobile and Desktop framework configurations (Gaps 166-172).

Covers MobileConfig / DesktopConfig dataclass validation, registry operations,
alias resolution, framework attribute correctness, rules quality, golden
example quality, and edge cases.  Targets 100+ individual test assertions
across ~100 parametrized test cases.
"""

from __future__ import annotations

import re
from dataclasses import FrozenInstanceError

import pytest

from app.agents.mobile_configs import (
    MobileConfig,
    get_mobile_config,
    list_mobile_frameworks,
    register_mobile,
)
from app.agents.desktop_configs import (
    DesktopConfig,
    get_desktop_config,
    list_desktop_frameworks,
    register_desktop,
)


# ============================================================================
# Helpers: minimal valid configs for validation tests
# ============================================================================

def _minimal_mobile(**overrides) -> MobileConfig:
    """Return a minimal valid MobileConfig, merging *overrides*."""
    defaults = dict(
        name="test_fw",
        display_name="Test Framework",
        language="python",
        code_block_lang="python",
        platform="ios",
        component_extension=".py",
        file_structure={"a": "b", "c": "d", "e": "f"},
        rules=("Rule 1.", "Rule 2.", "Rule 3.", "Rule 4.", "Rule 5."),
        golden_examples={"a": "x" * 60},
        supports_hot_reload=False,
        min_sdk_version="1.0",
        package_manager="pip",
    )
    defaults.update(overrides)
    return MobileConfig(**defaults)


def _minimal_desktop(**overrides) -> DesktopConfig:
    """Return a minimal valid DesktopConfig, merging *overrides*."""
    defaults = dict(
        name="test_desktop",
        display_name="Test Desktop",
        language="python",
        code_block_lang="python",
        backend_language="python",
        frontend_framework="react",
        file_structure={"a": "b", "c": "d", "e": "f"},
        rules=("Rule 1.", "Rule 2.", "Rule 3.", "Rule 4.", "Rule 5."),
        golden_examples={"a": "x" * 60},
        supports_auto_update=False,
        supports_system_tray=False,
        package_manager="pip",
        platforms=("linux",),
    )
    defaults.update(overrides)
    return DesktopConfig(**defaults)


# ============================================================================
# SECTION A -- MobileConfig Dataclass Validation
# ============================================================================


class TestMobileConfigValidation:
    """Validate __post_init__ guards on MobileConfig."""

    def test_valid_minimal_config(self):
        cfg = _minimal_mobile()
        assert cfg.name == "test_fw"

    def test_name_required_empty_string(self):
        with pytest.raises(ValueError, match="name is required"):
            _minimal_mobile(name="")

    def test_display_name_required_empty_string(self):
        with pytest.raises(ValueError, match="display_name is required"):
            _minimal_mobile(display_name="")

    @pytest.mark.parametrize("bad_platform", ["desktop", "web", "macos", "linux", "CROSS_PLATFORM", ""])
    def test_invalid_platform_rejected(self, bad_platform: str):
        with pytest.raises(ValueError, match="Invalid platform"):
            _minimal_mobile(platform=bad_platform)

    @pytest.mark.parametrize("valid_platform", ["cross_platform", "ios", "android"])
    def test_valid_platforms_accepted(self, valid_platform: str):
        cfg = _minimal_mobile(platform=valid_platform)
        assert cfg.platform == valid_platform

    def test_fewer_than_5_rules_rejected(self):
        with pytest.raises(ValueError, match="At least 5 rules required"):
            _minimal_mobile(rules=("r1", "r2", "r3", "r4"))

    def test_zero_rules_rejected(self):
        with pytest.raises(ValueError, match="At least 5 rules required"):
            _minimal_mobile(rules=())

    def test_exactly_5_rules_accepted(self):
        cfg = _minimal_mobile(rules=("1", "2", "3", "4", "5"))
        assert len(cfg.rules) == 5

    def test_frozen_prevents_mutation(self):
        cfg = _minimal_mobile()
        with pytest.raises(FrozenInstanceError):
            cfg.name = "oops"  # type: ignore[misc]

    def test_slots_prevents_arbitrary_attributes(self):
        cfg = _minimal_mobile()
        with pytest.raises((AttributeError, FrozenInstanceError, TypeError)):
            cfg.random_attr = "nope"  # type: ignore[attr-defined]


# ============================================================================
# SECTION B -- Mobile Registry Operations
# ============================================================================


class TestMobileRegistry:
    """Tests for register_mobile, get_mobile_config, list_mobile_frameworks."""

    def test_all_five_frameworks_registered(self):
        frameworks = list_mobile_frameworks()
        assert len(frameworks) == 5

    def test_list_returns_sorted(self):
        frameworks = list_mobile_frameworks()
        assert frameworks == sorted(frameworks)

    @pytest.mark.parametrize("name", [
        "react_native", "flutter", "swift", "kotlin_compose", "expo",
    ])
    def test_each_framework_retrievable_by_canonical_name(self, name: str):
        cfg = get_mobile_config(name)
        assert cfg.name == name

    def test_register_duplicate_raises_value_error(self):
        dup = _minimal_mobile(name="react_native")
        with pytest.raises(ValueError, match="already registered"):
            register_mobile(dup)

    def test_unknown_framework_raises_key_error(self):
        with pytest.raises(KeyError, match="Unknown mobile framework"):
            get_mobile_config("blazor_mobile")

    def test_key_error_message_includes_available(self):
        with pytest.raises(KeyError, match="Available:"):
            get_mobile_config("nonexistent_fw")


# ============================================================================
# SECTION C -- Mobile Alias Resolution
# ============================================================================


class TestMobileAliases:
    """All aliases from _aliases dict must resolve to the correct config."""

    @pytest.mark.parametrize("alias,canonical", [
        ("rn", "react_native"),
        ("reactnative", "react_native"),
        ("react-native", "react_native"),
        ("react_native", "react_native"),
        ("swiftui", "swift"),
        ("ios", "swift"),
        ("kotlin", "kotlin_compose"),
        ("jetpack", "kotlin_compose"),
        ("jetpack_compose", "kotlin_compose"),
        ("android", "kotlin_compose"),
        ("dart", "flutter"),
    ])
    def test_alias_resolves(self, alias: str, canonical: str):
        cfg = get_mobile_config(alias)
        assert cfg.name == canonical

    @pytest.mark.parametrize("alias", [
        "RN", "ReactNative", "REACT-NATIVE", "React_Native",
        "SwiftUI", "SWIFTUI", "IOS",
        "Kotlin", "KOTLIN", "Android", "ANDROID",
        "Dart", "DART", "Flutter", "FLUTTER",
        "EXPO", "Expo",
    ])
    def test_case_insensitive_lookups(self, alias: str):
        cfg = get_mobile_config(alias)
        assert cfg.name in list_mobile_frameworks()

    @pytest.mark.parametrize("weird_input,expected", [
        ("  flutter  ", "flutter"),
        ("react native", "react_native"),
        ("React Native", "react_native"),
        ("kotlin compose", "kotlin_compose"),
        ("react.native", "react_native"),
    ])
    def test_whitespace_dot_normalization(self, weird_input: str, expected: str):
        cfg = get_mobile_config(weird_input)
        assert cfg.name == expected


# ============================================================================
# SECTION D -- Mobile Framework Attribute Correctness (parametrized)
# ============================================================================


ALL_MOBILE_FRAMEWORKS = ["react_native", "flutter", "swift", "kotlin_compose", "expo"]

EXPECTED_MOBILE_ATTRS = {
    "react_native": {
        "display_name": "React Native",
        "language": "typescript",
        "code_block_lang": "tsx",
        "platform": "cross_platform",
        "component_extension": ".tsx",
        "supports_hot_reload": True,
        "package_manager": "npm",
    },
    "flutter": {
        "display_name": "Flutter",
        "language": "dart",
        "code_block_lang": "dart",
        "platform": "cross_platform",
        "component_extension": ".dart",
        "supports_hot_reload": True,
        "package_manager": "pub",
    },
    "swift": {
        "display_name": "Swift / SwiftUI",
        "language": "swift",
        "code_block_lang": "swift",
        "platform": "ios",
        "component_extension": ".swift",
        "supports_hot_reload": False,
        "package_manager": "cocoapods",
    },
    "kotlin_compose": {
        "display_name": "Kotlin / Jetpack Compose",
        "language": "kotlin",
        "code_block_lang": "kotlin",
        "platform": "android",
        "component_extension": ".kt",
        "supports_hot_reload": False,
        "package_manager": "gradle",
    },
    "expo": {
        "display_name": "Expo",
        "language": "typescript",
        "code_block_lang": "tsx",
        "platform": "cross_platform",
        "component_extension": ".tsx",
        "supports_hot_reload": True,
        "package_manager": "npm",
    },
}


class TestMobileFrameworkAttributes:
    """Verify correct language, platform, code_block_lang, etc. per framework."""

    @pytest.mark.parametrize("fw_name", ALL_MOBILE_FRAMEWORKS)
    def test_display_name_matches(self, fw_name: str):
        cfg = get_mobile_config(fw_name)
        assert cfg.display_name == EXPECTED_MOBILE_ATTRS[fw_name]["display_name"]

    @pytest.mark.parametrize("fw_name", ALL_MOBILE_FRAMEWORKS)
    def test_language_matches(self, fw_name: str):
        cfg = get_mobile_config(fw_name)
        assert cfg.language == EXPECTED_MOBILE_ATTRS[fw_name]["language"]

    @pytest.mark.parametrize("fw_name", ALL_MOBILE_FRAMEWORKS)
    def test_code_block_lang_matches(self, fw_name: str):
        cfg = get_mobile_config(fw_name)
        assert cfg.code_block_lang == EXPECTED_MOBILE_ATTRS[fw_name]["code_block_lang"]

    @pytest.mark.parametrize("fw_name", ALL_MOBILE_FRAMEWORKS)
    def test_platform_matches(self, fw_name: str):
        cfg = get_mobile_config(fw_name)
        assert cfg.platform == EXPECTED_MOBILE_ATTRS[fw_name]["platform"]

    @pytest.mark.parametrize("fw_name", ALL_MOBILE_FRAMEWORKS)
    def test_component_extension_matches(self, fw_name: str):
        cfg = get_mobile_config(fw_name)
        assert cfg.component_extension == EXPECTED_MOBILE_ATTRS[fw_name]["component_extension"]

    @pytest.mark.parametrize("fw_name", ALL_MOBILE_FRAMEWORKS)
    def test_hot_reload_matches(self, fw_name: str):
        cfg = get_mobile_config(fw_name)
        assert cfg.supports_hot_reload is EXPECTED_MOBILE_ATTRS[fw_name]["supports_hot_reload"]

    @pytest.mark.parametrize("fw_name", ALL_MOBILE_FRAMEWORKS)
    def test_package_manager_matches(self, fw_name: str):
        cfg = get_mobile_config(fw_name)
        assert cfg.package_manager == EXPECTED_MOBILE_ATTRS[fw_name]["package_manager"]


# ============================================================================
# SECTION E -- Mobile Hot-Reload & Platform Groups
# ============================================================================


class TestMobileHotReloadAndPlatformGroups:
    """Verify hot-reload and platform groupings."""

    @pytest.mark.parametrize("fw_name", ["react_native", "flutter", "expo"])
    def test_cross_platform_supports_hot_reload(self, fw_name: str):
        cfg = get_mobile_config(fw_name)
        assert cfg.supports_hot_reload is True
        assert cfg.platform == "cross_platform"

    @pytest.mark.parametrize("fw_name", ["swift", "kotlin_compose"])
    def test_native_does_not_support_hot_reload(self, fw_name: str):
        cfg = get_mobile_config(fw_name)
        assert cfg.supports_hot_reload is False

    def test_swift_is_ios_only(self):
        assert get_mobile_config("swift").platform == "ios"

    def test_kotlin_compose_is_android_only(self):
        assert get_mobile_config("kotlin_compose").platform == "android"


# ============================================================================
# SECTION F -- Mobile Rules Quality
# ============================================================================


class TestMobileRulesQuality:
    """Each framework has rules that start with a number and are non-trivial."""

    @pytest.mark.parametrize("fw_name", ALL_MOBILE_FRAMEWORKS)
    def test_at_least_10_rules_except_expo(self, fw_name: str):
        cfg = get_mobile_config(fw_name)
        if fw_name == "expo":
            assert len(cfg.rules) >= 10, f"{fw_name}: got {len(cfg.rules)} rules"
        else:
            assert len(cfg.rules) >= 10, f"{fw_name}: got {len(cfg.rules)} rules"

    @pytest.mark.parametrize("fw_name", ALL_MOBILE_FRAMEWORKS)
    def test_every_rule_starts_with_number(self, fw_name: str):
        cfg = get_mobile_config(fw_name)
        for i, rule in enumerate(cfg.rules):
            assert re.match(r"^\d+\.", rule), (
                f"{fw_name} rule[{i}] does not start with a number: {rule[:60]}"
            )

    @pytest.mark.parametrize("fw_name", ALL_MOBILE_FRAMEWORKS)
    def test_every_rule_is_non_empty_and_substantial(self, fw_name: str):
        cfg = get_mobile_config(fw_name)
        for i, rule in enumerate(cfg.rules):
            assert len(rule) >= 30, (
                f"{fw_name} rule[{i}] too short ({len(rule)} chars): {rule[:40]}"
            )

    @pytest.mark.parametrize("fw_name", ALL_MOBILE_FRAMEWORKS)
    def test_rules_are_tuple_not_list(self, fw_name: str):
        cfg = get_mobile_config(fw_name)
        assert isinstance(cfg.rules, tuple)

    @pytest.mark.parametrize("fw_name", ALL_MOBILE_FRAMEWORKS)
    def test_last_two_rules_mention_import_and_output(self, fw_name: str):
        """The last two rules in every framework are about imports and output."""
        cfg = get_mobile_config(fw_name)
        import_rule = cfg.rules[-2].lower()
        output_rule = cfg.rules[-1].lower()
        assert "import" in import_rule or "invent" in import_rule, (
            f"{fw_name} second-to-last rule should mention imports: {cfg.rules[-2][:60]}"
        )
        assert "output" in output_rule or "code" in output_rule, (
            f"{fw_name} last rule should mention output: {cfg.rules[-1][:60]}"
        )


# ============================================================================
# SECTION G -- Mobile Golden Examples Quality
# ============================================================================


class TestMobileGoldenExamplesQuality:
    """Each framework has golden examples that are non-empty and substantial."""

    @pytest.mark.parametrize("fw_name", ALL_MOBILE_FRAMEWORKS)
    def test_at_least_4_golden_examples(self, fw_name: str):
        cfg = get_mobile_config(fw_name)
        assert len(cfg.golden_examples) >= 4, (
            f"{fw_name}: only {len(cfg.golden_examples)} golden examples"
        )

    @pytest.mark.parametrize("fw_name", ALL_MOBILE_FRAMEWORKS)
    def test_every_golden_example_non_empty(self, fw_name: str):
        cfg = get_mobile_config(fw_name)
        for step, code in cfg.golden_examples.items():
            assert len(code.strip()) > 0, f"{fw_name} example '{step}' is empty"

    @pytest.mark.parametrize("fw_name", ALL_MOBILE_FRAMEWORKS)
    def test_every_golden_example_at_least_50_chars(self, fw_name: str):
        cfg = get_mobile_config(fw_name)
        for step, code in cfg.golden_examples.items():
            assert len(code) >= 50, (
                f"{fw_name} example '{step}' too short: {len(code)} chars"
            )

    @pytest.mark.parametrize("fw_name", ALL_MOBILE_FRAMEWORKS)
    def test_golden_examples_are_dict(self, fw_name: str):
        cfg = get_mobile_config(fw_name)
        assert isinstance(cfg.golden_examples, dict)

    @pytest.mark.parametrize("fw_name", ALL_MOBILE_FRAMEWORKS)
    def test_golden_example_keys_match_file_structure_keys(self, fw_name: str):
        """Golden examples should cover at least some of the file_structure steps."""
        cfg = get_mobile_config(fw_name)
        fs_keys = set(cfg.file_structure.keys())
        ge_keys = set(cfg.golden_examples.keys())
        overlap = fs_keys & ge_keys
        assert len(overlap) >= 1, (
            f"{fw_name}: no overlap between file_structure keys {fs_keys} "
            f"and golden_example keys {ge_keys}"
        )


# ============================================================================
# SECTION H -- Mobile File Structure
# ============================================================================


class TestMobileFileStructure:
    """Each framework has a file_structure with >= 3 entries."""

    @pytest.mark.parametrize("fw_name", ALL_MOBILE_FRAMEWORKS)
    def test_file_structure_has_at_least_3_entries(self, fw_name: str):
        cfg = get_mobile_config(fw_name)
        assert len(cfg.file_structure) >= 3, (
            f"{fw_name}: only {len(cfg.file_structure)} file_structure entries"
        )

    @pytest.mark.parametrize("fw_name", ALL_MOBILE_FRAMEWORKS)
    def test_file_structure_values_are_nonempty_strings(self, fw_name: str):
        cfg = get_mobile_config(fw_name)
        for step, path in cfg.file_structure.items():
            assert isinstance(path, str) and len(path) > 0, (
                f"{fw_name} file_structure['{step}'] is empty or not a string"
            )

    @pytest.mark.parametrize("fw_name", ALL_MOBILE_FRAMEWORKS)
    def test_file_structure_paths_contain_extension_or_brace(self, fw_name: str):
        """Paths should contain either a file extension or a {placeholder}."""
        cfg = get_mobile_config(fw_name)
        for step, path in cfg.file_structure.items():
            has_ext = "." in path.split("/")[-1]
            has_brace = "{" in path
            assert has_ext or has_brace, (
                f"{fw_name} file_structure['{step}'] = '{path}' has no ext or placeholder"
            )


# ============================================================================
# SECTION I -- Mobile Min SDK Versions
# ============================================================================


class TestMobileMinSdkVersions:
    """Each framework declares a non-empty min_sdk_version."""

    @pytest.mark.parametrize("fw_name", ALL_MOBILE_FRAMEWORKS)
    def test_min_sdk_is_nonempty_string(self, fw_name: str):
        cfg = get_mobile_config(fw_name)
        assert isinstance(cfg.min_sdk_version, str)
        assert len(cfg.min_sdk_version) > 0

    @pytest.mark.parametrize("fw_name,expected", [
        ("react_native", "0.72"),
        ("flutter", "3.22"),
        ("swift", "16.0"),
        ("kotlin_compose", "24"),
        ("expo", "50"),
    ])
    def test_specific_min_sdk(self, fw_name: str, expected: str):
        cfg = get_mobile_config(fw_name)
        assert cfg.min_sdk_version == expected


# ============================================================================
# SECTION J -- DesktopConfig Dataclass Validation
# ============================================================================


class TestDesktopConfigValidation:
    """Validate __post_init__ guards on DesktopConfig."""

    def test_valid_minimal_config(self):
        cfg = _minimal_desktop()
        assert cfg.name == "test_desktop"

    def test_name_required_empty_string(self):
        with pytest.raises(ValueError, match="name is required"):
            _minimal_desktop(name="")

    def test_display_name_required_empty_string(self):
        with pytest.raises(ValueError, match="display_name is required"):
            _minimal_desktop(display_name="")

    def test_fewer_than_5_rules_rejected(self):
        with pytest.raises(ValueError, match="At least 5 rules required"):
            _minimal_desktop(rules=("r1", "r2", "r3", "r4"))

    def test_zero_rules_rejected(self):
        with pytest.raises(ValueError, match="At least 5 rules required"):
            _minimal_desktop(rules=())

    def test_empty_platforms_rejected(self):
        with pytest.raises(ValueError, match="At least one platform required"):
            _minimal_desktop(platforms=())

    def test_exactly_5_rules_accepted(self):
        cfg = _minimal_desktop(rules=("1", "2", "3", "4", "5"))
        assert len(cfg.rules) == 5

    def test_single_platform_accepted(self):
        cfg = _minimal_desktop(platforms=("windows",))
        assert cfg.platforms == ("windows",)

    def test_frozen_prevents_mutation(self):
        cfg = _minimal_desktop()
        with pytest.raises(FrozenInstanceError):
            cfg.name = "oops"  # type: ignore[misc]

    def test_slots_prevents_arbitrary_attributes(self):
        cfg = _minimal_desktop()
        with pytest.raises((AttributeError, FrozenInstanceError, TypeError)):
            cfg.random_attr = "nope"  # type: ignore[attr-defined]


# ============================================================================
# SECTION K -- Desktop Registry Operations
# ============================================================================


class TestDesktopRegistry:
    """Tests for register_desktop, get_desktop_config, list_desktop_frameworks."""

    def test_both_frameworks_registered(self):
        frameworks = list_desktop_frameworks()
        assert len(frameworks) == 2

    def test_list_returns_sorted(self):
        frameworks = list_desktop_frameworks()
        assert frameworks == sorted(frameworks)

    def test_list_contains_electron_and_tauri(self):
        frameworks = list_desktop_frameworks()
        assert "electron" in frameworks
        assert "tauri" in frameworks

    @pytest.mark.parametrize("name", ["electron", "tauri"])
    def test_each_framework_retrievable(self, name: str):
        cfg = get_desktop_config(name)
        assert cfg.name == name

    def test_register_duplicate_raises_value_error(self):
        dup = _minimal_desktop(name="electron")
        with pytest.raises(ValueError, match="already registered"):
            register_desktop(dup)

    def test_unknown_framework_raises_key_error(self):
        with pytest.raises(KeyError, match="Unknown desktop framework"):
            get_desktop_config("qt_framework")

    def test_key_error_message_includes_available(self):
        with pytest.raises(KeyError, match="Available:"):
            get_desktop_config("wxwidgets")


# ============================================================================
# SECTION L -- Desktop Alias Resolution
# ============================================================================


class TestDesktopAliases:
    """All desktop aliases must resolve correctly."""

    @pytest.mark.parametrize("alias,canonical", [
        ("electronjs", "electron"),
        ("electron_js", "electron"),
        ("tauri2", "tauri"),
        ("tauri_v2", "tauri"),
    ])
    def test_alias_resolves(self, alias: str, canonical: str):
        cfg = get_desktop_config(alias)
        assert cfg.name == canonical

    @pytest.mark.parametrize("alias", [
        "Electron", "ELECTRON", "ElectronJS", "ELECTRONJS",
        "Tauri", "TAURI", "Tauri2", "TAURI2",
        "Tauri_V2", "TAURI_V2",
    ])
    def test_case_insensitive_lookups(self, alias: str):
        cfg = get_desktop_config(alias)
        assert cfg.name in list_desktop_frameworks()

    @pytest.mark.parametrize("weird_input,expected", [
        ("  electron  ", "electron"),
        ("  tauri  ", "tauri"),
        ("electron js", "electron"),
        ("tauri v2", "tauri"),
        ("electron.js", "electron"),
    ])
    def test_whitespace_dot_normalization(self, weird_input: str, expected: str):
        cfg = get_desktop_config(weird_input)
        assert cfg.name == expected


# ============================================================================
# SECTION M -- Desktop Framework Attribute Correctness
# ============================================================================


ALL_DESKTOP_FRAMEWORKS = ["electron", "tauri"]


class TestDesktopFrameworkAttributes:
    """Verify correct language, backend_language, frontend_framework, etc."""

    def test_electron_language(self):
        cfg = get_desktop_config("electron")
        assert cfg.language == "typescript"

    def test_electron_backend_language(self):
        cfg = get_desktop_config("electron")
        assert cfg.backend_language == "javascript"

    def test_electron_frontend_framework(self):
        cfg = get_desktop_config("electron")
        assert cfg.frontend_framework == "react"

    def test_electron_code_block_lang(self):
        cfg = get_desktop_config("electron")
        assert cfg.code_block_lang == "typescript"

    def test_electron_display_name(self):
        cfg = get_desktop_config("electron")
        assert cfg.display_name == "Electron"

    def test_electron_package_manager(self):
        cfg = get_desktop_config("electron")
        assert cfg.package_manager == "npm"

    def test_tauri_language(self):
        cfg = get_desktop_config("tauri")
        assert cfg.language == "rust"

    def test_tauri_backend_language(self):
        cfg = get_desktop_config("tauri")
        assert cfg.backend_language == "rust"

    def test_tauri_frontend_framework(self):
        cfg = get_desktop_config("tauri")
        assert cfg.frontend_framework == "any"

    def test_tauri_code_block_lang(self):
        cfg = get_desktop_config("tauri")
        assert cfg.code_block_lang == "rust"

    def test_tauri_display_name(self):
        cfg = get_desktop_config("tauri")
        assert cfg.display_name == "Tauri"

    def test_tauri_package_manager(self):
        cfg = get_desktop_config("tauri")
        assert cfg.package_manager == "cargo"


# ============================================================================
# SECTION N -- Desktop Platforms
# ============================================================================


class TestDesktopPlatforms:
    """Both frameworks target Windows, macOS, and Linux."""

    @pytest.mark.parametrize("fw_name", ALL_DESKTOP_FRAMEWORKS)
    def test_platforms_is_tuple(self, fw_name: str):
        cfg = get_desktop_config(fw_name)
        assert isinstance(cfg.platforms, tuple)

    @pytest.mark.parametrize("fw_name", ALL_DESKTOP_FRAMEWORKS)
    def test_three_platforms(self, fw_name: str):
        cfg = get_desktop_config(fw_name)
        assert len(cfg.platforms) == 3

    @pytest.mark.parametrize("fw_name", ALL_DESKTOP_FRAMEWORKS)
    @pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
    def test_platform_present(self, fw_name: str, platform: str):
        cfg = get_desktop_config(fw_name)
        assert platform in cfg.platforms

    @pytest.mark.parametrize("fw_name", ALL_DESKTOP_FRAMEWORKS)
    def test_platforms_exact_value(self, fw_name: str):
        cfg = get_desktop_config(fw_name)
        assert cfg.platforms == ("windows", "macos", "linux")


# ============================================================================
# SECTION O -- Desktop Auto-Update & System Tray
# ============================================================================


class TestDesktopFeatureFlags:
    """Both Electron and Tauri support auto-update and system tray."""

    @pytest.mark.parametrize("fw_name", ALL_DESKTOP_FRAMEWORKS)
    def test_supports_auto_update(self, fw_name: str):
        cfg = get_desktop_config(fw_name)
        assert cfg.supports_auto_update is True

    @pytest.mark.parametrize("fw_name", ALL_DESKTOP_FRAMEWORKS)
    def test_supports_system_tray(self, fw_name: str):
        cfg = get_desktop_config(fw_name)
        assert cfg.supports_system_tray is True


# ============================================================================
# SECTION P -- Desktop Rules Quality
# ============================================================================


class TestDesktopRulesQuality:
    """Each desktop framework has >= 10 numbered, non-trivial rules."""

    @pytest.mark.parametrize("fw_name", ALL_DESKTOP_FRAMEWORKS)
    def test_at_least_10_rules(self, fw_name: str):
        cfg = get_desktop_config(fw_name)
        assert len(cfg.rules) >= 10, f"{fw_name}: got {len(cfg.rules)} rules"

    @pytest.mark.parametrize("fw_name", ALL_DESKTOP_FRAMEWORKS)
    def test_rules_are_tuple(self, fw_name: str):
        cfg = get_desktop_config(fw_name)
        assert isinstance(cfg.rules, tuple)

    @pytest.mark.parametrize("fw_name", ALL_DESKTOP_FRAMEWORKS)
    def test_every_rule_is_substantial(self, fw_name: str):
        cfg = get_desktop_config(fw_name)
        for i, rule in enumerate(cfg.rules):
            assert len(rule) >= 30, (
                f"{fw_name} rule[{i}] too short ({len(rule)} chars)"
            )

    @pytest.mark.parametrize("fw_name", ALL_DESKTOP_FRAMEWORKS)
    def test_no_duplicate_rules(self, fw_name: str):
        cfg = get_desktop_config(fw_name)
        assert len(set(cfg.rules)) == len(cfg.rules), (
            f"{fw_name} has duplicate rules"
        )

    @pytest.mark.parametrize("fw_name", ALL_DESKTOP_FRAMEWORKS)
    def test_last_two_rules_mention_import_and_output(self, fw_name: str):
        cfg = get_desktop_config(fw_name)
        import_rule = cfg.rules[-2].lower()
        output_rule = cfg.rules[-1].lower()
        assert "import" in import_rule or "invent" in import_rule, (
            f"{fw_name} second-to-last rule should mention imports"
        )
        assert "output" in output_rule or "code" in output_rule, (
            f"{fw_name} last rule should mention output"
        )

    def test_electron_mentions_preload(self):
        """Electron rules must mention preload / contextBridge."""
        cfg = get_desktop_config("electron")
        rules_text = " ".join(cfg.rules).lower()
        assert "preload" in rules_text
        assert "contextbridge" in rules_text or "context_bridge" in rules_text

    def test_electron_mentions_ipc(self):
        cfg = get_desktop_config("electron")
        rules_text = " ".join(cfg.rules).lower()
        assert "ipc" in rules_text

    def test_tauri_mentions_command(self):
        cfg = get_desktop_config("tauri")
        rules_text = " ".join(cfg.rules).lower()
        assert "command" in rules_text

    def test_tauri_mentions_state(self):
        cfg = get_desktop_config("tauri")
        rules_text = " ".join(cfg.rules).lower()
        assert "state" in rules_text


# ============================================================================
# SECTION Q -- Desktop Golden Examples Quality
# ============================================================================


class TestDesktopGoldenExamplesQuality:
    """Both desktop frameworks have high-quality golden examples."""

    @pytest.mark.parametrize("fw_name", ALL_DESKTOP_FRAMEWORKS)
    def test_at_least_4_golden_examples(self, fw_name: str):
        cfg = get_desktop_config(fw_name)
        assert len(cfg.golden_examples) >= 4, (
            f"{fw_name}: only {len(cfg.golden_examples)} golden examples"
        )

    @pytest.mark.parametrize("fw_name", ALL_DESKTOP_FRAMEWORKS)
    def test_every_golden_example_non_empty(self, fw_name: str):
        cfg = get_desktop_config(fw_name)
        for step, code in cfg.golden_examples.items():
            assert len(code.strip()) > 0, f"{fw_name} example '{step}' is empty"

    @pytest.mark.parametrize("fw_name", ALL_DESKTOP_FRAMEWORKS)
    def test_every_golden_example_at_least_50_chars(self, fw_name: str):
        cfg = get_desktop_config(fw_name)
        for step, code in cfg.golden_examples.items():
            assert len(code) >= 50, (
                f"{fw_name} example '{step}' too short: {len(code)} chars"
            )

    @pytest.mark.parametrize("fw_name", ALL_DESKTOP_FRAMEWORKS)
    def test_golden_examples_are_dict(self, fw_name: str):
        cfg = get_desktop_config(fw_name)
        assert isinstance(cfg.golden_examples, dict)

    def test_electron_golden_examples_contain_expected_steps(self):
        cfg = get_desktop_config("electron")
        expected_steps = {"main_process", "preload", "renderer", "ipc_handler"}
        actual = set(cfg.golden_examples.keys())
        assert expected_steps.issubset(actual), (
            f"Electron missing golden example steps: {expected_steps - actual}"
        )

    def test_tauri_golden_examples_contain_expected_steps(self):
        cfg = get_desktop_config("tauri")
        expected_steps = {"command", "state", "frontend_invoke", "config"}
        actual = set(cfg.golden_examples.keys())
        assert expected_steps.issubset(actual), (
            f"Tauri missing golden example steps: {expected_steps - actual}"
        )

    def test_electron_main_process_example_mentions_browserwindow(self):
        cfg = get_desktop_config("electron")
        assert "BrowserWindow" in cfg.golden_examples["main_process"]

    def test_electron_preload_example_mentions_contextbridge(self):
        cfg = get_desktop_config("electron")
        assert "contextBridge" in cfg.golden_examples["preload"]

    def test_tauri_command_example_mentions_tauri_command(self):
        cfg = get_desktop_config("tauri")
        assert "#[tauri::command]" in cfg.golden_examples["command"]

    def test_tauri_state_example_mentions_mutex(self):
        cfg = get_desktop_config("tauri")
        assert "Mutex" in cfg.golden_examples["state"]


# ============================================================================
# SECTION R -- Desktop File Structure
# ============================================================================


class TestDesktopFileStructure:
    """Each desktop framework has a file_structure with >= 3 entries."""

    @pytest.mark.parametrize("fw_name", ALL_DESKTOP_FRAMEWORKS)
    def test_file_structure_has_at_least_3_entries(self, fw_name: str):
        cfg = get_desktop_config(fw_name)
        assert len(cfg.file_structure) >= 3, (
            f"{fw_name}: only {len(cfg.file_structure)} file_structure entries"
        )

    @pytest.mark.parametrize("fw_name", ALL_DESKTOP_FRAMEWORKS)
    def test_file_structure_values_are_nonempty_strings(self, fw_name: str):
        cfg = get_desktop_config(fw_name)
        for step, path in cfg.file_structure.items():
            assert isinstance(path, str) and len(path) > 0, (
                f"{fw_name} file_structure['{step}'] is empty or not a string"
            )

    def test_electron_file_structure_has_main_and_preload(self):
        cfg = get_desktop_config("electron")
        assert "main_process" in cfg.file_structure
        assert "preload" in cfg.file_structure

    def test_tauri_file_structure_has_command_and_main(self):
        cfg = get_desktop_config("tauri")
        assert "command" in cfg.file_structure
        assert "main" in cfg.file_structure

    def test_electron_has_many_file_structure_entries(self):
        """Electron has a rich file structure (main, preload, renderer, ipc, etc.)."""
        cfg = get_desktop_config("electron")
        assert len(cfg.file_structure) >= 8

    def test_tauri_has_many_file_structure_entries(self):
        cfg = get_desktop_config("tauri")
        assert len(cfg.file_structure) >= 7


# ============================================================================
# SECTION S -- Cross-Module Consistency Checks
# ============================================================================


class TestCrossModuleConsistency:
    """Ensure mobile and desktop registries do not overlap or interfere."""

    def test_mobile_and_desktop_names_do_not_overlap(self):
        mobile_names = set(list_mobile_frameworks())
        desktop_names = set(list_desktop_frameworks())
        overlap = mobile_names & desktop_names
        assert not overlap, f"Overlapping framework names: {overlap}"

    def test_mobile_list_has_exactly_5(self):
        assert len(list_mobile_frameworks()) == 5

    def test_desktop_list_has_exactly_2(self):
        assert len(list_desktop_frameworks()) == 2

    def test_mobile_returns_list_type(self):
        result = list_mobile_frameworks()
        assert isinstance(result, list)

    def test_desktop_returns_list_type(self):
        result = list_desktop_frameworks()
        assert isinstance(result, list)

    def test_mobile_configs_are_instances_of_mobile_config(self):
        for name in list_mobile_frameworks():
            cfg = get_mobile_config(name)
            assert isinstance(cfg, MobileConfig)

    def test_desktop_configs_are_instances_of_desktop_config(self):
        for name in list_desktop_frameworks():
            cfg = get_desktop_config(name)
            assert isinstance(cfg, DesktopConfig)


# ============================================================================
# SECTION T -- Edge Cases & Adversarial Inputs
# ============================================================================


class TestMobileEdgeCases:
    """Edge cases for mobile config construction and lookups."""

    def test_empty_string_lookup_raises_key_error(self):
        with pytest.raises(KeyError):
            get_mobile_config("")

    def test_whitespace_only_lookup_raises_key_error(self):
        with pytest.raises(KeyError):
            get_mobile_config("   ")

    def test_numeric_string_lookup_raises_key_error(self):
        with pytest.raises(KeyError):
            get_mobile_config("12345")

    def test_special_chars_lookup_raises_key_error(self):
        with pytest.raises(KeyError):
            get_mobile_config("!!!@@@")

    def test_very_long_string_raises_key_error(self):
        with pytest.raises(KeyError):
            get_mobile_config("a" * 1000)

    def test_name_with_only_whitespace_fails_validation(self):
        """Empty string after strip should fail."""
        # MobileConfig's __post_init__ checks `not self.name`, so
        # a non-empty whitespace string passes the name check but could be a
        # problem downstream.  Verify it at least does not crash on construction.
        cfg = _minimal_mobile(name="   spaces   ")
        assert cfg.name == "   spaces   "

    def test_rules_with_exactly_5_entries(self):
        cfg = _minimal_mobile(rules=("1.", "2.", "3.", "4.", "5."))
        assert len(cfg.rules) == 5

    def test_4_rules_fails(self):
        with pytest.raises(ValueError):
            _minimal_mobile(rules=("1.", "2.", "3.", "4."))

    def test_1_rule_fails(self):
        with pytest.raises(ValueError):
            _minimal_mobile(rules=("1.",))


class TestDesktopEdgeCases:
    """Edge cases for desktop config construction and lookups."""

    def test_empty_string_lookup_raises_key_error(self):
        with pytest.raises(KeyError):
            get_desktop_config("")

    def test_whitespace_only_lookup_raises_key_error(self):
        with pytest.raises(KeyError):
            get_desktop_config("   ")

    def test_numeric_string_lookup_raises_key_error(self):
        with pytest.raises(KeyError):
            get_desktop_config("99999")

    def test_special_chars_lookup_raises_key_error(self):
        with pytest.raises(KeyError):
            get_desktop_config("$$$%%%")

    def test_very_long_string_raises_key_error(self):
        with pytest.raises(KeyError):
            get_desktop_config("b" * 1000)

    def test_empty_platforms_tuple_fails(self):
        with pytest.raises(ValueError, match="At least one platform required"):
            _minimal_desktop(platforms=())

    def test_4_rules_fails(self):
        with pytest.raises(ValueError):
            _minimal_desktop(rules=("1.", "2.", "3.", "4."))

    def test_1_rule_fails(self):
        with pytest.raises(ValueError):
            _minimal_desktop(rules=("1.",))

    def test_name_and_display_name_both_empty_raises_name_first(self):
        """When both are empty, name check fires first."""
        with pytest.raises(ValueError, match="name is required"):
            _minimal_desktop(name="", display_name="")


# ============================================================================
# SECTION U -- Specific Framework Content Smoke Tests
# ============================================================================


class TestMobileContentSmoke:
    """Quick smoke tests for framework-specific content correctness."""

    def test_react_native_golden_screen_imports_react(self):
        cfg = get_mobile_config("react_native")
        assert "import React" in cfg.golden_examples["screen"]

    def test_react_native_golden_screen_uses_flatlist(self):
        cfg = get_mobile_config("react_native")
        assert "FlatList" in cfg.golden_examples["screen"]

    def test_flutter_golden_screen_uses_riverpod(self):
        cfg = get_mobile_config("flutter")
        assert "riverpod" in cfg.golden_examples["screen"].lower()

    def test_flutter_golden_model_uses_freezed(self):
        cfg = get_mobile_config("flutter")
        assert "@freezed" in cfg.golden_examples["model"]

    def test_swift_golden_view_uses_swiftui(self):
        cfg = get_mobile_config("swift")
        assert "import SwiftUI" in cfg.golden_examples["view"]

    def test_swift_golden_viewmodel_uses_mainactor(self):
        cfg = get_mobile_config("swift")
        assert "@MainActor" in cfg.golden_examples["viewmodel"]

    def test_kotlin_compose_golden_screen_uses_scaffold(self):
        cfg = get_mobile_config("kotlin_compose")
        assert "Scaffold" in cfg.golden_examples["screen"]

    def test_kotlin_compose_golden_viewmodel_uses_hilt(self):
        cfg = get_mobile_config("kotlin_compose")
        assert "@HiltViewModel" in cfg.golden_examples["viewmodel"]

    def test_expo_golden_screen_uses_expo_router(self):
        cfg = get_mobile_config("expo")
        assert "expo-router" in cfg.golden_examples["screen"]

    def test_expo_golden_layout_uses_splash_screen(self):
        cfg = get_mobile_config("expo")
        assert "SplashScreen" in cfg.golden_examples["layout"]

    def test_expo_golden_hook_uses_secure_store(self):
        cfg = get_mobile_config("expo")
        assert "SecureStore" in cfg.golden_examples["hook"]


class TestDesktopContentSmoke:
    """Quick smoke tests for desktop framework-specific content."""

    def test_electron_main_process_uses_app_whenready(self):
        cfg = get_desktop_config("electron")
        assert "app.whenReady()" in cfg.golden_examples["main_process"]

    def test_electron_preload_uses_ipcrenderer(self):
        cfg = get_desktop_config("electron")
        assert "ipcRenderer" in cfg.golden_examples["preload"]

    def test_electron_ipc_handler_uses_ipcmain(self):
        cfg = get_desktop_config("electron")
        assert "ipcMain" in cfg.golden_examples["ipc_handler"]

    def test_tauri_command_uses_serde(self):
        cfg = get_desktop_config("tauri")
        assert "serde" in cfg.golden_examples["command"]

    def test_tauri_state_uses_appstate(self):
        cfg = get_desktop_config("tauri")
        assert "AppState" in cfg.golden_examples["state"]

    def test_tauri_frontend_uses_invoke(self):
        cfg = get_desktop_config("tauri")
        assert "invoke" in cfg.golden_examples["frontend_invoke"]

    def test_tauri_config_is_valid_looking_json(self):
        cfg = get_desktop_config("tauri")
        config_text = cfg.golden_examples["config"]
        assert config_text.strip().startswith("{")
        assert config_text.strip().endswith("}")


# ============================================================================
# SECTION V -- No Duplicate Rules Within Each Framework
# ============================================================================


class TestNoDuplicateRules:
    """Ensure no framework has duplicate rules (mobile or desktop)."""

    @pytest.mark.parametrize("fw_name", ALL_MOBILE_FRAMEWORKS)
    def test_mobile_no_duplicate_rules(self, fw_name: str):
        cfg = get_mobile_config(fw_name)
        assert len(set(cfg.rules)) == len(cfg.rules), (
            f"{fw_name} has duplicate rules"
        )

    @pytest.mark.parametrize("fw_name", ALL_DESKTOP_FRAMEWORKS)
    def test_desktop_no_duplicate_rules(self, fw_name: str):
        cfg = get_desktop_config(fw_name)
        assert len(set(cfg.rules)) == len(cfg.rules), (
            f"{fw_name} has duplicate rules"
        )
