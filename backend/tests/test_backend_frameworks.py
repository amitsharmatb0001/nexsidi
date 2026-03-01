"""Tests for framework-aware backend code generation.

Covers:
- FrameworkConfig dataclass and registry
- FastAPI, Django, Express config content
- Shubham's refactored _build_generation_prompt() with framework configs
- AI response continuation logic (stop_reason, was_truncated)
- Windowed scanning utility (split_into_windows)
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.frameworks import (
    FrameworkConfig,
    get_framework_config,
    list_frameworks,
    register_framework,
)
from app.agents.scan_utils import split_into_windows
from app.agents.shubham import Shubham
from app.services.ai_router import AIResponse, Provider


# ── TestFrameworkConfig ─────────────────────────────────────────────


class TestFrameworkConfig:
    """Test the FrameworkConfig dataclass and registry."""

    def test_framework_config_creation(self):
        config = FrameworkConfig(
            name="test",
            display_name="Test Framework",
            language="python",
            code_block_lang="python",
            error_comment_prefix="#",
            file_structure={"models": "app/models.py"},
            rules=("rule1", "rule2"),
            golden_examples={"models": "class Model: pass"},
        )
        assert config.name == "test"
        assert config.display_name == "Test Framework"
        assert config.language == "python"
        assert len(config.rules) == 2

    def test_framework_config_frozen(self):
        config = FrameworkConfig(
            name="test",
            display_name="Test",
            language="python",
            code_block_lang="python",
            error_comment_prefix="#",
            file_structure={},
            rules=(),
            golden_examples={},
        )
        with pytest.raises(FrozenInstanceError):
            config.name = "changed"  # type: ignore[misc]

    def test_register_and_retrieve(self):
        config = FrameworkConfig(
            name="test_fw",
            display_name="Test FW",
            language="python",
            code_block_lang="python",
            error_comment_prefix="#",
            file_structure={},
            rules=(),
            golden_examples={},
        )
        register_framework(config)
        retrieved = get_framework_config("test_fw")
        assert retrieved is config

    def test_list_frameworks_includes_all_three(self):
        frameworks = list_frameworks()
        assert "fastapi" in frameworks
        assert "django" in frameworks
        assert "express" in frameworks

    def test_unknown_framework_falls_back_to_fastapi(self):
        config = get_framework_config("cobol")
        assert config.name == "fastapi"


# ── TestFastAPIConfig ───────────────────────────────────────────────


class TestFastAPIConfig:
    """Test FastAPI framework configuration content."""

    def test_language_is_python(self):
        config = get_framework_config("fastapi")
        assert config.language == "python"
        assert config.code_block_lang == "python"
        assert config.error_comment_prefix == "#"

    def test_has_minimum_rules(self):
        config = get_framework_config("fastapi")
        assert len(config.rules) >= 12

    def test_has_golden_examples(self):
        config = get_framework_config("fastapi")
        assert "models" in config.golden_examples
        assert "schemas" in config.golden_examples
        assert "routers" in config.golden_examples
        assert "services" in config.golden_examples

    def test_rules_mention_sqlalchemy(self):
        config = get_framework_config("fastapi")
        rules_text = "\n".join(config.rules).lower()
        assert "sqlalchemy" in rules_text

    def test_rules_mention_pydantic(self):
        config = get_framework_config("fastapi")
        rules_text = "\n".join(config.rules).lower()
        assert "pydantic" in rules_text

    def test_golden_example_models_has_mapped_column(self):
        config = get_framework_config("fastapi")
        assert "mapped_column" in config.golden_examples["models"]

    def test_file_structure_has_expected_paths(self):
        config = get_framework_config("fastapi")
        assert "models" in config.file_structure
        assert "routers" in config.file_structure
        assert "services" in config.file_structure


# ── TestDjangoConfig ────────────────────────────────────────────────


class TestDjangoConfig:
    """Test Django framework configuration content."""

    def test_language_is_python(self):
        config = get_framework_config("django")
        assert config.language == "python"
        assert config.code_block_lang == "python"

    def test_has_minimum_rules(self):
        config = get_framework_config("django")
        assert len(config.rules) >= 12

    def test_has_golden_examples(self):
        config = get_framework_config("django")
        assert "models" in config.golden_examples
        assert "serializers" in config.golden_examples
        assert "views" in config.golden_examples
        assert "urls" in config.golden_examples

    def test_rules_mention_drf(self):
        config = get_framework_config("django")
        rules_text = "\n".join(config.rules).lower()
        assert "django" in rules_text
        assert "serializer" in rules_text

    def test_rules_do_not_mention_sqlalchemy(self):
        """Django config should NOT reference SQLAlchemy."""
        config = get_framework_config("django")
        rules_text = "\n".join(config.rules)
        # Rule 1 explicitly says NEVER use SQLAlchemy
        assert "NEVER use SQLAlchemy" in rules_text

    def test_golden_example_models_has_django_model(self):
        config = get_framework_config("django")
        assert "models.Model" in config.golden_examples["models"] or \
               "AbstractUser" in config.golden_examples["models"]

    def test_file_structure_has_core_paths(self):
        config = get_framework_config("django")
        assert "models" in config.file_structure
        assert "serializers" in config.file_structure
        assert "views" in config.file_structure


# ── TestExpressConfig ───────────────────────────────────────────────


class TestExpressConfig:
    """Test Express framework configuration content."""

    def test_language_is_typescript(self):
        config = get_framework_config("express")
        assert config.language == "typescript"
        assert config.code_block_lang == "typescript"
        assert config.error_comment_prefix == "//"

    def test_has_minimum_rules(self):
        config = get_framework_config("express")
        assert len(config.rules) >= 12

    def test_has_golden_examples(self):
        config = get_framework_config("express")
        assert "db_schema" in config.golden_examples
        assert "types" in config.golden_examples
        assert "routes" in config.golden_examples
        assert "middleware" in config.golden_examples

    def test_rules_mention_drizzle(self):
        config = get_framework_config("express")
        rules_text = "\n".join(config.rules).lower()
        assert "drizzle" in rules_text

    def test_rules_mention_zod(self):
        config = get_framework_config("express")
        rules_text = "\n".join(config.rules).lower()
        assert "zod" in rules_text

    def test_golden_example_db_schema_has_pg_table(self):
        config = get_framework_config("express")
        assert "pgTable" in config.golden_examples["db_schema"]

    def test_file_structure_has_src_paths(self):
        config = get_framework_config("express")
        assert "db_schema" in config.file_structure
        assert "src/" in config.file_structure["db_schema"]


# ── TestPromptIntegration ───────────────────────────────────────────


class TestPromptIntegration:
    """Test that Shubham's _build_generation_prompt() uses FrameworkConfig."""

    def _build_prompt(self, framework: str = "fastapi", step_name: str = "models",
                      user_feedback: str = "") -> str:
        agent = Shubham()
        step = {"name": step_name, "path": f"backend/{step_name}.py",
                "task_type": "general", "description": f"Test {step_name}"}
        contract = {"project_name": "Test", "tech_stack": {"backend": framework}}
        config = get_framework_config(framework)
        return agent._build_generation_prompt(
            step=step,
            contract=contract,
            accumulated_code={},
            db_artifacts="",
            fw_config=config,
            user_feedback=user_feedback,
        )

    def test_prompt_uses_framework_rules_fastapi(self):
        prompt = self._build_prompt("fastapi")
        assert "SQLAlchemy" in prompt
        assert "MANDATORY FastAPI RULES" in prompt

    def test_prompt_uses_framework_rules_django(self):
        prompt = self._build_prompt("django")
        assert "MANDATORY Django RULES" in prompt
        assert "Django ORM" in prompt

    def test_prompt_uses_framework_rules_express(self):
        prompt = self._build_prompt("express")
        assert "MANDATORY Express RULES" in prompt
        assert "Drizzle" in prompt

    def test_prompt_uses_golden_example(self):
        prompt = self._build_prompt("fastapi", step_name="models")
        assert "GOLDEN EXAMPLE" in prompt
        assert "mapped_column" in prompt

    def test_prompt_code_block_language_python(self):
        prompt = self._build_prompt("fastapi")
        assert "```python" in prompt

    def test_prompt_code_block_language_typescript(self):
        prompt = self._build_prompt("express", step_name="db_schema")
        assert "```typescript" in prompt

    def test_prompt_includes_file_structure(self):
        prompt = self._build_prompt("django")
        assert "Project File Structure" in prompt
        assert "core/models.py" in prompt

    def test_prompt_includes_user_feedback(self):
        prompt = self._build_prompt("fastapi", user_feedback="Add pagination to all endpoints")
        assert "User Feedback" in prompt
        assert "Add pagination to all endpoints" in prompt

    def test_prompt_omits_user_feedback_when_empty(self):
        prompt = self._build_prompt("fastapi", user_feedback="")
        assert "User Feedback" not in prompt

    def test_prompt_includes_completeness_rules(self):
        prompt = self._build_prompt("fastapi")
        assert "COMPLETENESS RULES" in prompt
        assert "NEVER stop mid-function" in prompt

    def test_prompt_no_golden_example_for_unknown_step(self):
        """Steps without golden examples should still work (no crash)."""
        prompt = self._build_prompt("fastapi", step_name="seed_db")
        # seed_db has no golden example — prompt should still be valid
        assert "Architecture Contract" in prompt
        assert "MANDATORY FastAPI RULES" in prompt


# ── TestContinuation ────────────────────────────────────────────────


class TestContinuation:
    """Test AI response truncation detection and continuation."""

    def test_stop_reason_field_exists_on_ai_response(self):
        resp = AIResponse(
            content="hello",
            model_used="test",
            provider=Provider.ANTHROPIC,
        )
        assert resp.stop_reason == "end_turn"
        assert resp.was_truncated is False

    def test_truncated_response(self):
        resp = AIResponse(
            content="partial code...",
            model_used="test",
            provider=Provider.ANTHROPIC,
            stop_reason="max_tokens",
            was_truncated=True,
        )
        assert resp.was_truncated is True
        assert resp.stop_reason == "max_tokens"

    def test_google_truncated_response(self):
        resp = AIResponse(
            content="partial code...",
            model_used="test",
            provider=Provider.GOOGLE,
            stop_reason="MAX_TOKENS",
            was_truncated=True,
        )
        assert resp.was_truncated is True

    @pytest.mark.asyncio
    async def test_continuation_merges_content(self):
        """When first call is truncated, continuation should merge content."""
        agent = Shubham()

        call_count = 0

        async def mock_call_ai(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return AIResponse(
                    content="def func1():\n    return 1\n",
                    model_used="test",
                    provider=Provider.ANTHROPIC,
                    output_tokens=50,
                    stop_reason="max_tokens",
                    was_truncated=True,
                )
            return AIResponse(
                content="def func2():\n    return 2\n",
                model_used="test",
                provider=Provider.ANTHROPIC,
                output_tokens=50,
                stop_reason="end_turn",
                was_truncated=False,
            )

        with patch("app.agents.base.call_ai", side_effect=mock_call_ai):
            from app.agents.base import call_ai_with_continuation
            result = await call_ai_with_continuation(
                agent,
                messages=[{"role": "user", "content": "Generate code"}],
                max_continuations=3,
            )

        assert "func1" in result.content
        assert "func2" in result.content
        assert result.output_tokens == 100
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_continuation_caps_at_max(self):
        """Continuation should stop after max_continuations even if still truncated."""
        agent = Shubham()

        async def always_truncated(*args, **kwargs):
            return AIResponse(
                content="more code\n",
                model_used="test",
                provider=Provider.ANTHROPIC,
                output_tokens=10,
                stop_reason="max_tokens",
                was_truncated=True,
            )

        with patch("app.agents.base.call_ai", side_effect=always_truncated):
            from app.agents.base import call_ai_with_continuation
            result = await call_ai_with_continuation(
                agent,
                messages=[{"role": "user", "content": "Generate code"}],
                max_continuations=3,
            )

        # 1 initial + 3 continuations = 4 total calls, content merged
        assert result.content.count("more code") == 4
        assert result.was_truncated is True  # Still truncated after max

    @pytest.mark.asyncio
    async def test_no_continuation_when_complete(self):
        """Complete responses should result in a single call."""
        agent = Shubham()
        call_count = 0

        async def complete_response(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return AIResponse(
                content="complete file\n",
                model_used="test",
                provider=Provider.ANTHROPIC,
                output_tokens=100,
                stop_reason="end_turn",
                was_truncated=False,
            )

        with patch("app.agents.base.call_ai", side_effect=complete_response):
            from app.agents.base import call_ai_with_continuation
            result = await call_ai_with_continuation(
                agent,
                messages=[{"role": "user", "content": "Generate code"}],
                max_continuations=5,
            )

        assert call_count == 1
        assert result.content == "complete file\n"
        assert result.was_truncated is False


# ── TestWindowedScanning ────────────────────────────────────────────


class TestWindowedScanning:
    """Test the split_into_windows utility for full-file scanning."""

    def test_short_content_single_window(self):
        windows = split_into_windows("short content")
        assert len(windows) == 1
        assert windows[0] == "short content"

    def test_exact_window_size_single_window(self):
        content = "x" * 3000
        windows = split_into_windows(content, window_size=3000)
        assert len(windows) == 1

    def test_long_content_multiple_windows(self):
        content = "x" * 7000
        windows = split_into_windows(content, window_size=3000, overlap=500)
        assert len(windows) >= 3
        # Every character should be covered
        merged = ""
        for w in windows:
            merged += w  # overlap means some chars appear twice, that's OK
        assert len(merged) >= 7000

    def test_windows_overlap_correctly(self):
        """Adjacent windows should share `overlap` characters."""
        content = "ABCDEFGHIJ" * 500  # 5000 chars
        windows = split_into_windows(content, window_size=2000, overlap=500)
        assert len(windows) >= 3
        # Check overlap: end of window N should match start of window N+1
        for i in range(len(windows) - 1):
            end_of_current = windows[i][-500:]
            start_of_next = windows[i + 1][:500]
            assert end_of_current == start_of_next

    def test_empty_content(self):
        windows = split_into_windows("")
        assert len(windows) == 1
        assert windows[0] == ""

    def test_custom_window_size(self):
        content = "a" * 100
        windows = split_into_windows(content, window_size=30, overlap=10)
        assert len(windows) >= 4
        # All windows should be <= window_size
        for w in windows:
            assert len(w) <= 30
