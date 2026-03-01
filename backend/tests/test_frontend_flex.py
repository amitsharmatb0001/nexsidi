"""Tests for frontend framework flexibility (Aanya dynamic framework rules).

Updated to work with both the config registry (preferred) and legacy constants
(backward compatibility). The config registry takes priority when a framework
is registered, falling back to legacy hardcoded values for unknown frameworks.
"""

from __future__ import annotations

import pytest

from app.agents.aanya import (
    DEFAULT_FRONTEND_RULES,
    FRAMEWORK_GENERATION_ORDERS,
    FRONTEND_RULES,
    NEXTJS_GENERATION_ORDER,
    REACT_GENERATION_ORDER,
    get_frontend_framework,
    get_generation_order,
    get_rules,
)


class TestFrameworkDetection:
    """Test framework detection from architecture contract."""

    def test_detect_nextjs(self):
        contract = {"tech_stack": {"frontend": "Next.js"}}
        assert get_frontend_framework(contract) == "Next.js"

    def test_detect_react(self):
        contract = {"tech_stack": {"frontend": "React"}}
        assert get_frontend_framework(contract) == "React"

    def test_detect_alias(self):
        contract = {"tech_stack": {"frontend": "next"}}
        assert get_frontend_framework(contract) == "Next.js"

    def test_detect_default_when_missing(self):
        contract = {"tech_stack": {}}
        assert get_frontend_framework(contract) == "Next.js"

    def test_detect_default_when_no_tech_stack(self):
        contract = {}
        assert get_frontend_framework(contract) == "Next.js"

    def test_detect_unknown_defaults_to_nextjs(self):
        contract = {"tech_stack": {"frontend": "SomeUnknownFramework"}}
        assert get_frontend_framework(contract) == "Next.js"


class TestGenerationOrder:
    """Test framework-specific generation orders.

    These tests now validate content rather than identity (``is`` checks)
    because get_generation_order() returns config registry data first,
    which creates new list objects each call.
    """

    def test_nextjs_order(self):
        order = get_generation_order("Next.js")
        names = [s["name"] for s in order]
        assert "auth_context" in names
        assert names[-1] == "app"  # App is always last
        # All steps must have name, path, description
        for step in order:
            assert "name" in step
            assert "path" in step
            assert "description" in step

    def test_react_order(self):
        order = get_generation_order("React")
        names = [s["name"] for s in order]
        assert "router" in names  # React has router step
        assert names[-1] == "app"  # App is still last
        # App.tsx should be at src/App.tsx for React
        assert "App.tsx" in order[-1]["path"]

    def test_nextjs_order_has_layout(self):
        order = get_generation_order("Next.js")
        names = [s["name"] for s in order]
        assert "layout" in names

    def test_known_framework_returns_its_order(self):
        """Known frameworks (in registry) return their own generation order."""
        order = get_generation_order("Angular")
        names = [s["name"] for s in order]
        # Angular config has its own generation order — should NOT be Next.js
        assert len(names) >= 3
        for step in order:
            assert "name" in step
            assert "path" in step

    def test_unknown_framework_fallback(self):
        """Truly unknown frameworks fall back to Next.js generation order.

        The config registry falls back to nextjs, so the returned order
        will match the registry's nextjs config (not the legacy constant).
        """
        order = get_generation_order("CobaltFramework")
        names = [s["name"] for s in order]
        # Must still return a valid Next.js-like generation order
        assert "auth_context" in names
        assert names[-1] in ("app", "root_layout")  # Next.js last step
        assert len(order) >= 3


class TestFrameworkRules:
    """Test framework-specific mandatory rules."""

    def test_nextjs_rules_have_app_router(self):
        rules = get_rules("Next.js")
        assert any("App Router" in r for r in rules)
        assert any("use client" in r.lower() for r in rules)

    def test_react_rules_have_react_router(self):
        rules = get_rules("React")
        assert any("React Router" in r for r in rules)
        assert any("<Link>" in r for r in rules)

    def test_both_have_typescript(self):
        for framework in ["Next.js", "React"]:
            rules = get_rules(framework)
            assert any("TypeScript" in r for r in rules)

    def test_both_have_tailwind(self):
        for framework in ["Next.js", "React"]:
            rules = get_rules(framework)
            assert any("Tailwind" in r for r in rules)

    def test_both_have_accessibility(self):
        for framework in ["Next.js", "React"]:
            rules = get_rules(framework)
            assert any("Accessibility" in r or "ARIA" in r for r in rules)

    def test_angular_returns_angular_rules(self):
        """Angular is in the config registry — returns its own rules, not Next.js."""
        rules = get_rules("Angular")
        # Angular rules should mention Angular-specific things
        assert any("standalone" in r.lower() or "angular" in r.lower() for r in rules)
        assert len(rules) >= 10

    def test_truly_unknown_defaults_to_nextjs_rules(self):
        """A framework not in the registry falls back to Next.js rules.

        The config registry falls back to nextjs, so rules come from the
        registry's nextjs config (enhanced) rather than the legacy constant.
        """
        rules = get_rules("CobaltFramework")
        # Must still have Next.js-flavoured rules
        assert any("App Router" in r for r in rules)
        assert any("Next.js" in r for r in rules)
        assert len(rules) >= 11
