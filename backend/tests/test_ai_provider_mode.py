"""Tests for AI Provider Mode feature flag.

Verifies that the ai_provider_mode setting correctly controls which
models are selected for every routing path: complexity maps, security
overrides, generation size thresholds, escalation chains, fixer
iteration maps, and agent default_model resolution.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.ai_router import (
    COMPLEXITY_TO_MODEL,
    ESCALATION_CHAIN,
    MODELS,
    SECURITY_CRITICAL_TASKS,
    ModelSpec,
    Provider,
    TaskComplexity,
    _MODE_COMPLEXITY_MAP,
    _MODE_GENERATION_MAP,
    _MODE_SECURITY_MODEL,
    get_ai_mode,
    select_model_for_generation,
)


# ── Helpers ─────────────────────────────────────────────────────────


def _patch_mode(mode: str):
    """Context manager that patches ai_provider_mode to the given mode."""
    return patch(
        "app.services.ai_router.get_settings",
        return_value=type("S", (), {"ai_provider_mode": mode})(),
    )


def _all_models_provider(models: dict[TaskComplexity, str], target: Provider) -> bool:
    """Check that every model key in the map belongs to the target provider."""
    return all(MODELS[k].provider == target for k in models.values())


# ── TestProviderModeConfig ──────────────────────────────────────────


class TestProviderModeConfig:
    """Feature flag basics."""

    def test_default_mode_is_mixed(self):
        """Default must be 'mixed' so all existing tests pass."""
        from app.config import Settings

        # Settings requires DATABASE_URL and JWT_SECRET_KEY at minimum
        # Omit AI_PROVIDER_MODE entirely so the default kicks in
        env = {
            "DATABASE_URL": "postgresql://x:x@localhost/x",
            "JWT_SECRET_KEY": "testsecretkey12345678901234567890ab",
        }
        with patch.dict("os.environ", env, clear=False):
            import os
            os.environ.pop("AI_PROVIDER_MODE", None)
            s = Settings()  # type: ignore[call-arg]
            assert s.ai_provider_mode == "mixed"

    def test_gemini_mode_from_env(self):
        from app.config import Settings

        with patch.dict(
            "os.environ",
            {
                "DATABASE_URL": "postgresql://x:x@localhost/x",
                "JWT_SECRET_KEY": "testsecretkey12345678901234567890ab",
                "AI_PROVIDER_MODE": "gemini",
            },
        ):
            s = Settings()  # type: ignore[call-arg]
            assert s.ai_provider_mode == "gemini"

    def test_claude_mode_from_env(self):
        from app.config import Settings

        with patch.dict(
            "os.environ",
            {
                "DATABASE_URL": "postgresql://x:x@localhost/x",
                "JWT_SECRET_KEY": "testsecretkey12345678901234567890ab",
                "AI_PROVIDER_MODE": "claude",
            },
        ):
            s = Settings()  # type: ignore[call-arg]
            assert s.ai_provider_mode == "claude"

    def test_get_ai_mode_reads_setting(self):
        with _patch_mode("gemini"):
            assert get_ai_mode() == "gemini"

    def test_get_ai_mode_defaults_to_mixed(self):
        with _patch_mode("mixed"):
            assert get_ai_mode() == "mixed"


# ── TestModeComplexityMaps ──────────────────────────────────────────


class TestModeComplexityMaps:
    """Verify per-mode complexity routing maps."""

    def test_mixed_map_matches_original(self):
        """Mixed mode must be identical to the original COMPLEXITY_TO_MODEL."""
        assert _MODE_COMPLEXITY_MAP["mixed"] is COMPLEXITY_TO_MODEL

    def test_gemini_map_all_google(self):
        assert _all_models_provider(_MODE_COMPLEXITY_MAP["gemini"], Provider.GOOGLE)

    def test_claude_map_all_anthropic(self):
        assert _all_models_provider(_MODE_COMPLEXITY_MAP["claude"], Provider.ANTHROPIC)

    def test_all_modes_cover_all_complexities(self):
        for mode, cmap in _MODE_COMPLEXITY_MAP.items():
            for c in TaskComplexity:
                assert c in cmap, f"{mode} missing {c}"

    def test_gemini_map_has_four_entries(self):
        assert len(_MODE_COMPLEXITY_MAP["gemini"]) == 4

    def test_claude_map_has_four_entries(self):
        assert len(_MODE_COMPLEXITY_MAP["claude"]) == 4


# ── TestModeSecurityModel ───────────────────────────────────────────


class TestModeSecurityModel:
    """Verify per-mode security model overrides."""

    def test_mixed_security_is_sonnet(self):
        assert _MODE_SECURITY_MODEL["mixed"] == "sonnet"

    def test_gemini_security_is_gemini_3_pro(self):
        assert _MODE_SECURITY_MODEL["gemini"] == "gemini-3-pro"

    def test_claude_security_is_sonnet(self):
        assert _MODE_SECURITY_MODEL["claude"] == "sonnet"

    def test_all_security_models_exist_in_registry(self):
        for mode, key in _MODE_SECURITY_MODEL.items():
            assert key in MODELS, f"{mode} security model '{key}' not in MODELS"

    def test_gemini_security_is_google_provider(self):
        assert MODELS[_MODE_SECURITY_MODEL["gemini"]].provider == Provider.GOOGLE

    def test_claude_security_is_anthropic_provider(self):
        assert MODELS[_MODE_SECURITY_MODEL["claude"]].provider == Provider.ANTHROPIC


# ── TestGeminiModeRouting ───────────────────────────────────────────


class TestGeminiModeRouting:
    """In gemini mode, no Claude models should be selected."""

    def test_low_complexity_returns_gemini(self):
        with _patch_mode("gemini"):
            model = select_model_for_generation(50)
            assert MODELS[model].provider == Provider.GOOGLE

    def test_medium_complexity_returns_gemini(self):
        with _patch_mode("gemini"):
            model = select_model_for_generation(300)
            assert MODELS[model].provider == Provider.GOOGLE

    def test_high_complexity_returns_gemini(self):
        with _patch_mode("gemini"):
            model = select_model_for_generation(800)
            assert MODELS[model].provider == Provider.GOOGLE

    def test_security_task_returns_gemini(self):
        with _patch_mode("gemini"):
            for task in SECURITY_CRITICAL_TASKS:
                model = select_model_for_generation(100, task_type=task)
                assert MODELS[model].provider == Provider.GOOGLE, f"Failed for {task}"

    def test_small_file_returns_flash(self):
        with _patch_mode("gemini"):
            assert select_model_for_generation(50) == "gemini-flash"

    def test_medium_file_returns_gemini_3_pro(self):
        with _patch_mode("gemini"):
            assert select_model_for_generation(300) == "gemini-3-pro"

    def test_large_file_returns_gemini_31_pro(self):
        with _patch_mode("gemini"):
            assert select_model_for_generation(800) == "gemini-3.1-pro"


# ── TestClaudeModeRouting ───────────────────────────────────────────


class TestClaudeModeRouting:
    """In claude mode, no Gemini models should be selected."""

    def test_low_complexity_returns_claude(self):
        with _patch_mode("claude"):
            model = select_model_for_generation(50)
            assert MODELS[model].provider == Provider.ANTHROPIC

    def test_medium_complexity_returns_claude(self):
        with _patch_mode("claude"):
            model = select_model_for_generation(300)
            assert MODELS[model].provider == Provider.ANTHROPIC

    def test_high_complexity_returns_claude(self):
        with _patch_mode("claude"):
            model = select_model_for_generation(800)
            assert MODELS[model].provider == Provider.ANTHROPIC

    def test_security_task_returns_claude(self):
        with _patch_mode("claude"):
            for task in SECURITY_CRITICAL_TASKS:
                model = select_model_for_generation(100, task_type=task)
                assert MODELS[model].provider == Provider.ANTHROPIC, f"Failed for {task}"

    def test_small_file_returns_haiku(self):
        with _patch_mode("claude"):
            assert select_model_for_generation(50) == "haiku"

    def test_medium_file_returns_sonnet(self):
        with _patch_mode("claude"):
            assert select_model_for_generation(300) == "sonnet"

    def test_large_file_returns_opus(self):
        with _patch_mode("claude"):
            assert select_model_for_generation(800) == "opus"


# ── TestMixedModeRouting ────────────────────────────────────────────


class TestMixedModeRouting:
    """Mixed mode must produce identical results to the original behavior."""

    def test_small_file_returns_gemini_flash(self):
        with _patch_mode("mixed"):
            assert select_model_for_generation(50) == "gemini-flash"

    def test_medium_file_returns_sonnet(self):
        with _patch_mode("mixed"):
            assert select_model_for_generation(300) == "sonnet"

    def test_large_file_returns_opus(self):
        with _patch_mode("mixed"):
            assert select_model_for_generation(800) == "opus"

    def test_security_returns_sonnet(self):
        with _patch_mode("mixed"):
            assert select_model_for_generation(50, "auth_code") == "sonnet"

    def test_boundary_199_is_small(self):
        with _patch_mode("mixed"):
            assert select_model_for_generation(199) == "gemini-flash"

    def test_boundary_200_is_medium(self):
        with _patch_mode("mixed"):
            assert select_model_for_generation(200) == "sonnet"

    def test_boundary_499_is_medium(self):
        with _patch_mode("mixed"):
            assert select_model_for_generation(499) == "sonnet"

    def test_boundary_500_is_large(self):
        with _patch_mode("mixed"):
            assert select_model_for_generation(500) == "opus"


# ── TestResolveModelOverride ────────────────────────────────────────


class TestResolveModelOverride:
    """Test resolve_model_override in base.py."""

    def test_none_returns_none(self):
        from app.agents.base import resolve_model_override

        assert resolve_model_override(None) is None

    def test_fixes_model_id_to_key_claude(self):
        from app.agents.base import resolve_model_override

        with _patch_mode("mixed"):
            assert resolve_model_override("claude-sonnet-4-6") == "sonnet"

    def test_fixes_model_id_to_key_gemini(self):
        from app.agents.base import resolve_model_override

        with _patch_mode("mixed"):
            assert resolve_model_override("gemini-2.5-pro") == "gemini-pro"

    def test_mixed_mode_returns_corrected_key(self):
        from app.agents.base import resolve_model_override

        with _patch_mode("mixed"):
            assert resolve_model_override("sonnet") == "sonnet"

    def test_gemini_mode_remaps_claude_to_gemini(self):
        from app.agents.base import resolve_model_override

        with _patch_mode("gemini"):
            result = resolve_model_override("claude-sonnet-4-6")
            assert result is not None
            assert MODELS[result].provider == Provider.GOOGLE

    def test_gemini_mode_remaps_sonnet_to_gemini_3_pro(self):
        from app.agents.base import resolve_model_override

        with _patch_mode("gemini"):
            assert resolve_model_override("sonnet") == "gemini-3-pro"

    def test_gemini_mode_remaps_opus_to_gemini_31_pro(self):
        from app.agents.base import resolve_model_override

        with _patch_mode("gemini"):
            assert resolve_model_override("opus") == "gemini-3.1-pro"

    def test_gemini_mode_remaps_haiku_to_gemini_flash(self):
        from app.agents.base import resolve_model_override

        with _patch_mode("gemini"):
            assert resolve_model_override("haiku") == "gemini-flash"

    def test_claude_mode_remaps_gemini_to_claude(self):
        from app.agents.base import resolve_model_override

        with _patch_mode("claude"):
            result = resolve_model_override("gemini-flash")
            assert result is not None
            assert MODELS[result].provider == Provider.ANTHROPIC

    def test_claude_mode_remaps_gemini_flash_to_haiku(self):
        from app.agents.base import resolve_model_override

        with _patch_mode("claude"):
            assert resolve_model_override("gemini-flash") == "haiku"

    def test_claude_mode_remaps_gemini_3_pro_to_sonnet(self):
        from app.agents.base import resolve_model_override

        with _patch_mode("claude"):
            assert resolve_model_override("gemini-3-pro") == "sonnet"

    def test_claude_mode_remaps_gemini_31_pro_to_opus(self):
        from app.agents.base import resolve_model_override

        with _patch_mode("claude"):
            assert resolve_model_override("gemini-3.1-pro") == "opus"

    def test_unknown_model_returns_none(self):
        from app.agents.base import resolve_model_override

        with _patch_mode("mixed"):
            assert resolve_model_override("nonexistent-model") is None

    def test_gemini_key_stays_in_gemini_mode(self):
        from app.agents.base import resolve_model_override

        with _patch_mode("gemini"):
            assert resolve_model_override("gemini-flash") == "gemini-flash"

    def test_claude_key_stays_in_claude_mode(self):
        from app.agents.base import resolve_model_override

        with _patch_mode("claude"):
            assert resolve_model_override("sonnet") == "sonnet"


# ── TestEscalationChainFiltering ────────────────────────────────────


class TestEscalationChainFiltering:
    """Test that escalation chain is filtered by provider mode."""

    def test_mixed_mode_returns_full_chain(self):
        from app.services.ai_router import AIRouter

        router = AIRouter.__new__(AIRouter)
        with _patch_mode("mixed"):
            chain = router._get_active_escalation_chain()
        assert chain == ESCALATION_CHAIN

    def test_gemini_mode_only_google_models(self):
        from app.services.ai_router import AIRouter

        router = AIRouter.__new__(AIRouter)
        with _patch_mode("gemini"):
            chain = router._get_active_escalation_chain()
        assert len(chain) > 0
        for key in chain:
            assert MODELS[key].provider == Provider.GOOGLE, f"{key} is not Google"

    def test_claude_mode_only_anthropic_models(self):
        from app.services.ai_router import AIRouter

        router = AIRouter.__new__(AIRouter)
        with _patch_mode("claude"):
            chain = router._get_active_escalation_chain()
        assert len(chain) > 0
        for key in chain:
            assert MODELS[key].provider == Provider.ANTHROPIC, f"{key} is not Anthropic"

    def test_gemini_chain_has_at_least_3_models(self):
        from app.services.ai_router import AIRouter

        router = AIRouter.__new__(AIRouter)
        with _patch_mode("gemini"):
            chain = router._get_active_escalation_chain()
        assert len(chain) >= 3

    def test_claude_chain_has_at_least_3_models(self):
        from app.services.ai_router import AIRouter

        router = AIRouter.__new__(AIRouter)
        with _patch_mode("claude"):
            chain = router._get_active_escalation_chain()
        assert len(chain) >= 3


# ── TestFixerEscalation ─────────────────────────────────────────────


class TestFixerEscalation:
    """Test Fixer model escalation respects provider mode."""

    def test_mixed_mode_iteration_map(self):
        from app.agents.fixer import _get_iteration_model_map

        with _patch_mode("mixed"):
            imap = _get_iteration_model_map()
        assert len(imap) == 5
        # First iterations cheap, last with thinking
        assert imap[5][1] is True  # enable_thinking on iteration 5

    def test_gemini_mode_all_google(self):
        from app.agents.fixer import _get_iteration_model_map

        with _patch_mode("gemini"):
            imap = _get_iteration_model_map()
        for i, (model_key, _) in imap.items():
            assert MODELS[model_key].provider == Provider.GOOGLE, f"iter {i}: {model_key}"

    def test_claude_mode_all_anthropic(self):
        from app.agents.fixer import _get_iteration_model_map

        with _patch_mode("claude"):
            imap = _get_iteration_model_map()
        for i, (model_key, _) in imap.items():
            assert MODELS[model_key].provider == Provider.ANTHROPIC, f"iter {i}: {model_key}"

    def test_all_modes_have_5_iterations(self):
        from app.agents.fixer import _get_iteration_model_map

        for mode in ("mixed", "gemini", "claude"):
            with _patch_mode(mode):
                imap = _get_iteration_model_map()
            assert len(imap) == 5, f"{mode} has {len(imap)} iterations"

    def test_all_modes_enable_thinking_on_last(self):
        from app.agents.fixer import _get_iteration_model_map

        for mode in ("mixed", "gemini", "claude"):
            with _patch_mode(mode):
                imap = _get_iteration_model_map()
            assert imap[5][1] is True, f"{mode} iter 5 doesn't enable thinking"

    def test_fixer_uses_registry_keys_not_model_ids(self):
        from app.agents.fixer import _get_iteration_model_map

        for mode in ("mixed", "gemini", "claude"):
            with _patch_mode(mode):
                imap = _get_iteration_model_map()
            for i, (model_key, _) in imap.items():
                assert model_key in MODELS, f"{mode} iter {i}: '{model_key}' not in MODELS"


# ── TestModeGenerationMap ───────────────────────────────────────────


class TestModeGenerationMap:
    """Verify generation size maps for all modes."""

    def test_all_modes_have_three_sizes(self):
        for mode, gmap in _MODE_GENERATION_MAP.items():
            assert set(gmap.keys()) == {"small", "medium", "large"}, f"{mode}"

    def test_all_keys_in_registry(self):
        for mode, gmap in _MODE_GENERATION_MAP.items():
            for size, key in gmap.items():
                assert key in MODELS, f"{mode}/{size}: '{key}' not in MODELS"

    def test_gemini_map_all_google(self):
        for key in _MODE_GENERATION_MAP["gemini"].values():
            assert MODELS[key].provider == Provider.GOOGLE

    def test_claude_map_all_anthropic(self):
        for key in _MODE_GENERATION_MAP["claude"].values():
            assert MODELS[key].provider == Provider.ANTHROPIC

    def test_mixed_map_uses_both_providers(self):
        providers = {MODELS[k].provider for k in _MODE_GENERATION_MAP["mixed"].values()}
        assert Provider.GOOGLE in providers
        assert Provider.ANTHROPIC in providers
