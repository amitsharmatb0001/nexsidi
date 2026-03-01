"""Phase 1 tests: Infrastructure gaps 276-289.

Covers:
- Smart file splitting (estimate_file_complexity, split_generation_step)
- Parallel subagent generation (dependency graph, compute_parallel_levels)
- Model auto-selection by task size (select_model_for_generation)
- Cost tracking per project (ProjectCostTracker, CostEntry)
- Thinking token budget awareness (// 4 ratio)

Tests are BRUTAL: negative inputs, edge cases, boundary conditions,
empty data, huge data, unicode, special chars, security overrides.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agents.base import estimate_file_complexity
from app.agents.shubham import (
    DJANGO_DEPENDENCIES,
    EXPRESS_DEPENDENCIES,
    FASTAPI_DEPENDENCIES,
    Shubham,
    compute_parallel_levels,
    get_backend_generation_order,
    get_dependency_graph,
    split_generation_step,
)
from app.services.ai_router import (
    AIResponse,
    CostEntry,
    MODELS,
    ProjectCostTracker,
    Provider,
    SECURITY_CRITICAL_TASKS,
    select_model_for_generation,
)


# ── TestEstimateFileComplexity ─────────────────────────────────────


class TestEstimateFileComplexity:
    """Test estimate_file_complexity() static method."""

    def test_empty_contract_returns_base_estimate(self):
        """Empty contract → only base lines per step."""
        estimate = estimate_file_complexity({}, "models")
        assert estimate >= 30  # base for models is 30

    def test_models_step_with_many_tables(self):
        """More tables → higher line estimate for models."""
        contract = {"tables": [{"name": f"t{i}"} for i in range(10)]}
        estimate = estimate_file_complexity(contract, "models")
        # 30 base + 10 tables * 25 per table = 280
        assert estimate >= 250

    def test_routers_step_with_many_endpoints(self):
        """More endpoints → higher line estimate for routers."""
        contract = {"endpoints": [{"path": f"/ep{i}"} for i in range(20)]}
        estimate = estimate_file_complexity(contract, "routers")
        # 30 base + 20 endpoints * 20 per endpoint = 430
        assert estimate >= 400

    def test_unknown_step_name_uses_default_base(self):
        """Unknown step names get a default base of 50."""
        estimate = estimate_file_complexity({}, "custom_step_xyz")
        assert estimate == 50

    def test_zero_tables_and_endpoints(self):
        """Contract with empty lists → base estimate."""
        contract = {"tables": [], "endpoints": []}
        estimate = estimate_file_complexity(contract, "services")
        assert estimate >= 25  # base for services

    def test_non_list_tables_ignored(self):
        """If tables is not a list (e.g. dict), treat as 0."""
        contract = {"tables": "not_a_list"}
        estimate = estimate_file_complexity(contract, "models")
        assert estimate >= 30  # Just base, no multiplier

    def test_relationships_add_to_models_estimate(self):
        """Relationships add extra lines to model files."""
        contract = {
            "tables": [{"name": "a"}, {"name": "b"}],
            "relationships": [{"from": "a", "to": "b"}],
        }
        estimate = estimate_file_complexity(contract, "models")
        # 30 + 2*25 + 1*5 = 85
        assert estimate >= 80

    def test_features_dont_affect_models_estimate(self):
        """Features field doesn't have a multiplier for models."""
        contract = {"features": [f"feature_{i}" for i in range(100)]}
        estimate = estimate_file_complexity(contract, "models")
        assert estimate == 30  # base only, no feature multiplier on models

    def test_tests_step_scales_with_both_tables_and_endpoints(self):
        """Test step estimates scale with both tables and endpoints."""
        contract = {
            "tables": [{"name": f"t{i}"} for i in range(5)],
            "endpoints": [{"path": f"/ep{i}"} for i in range(10)],
        }
        estimate = estimate_file_complexity(contract, "tests")
        # 40 base + 5*10 + 10*25 = 340
        assert estimate >= 300

    def test_estimate_never_below_base(self):
        """Even with negative-like edge cases, estimate >= base."""
        contract = {"tables": [], "endpoints": [], "relationships": []}
        for step in ["models", "schemas", "routers", "services", "tests"]:
            estimate = estimate_file_complexity(contract, step)
            assert estimate >= 15  # All bases are >= 15


# ── TestSelectModelForGeneration ───────────────────────────────────


class TestSelectModelForGeneration:
    """Test select_model_for_generation() model routing by size."""

    def test_small_file_uses_flash(self):
        """<200 lines → gemini-flash."""
        model = select_model_for_generation(50)
        assert model == "gemini-flash"

    def test_medium_file_uses_sonnet(self):
        """200-499 lines → sonnet."""
        model = select_model_for_generation(300)
        assert model == "sonnet"

    def test_large_file_uses_opus(self):
        """500+ lines → opus."""
        model = select_model_for_generation(800)
        assert model == "opus"

    def test_boundary_199_uses_flash(self):
        """Exactly 199 lines → flash (boundary)."""
        assert select_model_for_generation(199) == "gemini-flash"

    def test_boundary_200_uses_sonnet(self):
        """Exactly 200 lines → sonnet (boundary)."""
        assert select_model_for_generation(200) == "sonnet"

    def test_boundary_499_uses_sonnet(self):
        """Exactly 499 lines → sonnet (boundary)."""
        assert select_model_for_generation(499) == "sonnet"

    def test_boundary_500_uses_opus(self):
        """Exactly 500 lines → opus (boundary)."""
        assert select_model_for_generation(500) == "opus"

    def test_zero_lines_uses_flash(self):
        """0 lines → flash."""
        assert select_model_for_generation(0) == "gemini-flash"

    def test_negative_lines_uses_flash(self):
        """Negative (shouldn't happen) → flash."""
        assert select_model_for_generation(-1) == "gemini-flash"

    def test_massive_file_uses_opus(self):
        """20000 lines → opus."""
        assert select_model_for_generation(20000) == "opus"

    def test_security_task_always_sonnet(self):
        """Security tasks override to sonnet regardless of size."""
        for task in SECURITY_CRITICAL_TASKS:
            model = select_model_for_generation(10, task_type=task)
            assert model == "sonnet", f"Security task {task} should use sonnet"

    def test_security_override_even_for_huge_files(self):
        """Security tasks use sonnet even for >500 line files (not opus)."""
        model = select_model_for_generation(5000, task_type="auth_code")
        assert model == "sonnet"

    def test_all_returned_models_exist_in_registry(self):
        """Every returned model key must exist in MODELS."""
        for size in [0, 50, 100, 199, 200, 300, 499, 500, 1000, 20000]:
            model = select_model_for_generation(size)
            assert model in MODELS, f"Model {model} not in MODELS registry"


# ── TestProjectCostTracker ─────────────────────────────────────────


class TestProjectCostTracker:
    """Test ProjectCostTracker cost calculation and breakdown."""

    def _make_response(self, model_id: str = "claude-sonnet-4-6-20250514",
                       provider: Provider = Provider.ANTHROPIC,
                       input_tokens: int = 1000, output_tokens: int = 500) -> AIResponse:
        return AIResponse(
            content="test",
            model_used=model_id,
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def test_empty_tracker(self):
        tracker = ProjectCostTracker("run-1")
        assert tracker.total_tokens == 0
        assert tracker.total_cost == 0.0
        assert tracker.call_count == 0
        assert tracker.pipeline_run_id == "run-1"

    def test_single_record(self):
        tracker = ProjectCostTracker("run-1")
        resp = self._make_response(input_tokens=1000, output_tokens=2000)
        entry = tracker.record(resp, agent_name="shubham", model_key="sonnet")

        assert entry.model_key == "sonnet"
        assert entry.agent_name == "shubham"
        assert entry.input_tokens == 1000
        assert entry.output_tokens == 2000
        assert entry.input_cost > 0
        assert entry.output_cost > 0
        assert entry.total_cost == entry.input_cost + entry.output_cost

        assert tracker.total_input_tokens == 1000
        assert tracker.total_output_tokens == 2000
        assert tracker.total_tokens == 3000
        assert tracker.call_count == 1

    def test_multiple_records_accumulate(self):
        tracker = ProjectCostTracker()
        tracker.record(self._make_response(input_tokens=100, output_tokens=200),
                       agent_name="a", model_key="sonnet")
        tracker.record(self._make_response(input_tokens=300, output_tokens=400),
                       agent_name="b", model_key="opus")

        assert tracker.total_input_tokens == 400
        assert tracker.total_output_tokens == 600
        assert tracker.call_count == 2

    def test_per_model_breakdown(self):
        tracker = ProjectCostTracker()
        tracker.record(self._make_response(input_tokens=100, output_tokens=200),
                       model_key="sonnet", agent_name="a")
        tracker.record(self._make_response(input_tokens=300, output_tokens=400),
                       model_key="sonnet", agent_name="b")
        tracker.record(self._make_response(input_tokens=50, output_tokens=100),
                       model_key="opus", agent_name="a")

        breakdown = tracker.per_model_breakdown()
        assert "sonnet" in breakdown
        assert "opus" in breakdown
        assert breakdown["sonnet"]["call_count"] == 2
        assert breakdown["sonnet"]["input_tokens"] == 400
        assert breakdown["opus"]["call_count"] == 1
        assert breakdown["opus"]["input_tokens"] == 50

    def test_per_agent_breakdown(self):
        tracker = ProjectCostTracker()
        tracker.record(self._make_response(input_tokens=100, output_tokens=200),
                       model_key="sonnet", agent_name="shubham")
        tracker.record(self._make_response(input_tokens=300, output_tokens=400),
                       model_key="opus", agent_name="shubham")
        tracker.record(self._make_response(input_tokens=50, output_tokens=100),
                       model_key="sonnet", agent_name="karan")

        breakdown = tracker.per_agent_breakdown()
        assert "shubham" in breakdown
        assert "karan" in breakdown
        assert breakdown["shubham"]["call_count"] == 2
        assert breakdown["karan"]["call_count"] == 1

    def test_summary_structure(self):
        tracker = ProjectCostTracker("pipeline-abc")
        tracker.record(self._make_response(), model_key="sonnet", agent_name="test")

        summary = tracker.summary()
        assert summary["pipeline_run_id"] == "pipeline-abc"
        assert "total_input_tokens" in summary
        assert "total_output_tokens" in summary
        assert "total_tokens" in summary
        assert "total_cost_usd" in summary
        assert "call_count" in summary
        assert "per_model" in summary
        assert "per_agent" in summary

    def test_auto_resolve_model_key(self):
        """When model_key is empty, resolve from model_id."""
        tracker = ProjectCostTracker()
        resp = self._make_response(model_id="claude-opus-4-6-20250514")
        entry = tracker.record(resp, agent_name="test")  # No model_key
        assert entry.model_key == "opus"

    def test_unknown_model_id_falls_back(self):
        """Unknown model_id falls back to sonnet pricing."""
        tracker = ProjectCostTracker()
        resp = self._make_response(model_id="unknown-model-xyz")
        entry = tracker.record(resp, agent_name="test")
        assert entry.model_key == "sonnet"  # Default fallback

    def test_cost_calculation_accuracy(self):
        """Verify cost math for known model."""
        tracker = ProjectCostTracker()
        resp = self._make_response(input_tokens=1000, output_tokens=1000)
        entry = tracker.record(resp, model_key="gemini-flash", agent_name="test")

        # gemini-flash: input=0.00015/1K, output=0.0006/1K
        expected_input = (1000 / 1000) * 0.00015
        expected_output = (1000 / 1000) * 0.0006
        assert abs(entry.input_cost - expected_input) < 1e-10
        assert abs(entry.output_cost - expected_output) < 1e-10
        assert abs(entry.total_cost - (expected_input + expected_output)) < 1e-10

    def test_zero_tokens_zero_cost(self):
        """Zero tokens → zero cost."""
        tracker = ProjectCostTracker()
        resp = self._make_response(input_tokens=0, output_tokens=0)
        entry = tracker.record(resp, model_key="opus", agent_name="test")
        assert entry.total_cost == 0.0
        assert tracker.total_cost == 0.0

    def test_per_project_isolation(self):
        """Two trackers with different pipeline IDs are independent."""
        t1 = ProjectCostTracker("run-1")
        t2 = ProjectCostTracker("run-2")

        t1.record(self._make_response(input_tokens=1000, output_tokens=500),
                   model_key="sonnet", agent_name="a")
        t2.record(self._make_response(input_tokens=200, output_tokens=100),
                   model_key="haiku", agent_name="b")

        assert t1.total_input_tokens == 1000
        assert t2.total_input_tokens == 200
        assert t1.call_count == 1
        assert t2.call_count == 1


# ── TestDependencyGraph ────────────────────────────────────────────


class TestDependencyGraph:
    """Test dependency graph and parallel level computation."""

    def test_fastapi_dependencies_exist(self):
        deps = get_dependency_graph("fastapi")
        assert "models" in deps
        assert deps["models"] == set()  # No deps
        assert "schemas" in deps
        assert "models" in deps["schemas"]

    def test_django_dependencies_exist(self):
        deps = get_dependency_graph("django")
        assert "models" in deps
        assert deps["models"] == set()
        assert "serializers" in deps
        assert "models" in deps["serializers"]

    def test_express_dependencies_exist(self):
        deps = get_dependency_graph("express")
        assert "db_schema" in deps
        assert deps["db_schema"] == set()
        assert "types" in deps
        assert "db_schema" in deps["types"]

    def test_unknown_framework_falls_back_to_fastapi(self):
        deps = get_dependency_graph("cobol")
        assert deps is FASTAPI_DEPENDENCIES

    def test_compute_levels_fastapi(self):
        order = get_backend_generation_order("fastapi")
        deps = get_dependency_graph("fastapi")
        levels = compute_parallel_levels(order, deps)

        # Level 0: models (no deps)
        assert len(levels) >= 3  # At least 3 levels
        assert levels[0][0]["name"] == "models"

        # routers and services should be in same level (both depend on same set)
        all_names = [[s["name"] for s in level] for level in levels]
        for level_names in all_names:
            if "routers" in level_names:
                assert "services" in level_names, "routers and services should be parallel"

    def test_compute_levels_all_sequential_if_linear(self):
        """If every step depends on the previous one, all levels have 1 step."""
        order = [
            {"name": "a", "path": "a.py", "task_type": "general", "description": "A"},
            {"name": "b", "path": "b.py", "task_type": "general", "description": "B"},
            {"name": "c", "path": "c.py", "task_type": "general", "description": "C"},
        ]
        deps = {"a": set(), "b": {"a"}, "c": {"b"}}
        levels = compute_parallel_levels(order, deps)

        assert len(levels) == 3
        assert len(levels[0]) == 1
        assert len(levels[1]) == 1
        assert len(levels[2]) == 1

    def test_compute_levels_all_parallel_if_no_deps(self):
        """If no step has deps, all run in one level."""
        order = [
            {"name": "a", "path": "a.py", "task_type": "general", "description": "A"},
            {"name": "b", "path": "b.py", "task_type": "general", "description": "B"},
            {"name": "c", "path": "c.py", "task_type": "general", "description": "C"},
        ]
        deps = {"a": set(), "b": set(), "c": set()}
        levels = compute_parallel_levels(order, deps)

        assert len(levels) == 1
        assert len(levels[0]) == 3

    def test_compute_levels_empty_order(self):
        levels = compute_parallel_levels([], {})
        assert levels == []

    def test_all_steps_covered_in_levels(self):
        """Every step from generation_order appears exactly once in levels."""
        for fw in ["fastapi", "django", "express"]:
            order = get_backend_generation_order(fw)
            deps = get_dependency_graph(fw)
            levels = compute_parallel_levels(order, deps)

            all_steps = set()
            for level in levels:
                for step in level:
                    assert step["name"] not in all_steps, f"Duplicate: {step['name']}"
                    all_steps.add(step["name"])

            expected = {s["name"] for s in order}
            assert all_steps == expected, f"Missing steps for {fw}: {expected - all_steps}"


# ── TestSplitGenerationStep ────────────────────────────────────────


class TestSplitGenerationStep:
    """Test split_generation_step() for large file splitting."""

    def test_small_contract_no_split(self):
        """Contract with <8 tables shouldn't split."""
        step = {"name": "models", "path": "backend/app/models.py",
                "task_type": "general", "description": "Models"}
        contract = {"tables": [{"name": "users"}, {"name": "products"}]}
        result = split_generation_step(step, contract)
        assert len(result) == 1
        assert result[0] is step

    def test_large_contract_splits_models(self):
        """Contract with many tables splits models step."""
        step = {"name": "models", "path": "backend/app/models.py",
                "task_type": "general", "description": "Models"}
        contract = {"tables": [{"name": f"table_{i}"} for i in range(25)]}
        result = split_generation_step(step, contract, threshold=200)
        assert len(result) > 1
        for sub in result:
            assert "models" in sub["name"]
            assert sub["task_type"] == "general"

    def test_non_splittable_step_not_split(self):
        """Steps like security/middleware are never split."""
        step = {"name": "security", "path": "backend/app/security.py",
                "task_type": "auth_code", "description": "Security"}
        contract = {"tables": [{"name": f"t{i}"} for i in range(50)]}
        result = split_generation_step(step, contract, threshold=50)
        assert len(result) == 1

    def test_empty_contract_no_split(self):
        step = {"name": "models", "path": "backend/app/models.py",
                "task_type": "general", "description": "Models"}
        result = split_generation_step(step, {})
        assert len(result) == 1

    def test_split_generates_correct_paths_py(self):
        """Sub-step paths end with _partN.py."""
        step = {"name": "models", "path": "backend/app/models.py",
                "task_type": "general", "description": "Models"}
        contract = {"tables": [{"name": f"t{i}"} for i in range(30)]}
        result = split_generation_step(step, contract, threshold=50)
        if len(result) > 1:
            for sub in result:
                assert "_part" in sub["path"]
                assert sub["path"].endswith(".py")

    def test_split_generates_correct_paths_ts(self):
        """Sub-step paths end with _partN.ts for TypeScript."""
        step = {"name": "db_schema", "path": "backend/src/db/schema.ts",
                "task_type": "general", "description": "DB Schema"}
        contract = {"tables": [{"name": f"t{i}"} for i in range(30)]}
        result = split_generation_step(step, contract, threshold=50)
        if len(result) > 1:
            for sub in result:
                assert "_part" in sub["path"]
                assert sub["path"].endswith(".ts")

    def test_split_with_single_table_no_split(self):
        """Even with high estimate, 1 table can't be split further."""
        step = {"name": "models", "path": "backend/app/models.py",
                "task_type": "general", "description": "Models"}
        contract = {"tables": [{"name": "massive_table"}],
                     "endpoints": [{"path": f"/ep{i}"} for i in range(50)]}
        result = split_generation_step(step, contract, threshold=10)
        assert len(result) == 1

    def test_split_high_threshold_no_split(self):
        """Very high threshold → never split."""
        step = {"name": "models", "path": "backend/app/models.py",
                "task_type": "general", "description": "Models"}
        contract = {"tables": [{"name": f"t{i}"} for i in range(100)]}
        result = split_generation_step(step, contract, threshold=999999)
        assert len(result) == 1


# ── TestThinkingTokenBudget ────────────────────────────────────────


class TestThinkingTokenBudget:
    """Test that thinking budget uses // 4, not // 2."""

    def test_thinking_budget_is_quarter(self):
        """Thinking budget should be min(10000, max_tokens // 4)."""
        from app.services.ai_router import AIRequest, AIRouter, TaskComplexity

        router = AIRouter.__new__(AIRouter)
        spec = MODELS["sonnet"]  # supports_thinking = True

        request = AIRequest(
            messages=[],
            enable_thinking=True,
            max_tokens=64000,
        )
        body = router._build_anthropic_body(spec, request)

        # Budget should be min(10000, 64000 // 4) = min(10000, 16000) = 10000
        assert body["thinking"]["budget_tokens"] == 10000

    def test_thinking_budget_small_max_tokens(self):
        """With small max_tokens, budget = max_tokens // 4."""
        from app.services.ai_router import AIRequest, AIRouter

        router = AIRouter.__new__(AIRouter)
        spec = MODELS["sonnet"]

        request = AIRequest(
            messages=[],
            enable_thinking=True,
            max_tokens=2000,
        )
        body = router._build_anthropic_body(spec, request)

        # THINK-FIX: Budget is now max(1024, min(10000, 2000 // 4)) = max(1024, 500) = 1024
        # Minimum of 1024 prevents useless zero/tiny thinking budgets
        assert body["thinking"]["budget_tokens"] == 1024

    def test_thinking_budget_not_half(self):
        """Verify budget is NOT // 2 (the old, wrong behavior)."""
        from app.services.ai_router import AIRequest, AIRouter

        router = AIRouter.__new__(AIRouter)
        spec = MODELS["sonnet"]

        request = AIRequest(
            messages=[],
            enable_thinking=True,
            max_tokens=40000,
        )
        body = router._build_anthropic_body(spec, request)

        # If it were // 2 it would be min(10000, 20000) = 10000
        # With // 4 it's min(10000, 10000) = 10000 — same at this value
        # Try smaller value to differentiate
        request2 = AIRequest(
            messages=[],
            enable_thinking=True,
            max_tokens=8000,
        )
        body2 = router._build_anthropic_body(spec, request2)

        # // 4: min(10000, 2000) = 2000
        # // 2 would be: min(10000, 4000) = 4000
        assert body2["thinking"]["budget_tokens"] == 2000

    def test_no_thinking_when_disabled(self):
        """When enable_thinking=False, no thinking config in body."""
        from app.services.ai_router import AIRequest, AIRouter

        router = AIRouter.__new__(AIRouter)
        spec = MODELS["sonnet"]

        request = AIRequest(
            messages=[],
            enable_thinking=False,
            max_tokens=64000,
        )
        body = router._build_anthropic_body(spec, request)
        assert "thinking" not in body

    def test_no_thinking_for_non_supporting_model(self):
        """Models without thinking support should not get thinking config."""
        from app.services.ai_router import AIRequest, AIRouter

        router = AIRouter.__new__(AIRouter)
        spec = MODELS["haiku"]  # supports_thinking = False

        request = AIRequest(
            messages=[],
            enable_thinking=True,
            max_tokens=8000,
        )
        body = router._build_anthropic_body(spec, request)
        assert "thinking" not in body


# ── TestCostEntry ──────────────────────────────────────────────────


class TestCostEntry:
    """Test CostEntry dataclass."""

    def test_cost_entry_fields(self):
        entry = CostEntry(
            model_key="sonnet",
            agent_name="shubham",
            input_tokens=1000,
            output_tokens=2000,
            input_cost=0.003,
            output_cost=0.030,
            total_cost=0.033,
        )
        assert entry.model_key == "sonnet"
        assert entry.agent_name == "shubham"
        assert entry.total_cost == 0.033

    def test_cost_entry_default_timestamp(self):
        import time
        before = time.time()
        entry = CostEntry(
            model_key="x", agent_name="y",
            input_tokens=0, output_tokens=0,
            input_cost=0, output_cost=0, total_cost=0,
        )
        after = time.time()
        # M6-FIX: Default timestamp now uses wall-clock time.time()
        assert before <= entry.timestamp <= after


# ── TestShubhamIntegration ─────────────────────────────────────────


class TestShubhamIntegration:
    """Integration tests: Shubham uses parallel levels, model selection, cost tracking."""

    def test_shubham_has_generate_step_method(self):
        agent = Shubham()
        assert hasattr(agent, "_generate_step")
        assert hasattr(agent, "_generate_step_isolated")

    def test_estimate_file_complexity_available(self):
        """estimate_file_complexity is a standalone function in base."""
        estimate = estimate_file_complexity(
            {"tables": [{"name": "users"}]},
            "models",
        )
        assert estimate >= 30

    @pytest.mark.asyncio
    async def test_generate_step_isolated_returns_tuple(self):
        """_generate_step_isolated returns (name, content) tuple."""
        agent = Shubham()

        async def mock_continuation(*args, **kwargs):
            return AIResponse(
                content="generated_code",
                model_used="test",
                provider=Provider.ANTHROPIC,
                output_tokens=100,
            )

        with patch("app.agents.shubham.call_ai_with_continuation", side_effect=mock_continuation):
            from app.agents.frameworks import get_framework_config
            fw_config = get_framework_config("fastapi")

            result = await agent._generate_step_isolated(
                step={"name": "models", "path": "models.py", "task_type": "general",
                      "description": "test models"},
                contract={"project_name": "Test", "tech_stack": {"backend": "fastapi"}},
                accumulated_code={},
                db_artifacts="",
                fw_config=fw_config,
                user_feedback="",
            )

            assert isinstance(result, tuple)
            assert result[0] == "models"
            assert result[1] == "generated_code"

    @pytest.mark.asyncio
    async def test_generate_step_with_cost_tracker(self):
        """_generate_step records cost when tracker provided."""
        agent = Shubham()
        tracker = ProjectCostTracker("test-run")

        async def mock_continuation(*args, **kwargs):
            return AIResponse(
                content="code here",
                model_used="claude-sonnet-4-6-20250514",
                provider=Provider.ANTHROPIC,
                input_tokens=500,
                output_tokens=1000,
            )

        with patch("app.agents.shubham.call_ai_with_continuation", side_effect=mock_continuation):
            from app.agents.frameworks import get_framework_config
            fw_config = get_framework_config("fastapi")

            accumulated = {}
            generated = {}

            await agent._generate_step(
                step={"name": "models", "path": "models.py", "task_type": "general",
                      "description": "test models"},
                contract={"project_name": "Test", "tech_stack": {"backend": "fastapi"}},
                accumulated_code=accumulated,
                generated_files=generated,
                db_artifacts="",
                fw_config=fw_config,
                user_feedback="",
                cost_tracker=tracker,
            )

            assert tracker.call_count == 1
            assert tracker.total_input_tokens == 500
            assert tracker.total_output_tokens == 1000
            assert "models" in accumulated
            assert "models.py" in generated
