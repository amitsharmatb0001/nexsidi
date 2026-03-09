"""LangGraph-based agentic pipeline — replaces the linear while-loop orchestrator.

Uses LangGraph StateGraph with:
- Typed state (PipelineState) persisted via langgraph-checkpoint-postgres
- Conditional edges for dynamic routing (fix-retest, challenge-retry, skip rules)
- Parallel execution (quality_review = Karan + Navya + Deepika)
- Crash recovery (resume from last checkpoint)
- Post-deployment live verification node

Feature-flagged: settings.enable_langgraph controls activation.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Literal

from langgraph.graph import END, StateGraph

logger = logging.getLogger("nexsidi.pipeline_graph")


# ── Pipeline State ─────────────────────────────────────────────────

from typing import TypedDict


class PipelineState(TypedDict, total=False):
    """Typed state flowing through the LangGraph pipeline.

    Persisted after every node via langgraph-checkpoint-postgres.
    On crash, pipeline resumes from the last committed state.
    """

    run_id: str
    organization_id: str
    user_id: str
    project_id: str

    # Accumulated agent outputs (agent_name → output dict)
    context: dict[str, Any]

    # Pipeline control
    status: str  # running, paused, completed, failed
    current_stage: str
    last_agent_result: dict[str, Any] | None
    error: str | None

    # Loop counters
    fix_retest_cycle: int
    challenge_retry_count: int
    fix_severity: str  # TRIVIAL, MODERATE, STRUCTURAL

    # User interaction
    checkpoint_approved: bool
    execution_mode: str  # DIRECT, CHECKPOINT, STEP_BY_STEP

    # Dynamic routing (set by Tilotma or conditional edges)
    route_suggestion: str | None
    skip_flags: dict[str, bool]  # __skip_frontend__, __skip_database__, etc.


# ── Node Functions (one per stage) ──────────────────────────────────

async def _run_agent_node(
    state: PipelineState,
    agent_name: str,
    stage_name: str,
    *,
    context_key: str | None = None,
) -> PipelineState:
    """Execute a single agent and update pipeline state.

    Handles:
    - Context dependency injection (agents only see what they need)
    - Mistake memory (inject past lessons into prompt)
    - Output storage in context
    - Pipeline event emission

    V5-FIX (CASING-2): Added ``context_key`` parameter.  When provided,
    output is stored under ``context[context_key]`` instead of
    ``context[agent_name]``.  This prevents tilotma_review from
    overwriting tilotma's original requirements output.
    """
    from app.agents import get_agent_instance
    from app.agents.base import run_agent

    _output_key = context_key or agent_name
    run_id = state["run_id"]
    context = state.get("context", {})

    logger.info("graph_node_start", extra={"stage": stage_name, "agent": agent_name, "run_id": run_id})

    try:
        agent = get_agent_instance(agent_name)
        result = await run_agent(agent, run_id, context)

        # Store agent output in context under the designated key
        new_context = {**context}
        if result.output:
            new_context[_output_key] = result.output

        # Extract route suggestion if agent directed routing
        route = result.route_to if hasattr(result, "route_to") else None

        # Extract fix severity from fixer output
        fix_severity = state.get("fix_severity", "STRUCTURAL")
        if agent_name == "fixer" and result.output:
            fix_severity = result.output.get("fix_severity", "STRUCTURAL")

        return {
            **state,
            "context": new_context,
            "current_stage": stage_name,
            "last_agent_result": {
                "agent": agent_name,
                "status": result.status.value,
                "output_keys": list(result.output.keys()) if result.output else [],
                "model_used": result.model_used,
                "duration_ms": result.duration_ms,
            },
            "route_suggestion": route,
            "fix_severity": fix_severity,
            "error": result.error if result.status.value == "failed" else None,
        }

    except Exception as exc:
        logger.error("graph_node_failed", extra={"stage": stage_name, "agent": agent_name, "error": str(exc)[:200]})
        return {
            **state,
            "current_stage": stage_name,
            "error": str(exc)[:500],
            "status": "failed",
        }


# ── Individual Node Wrappers ────────────────────────────────────────

async def requirements_node(state: PipelineState) -> PipelineState:
    """Tilotma: Parse user input, generate requirements, decide skip flags."""
    result = await _run_agent_node(state, "tilotma", "requirements")
    # Tilotma can set skip flags based on requirements analysis
    ctx = result.get("context", {})
    tilotma_out = ctx.get("tilotma", {})
    skip_flags = result.get("skip_flags", {})
    if tilotma_out.get("__skip_frontend__"):
        skip_flags["__skip_frontend__"] = True
    if tilotma_out.get("__skip_database__"):
        skip_flags["__skip_database__"] = True
    result["skip_flags"] = skip_flags
    return result


async def analysis_node(state: PipelineState) -> PipelineState:
    """Saanvi: Market analysis, requirement refinement, complexity scoring."""
    return await _run_agent_node(state, "saanvi", "analysis")


async def architecture_node(state: PipelineState) -> PipelineState:
    """Vikram: System architecture, API contract, database schema."""
    return await _run_agent_node(state, "vikram", "architecture")


async def challenge_node(state: PipelineState) -> PipelineState:
    """Challenger: Stress-test architecture, find weaknesses."""
    return await _run_agent_node(state, "challenger", "architecture_review")


async def database_node(state: PipelineState) -> PipelineState:
    """Dhruv: Database schema, migrations, seed data."""
    return await _run_agent_node(state, "dhruv", "database_design")


async def ui_design_node(state: PipelineState) -> PipelineState:
    """Vanya: UI/UX design, component architecture."""
    return await _run_agent_node(state, "vanya", "ui_design")


async def checkpoint_design_node(state: PipelineState) -> PipelineState:
    """Compile Software Design Document (SDD) from all design phase outputs.

    SDD-FIX: Previously this was a blank pass-through.  Now it compiles a
    proper SDD from the outputs of Tilotma (requirements), Saanvi (analysis),
    Vikram (architecture contract), Challenger (review), Dhruv (database),
    and Vanya (UI design) — exactly like a real SDLC design checkpoint.

    The SDD is stored in context["__sdd__"] and is available to ALL
    downstream agents (Shubham, Aanya, Karan, etc.) as the single source
    of truth for what to build.

    In CHECKPOINT mode, the pipeline pauses here for user review of the SDD.
    In DIRECT mode, it auto-approves and continues to build.
    """
    context = state.get("context", {})
    run_id = state.get("run_id", "")

    # ── Gather outputs from all design-phase agents ──
    tilotma_out = context.get("tilotma", {})
    saanvi_out = context.get("saanvi", {})
    vikram_out = context.get("vikram", {})
    challenger_out = context.get("challenger", {})
    dhruv_out = context.get("dhruv", {})
    vanya_out = context.get("vanya", {})

    # Vikram's contract is the core architecture spec
    contract = vikram_out.get("contract", {})

    # ── Compile the SDD ──
    parsed_req = tilotma_out.get("parsed_requirements", {}) or {}

    sdd: dict[str, Any] = {
        "__type__": "SoftwareDesignDocument",
        "version": "1.0",
        "pipeline_run_id": run_id,

        # Section 1: Project Overview
        "project_overview": {
            "title": parsed_req.get("understanding", tilotma_out.get("raw_input", "")[:200]),
            "project_type": parsed_req.get("project_type", "web_app"),
            "key_features": parsed_req.get("key_features", []),
            "user_input": tilotma_out.get("raw_input", "")[:500],
            "confidence": parsed_req.get("confidence", 0),
            "assumptions": parsed_req.get("assumptions", []),
            "capability_validated": tilotma_out.get("capability_validated", False),
        },

        # Section 2: Market Analysis & Complexity
        "analysis": {
            "complexity_score": saanvi_out.get("complexity_score"),
            "market_analysis": saanvi_out.get("market_analysis", ""),
            "risk_factors": saanvi_out.get("risk_factors", []),
            "estimated_effort": saanvi_out.get("estimated_effort"),
        },

        # Section 3: System Architecture (from Vikram's contract)
        "architecture": {
            "tech_stack": contract.get("tech_stack", {}),
            "system_components": contract.get("system_components", []),
            "deployment_target": contract.get("deployment", {}),
            "architecture_review": {
                "verdict": challenger_out.get("verdict", ""),
                "has_critical": challenger_out.get("has_critical", False),
                "findings": challenger_out.get("findings", [])[:10],
            },
        },

        # Section 4: Database Design
        "database": {
            "tables": contract.get("database", {}).get("tables", []),
            "relationships": contract.get("database", {}).get("relationships", []),
            "database_type": dhruv_out.get("database", "postgresql"),
            "migrations_ready": bool(dhruv_out.get("migrations")),
            "seed_data_ready": bool(dhruv_out.get("seed_data")),
            "table_count": dhruv_out.get("table_count", 0),
        },

        # Section 5: API Specification
        "api": {
            "endpoints": contract.get("api", {}).get("endpoints", []),
            "auth_strategy": contract.get("api", {}).get("auth", {}),
            "middleware": contract.get("api", {}).get("middleware", []),
            "endpoint_count": len(contract.get("api", {}).get("endpoints", [])),
        },

        # Section 6: Frontend Design
        "frontend": {
            "pages": contract.get("frontend", {}).get("pages", []),
            "components": contract.get("frontend", {}).get("components", []),
            "design_spec": vanya_out.get("design_spec", ""),
            "design_tokens_validated": vanya_out.get("tokens_validated", False),
            "page_count": vanya_out.get("page_count", 0),
        },

        # Section 7: Security & Compliance Requirements
        "security": {
            "compliance_flags": tilotma_out.get("compliance_auto_detected", []),
            "non_functional": parsed_req.get("non_functional", []),
            "auth_required": bool(contract.get("api", {}).get("auth")),
        },

        # Section 8: User Roles & Permissions
        "user_roles": parsed_req.get("user_roles", []),

        # Section 9: Integrations
        "integrations": parsed_req.get("integrations", []),

        # Section 10: Build Statistics
        "stats": {
            "tables": vikram_out.get("stats", {}).get("tables", 0),
            "endpoints": vikram_out.get("stats", {}).get("endpoints", 0),
            "pages": vikram_out.get("stats", {}).get("pages", 0),
            "contract_valid": vikram_out.get("is_valid", False),
        },
    }

    # Store SDD in context (accessible to ALL downstream agents)
    new_context = {**context, "__sdd__": sdd}

    logger.info(
        "sdd_compiled",
        run_id=run_id,
        tables=sdd["stats"]["tables"],
        endpoints=sdd["stats"]["endpoints"],
        pages=sdd["stats"]["pages"],
        project_type=sdd["project_overview"]["project_type"],
    )

    # Emit SDD event for Live Studio
    try:
        from app.services.pipeline_events import publish_agent_thinking_event
        await publish_agent_thinking_event(
            run_id, "checkpoint_design", "complete",
            detail=(
                f"SDD compiled: {sdd['stats']['tables']} tables, "
                f"{sdd['stats']['endpoints']} endpoints, "
                f"{sdd['stats']['pages']} pages — "
                f"project type: {sdd['project_overview']['project_type']}"
            ),
        )
    except Exception:
        pass  # Non-fatal

    # ── Checkpoint gate ──
    mode = state.get("execution_mode", "DIRECT")
    if mode == "DIRECT":
        return {**state, "context": new_context, "checkpoint_approved": True}

    # In CHECKPOINT/STEP_BY_STEP mode, pause for user review of the SDD
    return {**state, "context": new_context, "status": "paused", "checkpoint_approved": False}


async def backend_build_node(state: PipelineState) -> PipelineState:
    """Shubham: Generate backend code with real tools."""
    return await _run_agent_node(state, "shubham", "backend_build")


async def frontend_build_node(state: PipelineState) -> PipelineState:
    """Aanya: Generate frontend code with real tools."""
    return await _run_agent_node(state, "aanya", "frontend_build")


async def quality_review_node(state: PipelineState) -> PipelineState:
    """Parallel: Karan (security) + Navya (logic) + Deepika (performance)."""
    from app.agents import get_agent_instance
    from app.agents.base import run_agent

    run_id = state["run_id"]
    context = state.get("context", {})

    agents = ["karan", "navya", "deepika"]

    async def run_one(name: str) -> tuple[str, dict]:
        try:
            agent = get_agent_instance(name)
            result = await run_agent(agent, run_id, context)
            return name, result.output or {}
        except Exception as exc:
            logger.error("parallel_agent_failed", extra={"agent": name, "error": str(exc)[:200]})
            return name, {"error": str(exc)[:200]}

    # Run all three in parallel
    tasks = [run_one(name) for name in agents]
    results = await asyncio.gather(*tasks)

    new_context = {**context}
    for name, output in results:
        new_context[name] = output

    return {
        **state,
        "context": new_context,
        "current_stage": "quality_review",
    }


async def testing_node(state: PipelineState) -> PipelineState:
    """Aarav: Run endpoint tests, integration tests."""
    return await _run_agent_node(state, "aarav", "testing")


async def security_audit_node(state: PipelineState) -> PipelineState:
    """Karan: Deep security audit + attack testing."""
    return await _run_agent_node(state, "karan", "security_audit")


async def compliance_node(state: PipelineState) -> PipelineState:
    """Karan: Compliance checks (OWASP, GDPR, etc.)."""
    return await _run_agent_node(state, "karan", "compliance_check")


async def tilotma_review_node(state: PipelineState) -> PipelineState:
    """Tilotma: Final GO/NO-GO review with all agent reports.

    V5-FIX (CASING-2): Store output under ``"tilotma_review"`` key so
    it does NOT overwrite the original ``"tilotma"`` requirements output.
    Also use ``context_key="tilotma_review"`` for filtering so the agent
    receives the correct deps from ``_AGENT_CONTEXT_DEPS["tilotma_review"]``
    (vikram, shubham, aanya, aarav, attack_tester, karan, navya, deepika, saanvi).
    """
    return await _run_agent_node(
        state, "tilotma", "tilotma_review", context_key="tilotma_review",
    )


async def fixing_node(state: PipelineState) -> PipelineState:
    """Fixer: Autonomous code repairs based on issues found."""
    return await _run_agent_node(state, "fixer", "fixing")


async def checkpoint_testing_node(state: PipelineState) -> PipelineState:
    """Pause for user approval before deployment (if in CHECKPOINT mode)."""
    mode = state.get("execution_mode", "DIRECT")
    if mode == "DIRECT":
        return {**state, "checkpoint_approved": True}
    return {**state, "status": "paused", "checkpoint_approved": False}


async def deployment_node(state: PipelineState) -> PipelineState:
    """Pranav: Deploy to GCP Cloud Run."""
    return await _run_agent_node(state, "pranav", "deployment")


async def post_deploy_verify_node(state: PipelineState) -> PipelineState:
    """NEW: Re-run ALL tests against the LIVE deployed URL.

    This is the critical gap that was missing — previously there was only
    a 3-line smoke test after deployment. Now we:
    1. Hit every endpoint with full Postman-quality tests
    2. Verify frontend renders (if applicable)
    3. Check security headers on live URL
    4. Measure live latency
    """
    from app.services.live_verifier import LiveVerifier

    context = state.get("context", {})
    pranav_output = context.get("pranav", {})
    deployment_url = pranav_output.get("deployment_url", "")

    if not deployment_url:
        logger.warning("post_deploy_verify_skip: no deployment URL available")
        return {
            **state,
            "context": {**context, "post_deploy_verify": {"status": "skipped", "reason": "no_url"}},
            "current_stage": "post_deploy_verify",
        }

    try:
        verifier = LiveVerifier()

        # Get endpoint contract from Vikram's architecture
        vikram_output = context.get("vikram", {})
        contract_endpoints = vikram_output.get("endpoints", [])

        report = await verifier.verify_all_endpoints(deployment_url, contract_endpoints)

        new_context = {**context, "post_deploy_verify": report}

        return {
            **state,
            "context": new_context,
            "current_stage": "post_deploy_verify",
            "last_agent_result": {
                "agent": "live_verifier",
                "status": "completed",
                "output_keys": list(report.keys()),
            },
        }

    except Exception as exc:
        logger.error("post_deploy_verify_failed", extra={"error": str(exc)[:200]})
        return {
            **state,
            "context": {**context, "post_deploy_verify": {"status": "error", "error": str(exc)[:200]}},
            "current_stage": "post_deploy_verify",
        }


async def documentation_node(state: PipelineState) -> PipelineState:
    """DocsAgent: Generate README, API reference, setup guide from pipeline context.

    AUDIT-B1-FIX: docs_agent was registered but never called. DeliveryEngine
    expects context["docs_agent"] for bundling docs in the ZIP. Without this
    node, delivery packages were missing documentation.
    """
    return await _run_agent_node(state, "docs_agent", "documentation")


async def git_operations_node(state: PipelineState) -> PipelineState:
    """GitAgent: Create repo, push code, open PR.

    HIGH-2 FIX: GitAgent was registered (git_agent.py) and imported via
    agents/__init__.py but never wired into the pipeline graph.  Without
    this node, generated code was never pushed to a repository.
    """
    return await _run_agent_node(state, "git_agent", "git_operations")


async def delivery_node(state: PipelineState) -> PipelineState:
    """Assemble delivery package (ZIP with code, reports, config)."""
    # Delivery is handled by the existing delivery engine
    return {
        **state,
        "current_stage": "delivery",
        "status": "completed",
    }


# ── Conditional Edge Functions ──────────────────────────────────────

def route_after_challenge(state: PipelineState) -> str:
    """After architecture review: retry architecture or proceed."""
    result = state.get("last_agent_result", {})
    context = state.get("context", {})
    challenger_output = context.get("challenger", {})

    has_critical = challenger_output.get("has_critical", False)
    verdict = challenger_output.get("verdict", "approve")
    retry_count = state.get("challenge_retry_count", 0)

    if (has_critical or verdict == "reject") and retry_count < 2:
        logger.info("challenge_retry", extra={"retry": retry_count + 1})
        return "architecture"  # Retry Vikram with challenges as constraints

    return "database_or_skip"


def route_database_or_skip(state: PipelineState) -> str:
    """Skip database design if not needed."""
    skip_flags = state.get("skip_flags", {})
    if skip_flags.get("__skip_database__"):
        return "ui_or_skip"
    return "database"


def route_ui_or_skip(state: PipelineState) -> str:
    """Skip UI design if not needed (backend-only project)."""
    skip_flags = state.get("skip_flags", {})
    if skip_flags.get("__skip_frontend__"):
        return "checkpoint_design"
    return "ui_design"


def route_frontend_or_skip(state: PipelineState) -> str:
    """Skip frontend build if not needed."""
    skip_flags = state.get("skip_flags", {})
    if skip_flags.get("__skip_frontend__"):
        return "quality_review"
    return "frontend_build"


def route_after_tilotma_review(state: PipelineState) -> str:
    """Tilotma GO/NO-GO: approve → deploy, reject → fix.

    HIGH-6 FIX: Normalize verdict string robustly.  Tilotma might return
    "NO GO", "No-Go", "NOGO", "no_go", "REJECT", "rejected", "fail", etc.
    We normalize to a canonical form before matching against the rejection set.

    V5-FIX (CASING-2): Read from ``"tilotma_review"`` context key.
    V5-FIX (CASING-1): Collapse consecutive hyphens after normalization.
    """
    context = state.get("context", {})
    # V5-FIX: Read from tilotma_review key (not tilotma) to avoid
    # reading the requirements-phase output.
    tilotma_output = context.get("tilotma_review", {})

    raw_verdict = tilotma_output.get("verdict", "GO")
    if not isinstance(raw_verdict, str):
        raw_verdict = str(raw_verdict)

    # Normalize: uppercase, replace spaces/underscores with hyphens, strip
    normalized = raw_verdict.upper().strip().replace(" ", "-").replace("_", "-")
    # V5-FIX (CASING-1): Collapse consecutive hyphens so "NO - GO" →
    # "NO---GO" → "NO-GO" (match).  The old set had "NO--GO" which was
    # unreachable — no input could produce exactly two hyphens.
    import re as _re_verdict
    normalized = _re_verdict.sub(r"-{2,}", "-", normalized)

    _REJECTION_VERDICTS = {
        "NO-GO", "NOGO", "REJECT", "REJECTED",
        "FAIL", "FAILED", "BLOCK", "BLOCKED",
    }

    if normalized in _REJECTION_VERDICTS:
        return "fixing"
    return "checkpoint_testing"


def route_after_fixing(state: PipelineState) -> str:
    """Route after fixing based on severity: TRIVIAL→testing, MODERATE/STRUCTURAL→quality_review."""
    severity = state.get("fix_severity", "STRUCTURAL")
    if severity == "TRIVIAL":
        return "testing"
    return "quality_review"  # Both MODERATE and STRUCTURAL go through full review


def route_after_testing(state: PipelineState) -> str:
    """Route after testing: if failures found → fixing, else → security audit.

    V5-FIX (CRITICAL-10): Aarav outputs ``total_failed`` and ``total_tests``,
    NOT ``failures`` and ``pass_rate``.  The old code always read default (0/100)
    which meant test failures NEVER triggered the fix-retest loop from the
    testing stage.  This was the single most severe routing bug in the pipeline.
    """
    context = state.get("context", {})
    aarav_output = context.get("aarav", {})

    # V5-FIX: Read the correct keys from Aarav's output
    failures = aarav_output.get("total_failed", 0)
    total_tests = aarav_output.get("total_tests", 1)
    test_pass_rate = ((total_tests - failures) / max(total_tests, 1)) * 100

    if failures > 0 or test_pass_rate < 80:
        fix_cycle = state.get("fix_retest_cycle", 0)
        if fix_cycle >= 3:
            logger.warning("fix_retest_limit_reached", extra={"cycle": fix_cycle})
            return "security_audit"  # Proceed anyway after 3 fix cycles
        return "fixing"
    return "security_audit"


def route_after_post_deploy(state: PipelineState) -> str:
    """Route after post-deployment verification: critical failures → fixing, else → delivery.

    HIGH-7 FIX: Increment fix_retest_cycle BEFORE routing to fixing.
    Without this, the counter never advances and the 3-cycle safety cap
    never triggers → infinite fix loop on persistent post-deploy failures.

    V5-FIX (HIGH-9): Bridge post-deploy failures into ``__fix_items__``
    metadata so the Fixer can act on them.  The Fixer already receives
    ``post_deploy_verify`` in context (BLOCKER-3 FIX), but it expects
    file-level errors with ``file_path`` + ``error_message``.  Post-deploy
    failures are endpoint-level (HTTP 500, missing headers, timeouts).
    Without this bridge, the Fixer sees the verify output but can't
    extract actionable fix items → it runs an empty loop and exits.
    """
    context = state.get("context", {})
    verify_output = context.get("post_deploy_verify", {})

    critical_failures = verify_output.get("critical_failures", 0)
    if critical_failures > 0:
        fix_cycle = state.get("fix_retest_cycle", 0)
        if fix_cycle >= 3:
            logger.warning("post_deploy_fix_limit", extra={"cycle": fix_cycle})
            return "documentation"  # Proceed with warning after 3 cycles
        # Increment BEFORE routing to fixing — this is what was missing
        state["fix_retest_cycle"] = fix_cycle + 1
        logger.info(
            "post_deploy_fix_cycle_incremented",
            extra={"cycle": fix_cycle + 1, "critical_failures": critical_failures},
        )

        # V5-FIX (HIGH-9): Translate endpoint failures → fixer-compatible
        # __fix_items__.  Keys starting with __ are auto-included in all
        # agent contexts (pipeline.py L218: ``if k.startswith("__")``).
        _failed_eps = verify_output.get("failed_endpoints", [])
        _fix_items: list[dict[str, str]] = []
        for ep in _failed_eps:
            method = ep.get("method", "GET")
            path = ep.get("path", "/")
            status = ep.get("status", "error")
            detail = ep.get("detail", "unknown error")[:300]
            source_file = ep.get("likely_source_file", "")
            _fix_items.append({
                "file_path": source_file or f"routes/{path.strip('/').replace('/', '_') or 'root'}.py",
                "error_message": (
                    f"POST-DEPLOY FAILURE: {method} {path} "
                    f"returned {status} — {detail}"
                ),
                "severity": "HIGH",
                "source": "post_deploy_verify",
            })
        # Also include non-endpoint failures (security headers, CORS, etc.)
        _header_issues = verify_output.get("security_header_issues", [])
        for issue in _header_issues:
            _fix_items.append({
                "file_path": issue.get("file_path", "app/main.py"),
                "error_message": f"POST-DEPLOY SECURITY: {issue.get('detail', str(issue)[:300])}",
                "severity": "MEDIUM",
                "source": "post_deploy_verify",
            })
        if _fix_items:
            state.setdefault("context", {})["__fix_items__"] = _fix_items
            logger.info("post_deploy_fix_items_bridged", count=len(_fix_items))

        return "fixing"
    return "documentation"


# ── Graph Builder ──────────────────────────────────────────────────

def build_pipeline_graph() -> StateGraph:
    """Build the LangGraph StateGraph for the NexSidi pipeline.

    Node topology:
        requirements → analysis → architecture → challenge
        → [database] → [ui_design] → checkpoint_design
        → backend_build → [frontend_build] → quality_review
        → testing → security_audit → compliance → tilotma_review
        → [fixing loop] → checkpoint_testing → deployment
        → post_deploy_verify → documentation → git_operations → delivery → END

    Conditional edges:
        challenge → architecture (retry, max 2) or database_or_skip
        testing → fixing (if failures) or security_audit
        tilotma_review → fixing (reject) or checkpoint_testing (approve)
        fixing → quality_review (MODERATE/STRUCTURAL) or testing (TRIVIAL)
        post_deploy_verify → fixing (critical) or documentation
    """
    graph = StateGraph(PipelineState)

    # ── Add all nodes ──
    graph.add_node("requirements", requirements_node)
    graph.add_node("analysis", analysis_node)
    graph.add_node("architecture", architecture_node)
    graph.add_node("challenge", challenge_node)
    graph.add_node("database", database_node)
    graph.add_node("ui_design", ui_design_node)
    graph.add_node("checkpoint_design", checkpoint_design_node)
    graph.add_node("backend_build", backend_build_node)
    graph.add_node("frontend_build", frontend_build_node)
    graph.add_node("quality_review", quality_review_node)
    graph.add_node("testing", testing_node)
    graph.add_node("security_audit", security_audit_node)
    graph.add_node("compliance", compliance_node)
    graph.add_node("tilotma_review", tilotma_review_node)
    graph.add_node("fixing", fixing_node)
    graph.add_node("checkpoint_testing", checkpoint_testing_node)
    graph.add_node("deployment", deployment_node)
    graph.add_node("post_deploy_verify", post_deploy_verify_node)
    graph.add_node("documentation", documentation_node)  # AUDIT-B1-FIX
    graph.add_node("git_operations", git_operations_node)  # HIGH-2 FIX
    graph.add_node("delivery", delivery_node)

    # ── Set entry point ──
    graph.set_entry_point("requirements")

    # ── Sequential edges ──
    graph.add_edge("requirements", "analysis")
    graph.add_edge("analysis", "architecture")
    graph.add_edge("architecture", "challenge")

    # ── Challenge → retry or proceed ──
    graph.add_conditional_edges(
        "challenge",
        route_after_challenge,
        {
            "architecture": "architecture",
            "database_or_skip": "database",  # Will be re-evaluated by conditional
        },
    )

    # ── Skip-aware routing after challenge ──
    # We use an intermediate approach: challenge routes to database_or_skip
    # which is actually handled by the conditional edge mapping above.
    # For skip logic, we add conditional edges from database/ui_design:

    # Database → UI or skip
    graph.add_conditional_edges(
        "database",
        route_ui_or_skip,
        {
            "ui_design": "ui_design",
            "checkpoint_design": "checkpoint_design",
        },
    )

    # UI design → checkpoint
    graph.add_edge("ui_design", "checkpoint_design")

    # Checkpoint design → backend build
    graph.add_edge("checkpoint_design", "backend_build")

    # Backend build → frontend or skip
    graph.add_conditional_edges(
        "backend_build",
        route_frontend_or_skip,
        {
            "frontend_build": "frontend_build",
            "quality_review": "quality_review",
        },
    )

    # Frontend build → quality review
    graph.add_edge("frontend_build", "quality_review")

    # Quality review → testing
    graph.add_edge("quality_review", "testing")

    # Testing → fixing or security audit
    graph.add_conditional_edges(
        "testing",
        route_after_testing,
        {
            "fixing": "fixing",
            "security_audit": "security_audit",
        },
    )

    # Security audit → compliance
    graph.add_edge("security_audit", "compliance")

    # Compliance → tilotma review
    graph.add_edge("compliance", "tilotma_review")

    # Tilotma review → fixing or checkpoint_testing
    graph.add_conditional_edges(
        "tilotma_review",
        route_after_tilotma_review,
        {
            "fixing": "fixing",
            "checkpoint_testing": "checkpoint_testing",
        },
    )

    # Fixing → quality_review or testing (severity-based)
    graph.add_conditional_edges(
        "fixing",
        route_after_fixing,
        {
            "quality_review": "quality_review",
            "testing": "testing",
        },
    )

    # Checkpoint testing → deployment
    graph.add_edge("checkpoint_testing", "deployment")

    # Deployment → post-deploy verification
    graph.add_edge("deployment", "post_deploy_verify")

    # Post-deploy verification → fixing or documentation
    graph.add_conditional_edges(
        "post_deploy_verify",
        route_after_post_deploy,
        {
            "fixing": "fixing",
            "documentation": "documentation",  # AUDIT-B1-FIX: was "delivery"
        },
    )

    # Documentation → Git Operations → Delivery → END
    graph.add_edge("documentation", "git_operations")  # HIGH-2 FIX: was documentation→delivery
    graph.add_edge("git_operations", "delivery")
    graph.add_edge("delivery", END)

    return graph


# ── Compiled Graph (singleton) ──────────────────────────────────────

_compiled_graph = None


def get_compiled_graph():
    """Get or compile the pipeline graph (with optional PostgreSQL checkpointer)."""
    global _compiled_graph
    if _compiled_graph is not None:
        return _compiled_graph

    graph = build_pipeline_graph()

    # Try to set up PostgreSQL checkpointer for crash recovery
    try:
        from app.config import get_settings
        settings = get_settings()
        db_url = str(settings.database_url)
        if db_url:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            checkpointer = AsyncPostgresSaver.from_conn_string(db_url)
            _compiled_graph = graph.compile(checkpointer=checkpointer)
            logger.info("pipeline_graph_compiled_with_checkpointer")
            return _compiled_graph
    except Exception as exc:
        logger.warning("pipeline_graph_checkpointer_failed: %s — compiling without persistence", exc)

    # Fallback: compile without checkpointer (no crash recovery)
    _compiled_graph = graph.compile()
    logger.info("pipeline_graph_compiled_without_checkpointer")
    return _compiled_graph


async def run_pipeline_graph(
    run_id: str,
    organization_id: str,
    user_id: str,
    project_id: str,
    context: dict[str, Any] | None = None,
    execution_mode: str = "DIRECT",
) -> PipelineState:
    """Execute the full pipeline via LangGraph.

    Args:
        run_id: Unique pipeline run ID.
        organization_id: Organization ID.
        user_id: User who triggered the run.
        project_id: Project ID.
        context: Initial context (requirements, etc.).
        execution_mode: DIRECT, CHECKPOINT, or STEP_BY_STEP.

    Returns:
        Final PipelineState with all agent outputs.
    """
    compiled = get_compiled_graph()

    initial_state: PipelineState = {
        "run_id": run_id,
        "organization_id": organization_id,
        "user_id": user_id,
        "project_id": project_id,
        "context": context or {},
        "status": "running",
        "current_stage": "requirements",
        "last_agent_result": None,
        "error": None,
        "fix_retest_cycle": 0,
        "challenge_retry_count": 0,
        "fix_severity": "STRUCTURAL",
        "checkpoint_approved": False,
        "execution_mode": execution_mode,
        "route_suggestion": None,
        "skip_flags": {},
    }

    logger.info("pipeline_graph_start", extra={"run_id": run_id})
    start = time.monotonic()

    try:
        # Use thread_id for checkpointing (allows resume on crash)
        config = {"configurable": {"thread_id": run_id}}
        result = await compiled.ainvoke(initial_state, config=config)

        duration = time.monotonic() - start
        logger.info(
            "pipeline_graph_complete",
            extra={
                "run_id": run_id,
                "status": result.get("status", "unknown"),
                "duration_s": round(duration, 1),
            },
        )
        return result

    except Exception as exc:
        duration = time.monotonic() - start
        logger.error(
            "pipeline_graph_failed",
            extra={
                "run_id": run_id,
                "error": str(exc)[:500],
                "duration_s": round(duration, 1),
            },
        )
        return {
            **initial_state,
            "status": "failed",
            "error": str(exc)[:500],
        }
