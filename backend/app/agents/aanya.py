"""Aanya — Frontend Developer: hybrid template + AI code generation.

Aanya generates frontend code using a three-phase approach:
1. Template phase (ZERO AI): package.json, tsconfig.json, tailwind.config.js
2. Auto-generated (ZERO AI): types/index.ts (from Shubham's schemas),
   api/client.ts (from contract endpoints)
3. AI phase (ordered, config-driven):
   Loads FrontendFrameworkConfig (or MobileConfig/DesktopConfig) from registry
   → injects rules + golden examples into AI prompts
   → generates components in dependency order

Supports all 8 frontend frameworks (Next.js, React, Angular, Vue, Svelte,
Remix, Astro, Solid), 5 mobile frameworks (React Native, Flutter, Swift,
Kotlin Compose, Expo), and 2 desktop frameworks (Electron, Tauri).

Frontend types MATCH backend types exactly because they are auto-generated
from the same architecture contract.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.agents.base import (
    AgentResult,
    AgentStatus,
    ToolDefinition,
    call_ai_with_continuation,
    register_agent,
    run_agent,
    store_output,
)
from app.services.ai_router import TaskComplexity

logger = structlog.get_logger(__name__)


# -- Legacy constants (kept for backward compatibility with existing tests) ----

NEXTJS_GENERATION_ORDER: list[dict[str, str]] = [
    {"name": "auth_context", "path": "frontend/src/contexts/AuthContext.tsx",
     "description": "Authentication context provider (login state, token management)"},
    {"name": "layout", "path": "frontend/src/components/Layout.tsx",
     "description": "Main app layout (sidebar, header, content area)"},
    {"name": "pages", "path": "frontend/src/pages/",
     "description": "All page components from contract frontend.pages"},
    {"name": "app", "path": "frontend/src/app/page.tsx",
     "description": "Root page with routing — generated LAST with real import paths"},
]

REACT_GENERATION_ORDER: list[dict[str, str]] = [
    {"name": "auth_context", "path": "frontend/src/contexts/AuthContext.tsx",
     "description": "Authentication context provider (login state, token management)"},
    {"name": "router", "path": "frontend/src/router.tsx",
     "description": "React Router configuration with all routes"},
    {"name": "layout", "path": "frontend/src/components/Layout.tsx",
     "description": "Main app layout (sidebar, header, content area)"},
    {"name": "pages", "path": "frontend/src/pages/",
     "description": "All page components from contract frontend.pages"},
    {"name": "app", "path": "frontend/src/App.tsx",
     "description": "Root App component — generated LAST with real import paths"},
]

# Default (backward-compatible)
FRONTEND_GENERATION_ORDER = NEXTJS_GENERATION_ORDER

# Framework -> generation order (legacy — now sourced from FrontendFrameworkConfig)
FRAMEWORK_GENERATION_ORDERS: dict[str, list[dict[str, str]]] = {
    "Next.js": NEXTJS_GENERATION_ORDER,
    "React": REACT_GENERATION_ORDER,
}

# Legacy rules (kept for backward compat — now sourced from FrontendFrameworkConfig)
FRONTEND_RULES: dict[str, list[str]] = {
    "Next.js": [
        "1. Use Next.js 15 App Router with React 19",
        "2. Use TypeScript strict mode — no 'any' types",
        "3. Import types from '@/types' (auto-generated, don't reinvent)",
        "4. Import API functions from '@/lib/api-client' (auto-generated)",
        "5. NEVER use 'TODO', placeholder text, or stub functions",
        "6. EVERY component must be fully functional",
        "7. Use Tailwind CSS for styling (no inline styles, no CSS modules)",
        "8. Responsive: mobile-first (375px, 768px, 1280px breakpoints)",
        "9. Accessibility: semantic HTML, ARIA labels, keyboard navigation",
        "10. Use 'use client' directive for interactive components",
        "11. Output ONLY the code — no markdown, no explanation",
    ],
    "React": [
        "1. Use React 19 with React Router v7",
        "2. Use TypeScript strict mode — no 'any' types",
        "3. Import types from '@/types' (auto-generated, don't reinvent)",
        "4. Import API functions from '@/lib/api-client' (auto-generated)",
        "5. NEVER use 'TODO', placeholder text, or stub functions",
        "6. EVERY component must be fully functional",
        "7. Use Tailwind CSS for styling (no inline styles, no CSS modules)",
        "8. Responsive: mobile-first (375px, 768px, 1280px breakpoints)",
        "9. Accessibility: semantic HTML, ARIA labels, keyboard navigation",
        "10. Use React Router <Link> for navigation, not <a> tags",
        "11. Output ONLY the code — no markdown, no explanation",
    ],
}

DEFAULT_FRONTEND_RULES = FRONTEND_RULES["Next.js"]


# -- Platform detection and config loading ------------------------------------


def _detect_platform(contract: dict[str, Any]) -> str:
    """Detect the target platform from the architecture contract.

    Returns one of: ``"frontend"``, ``"mobile"``, ``"desktop"``.
    """
    tech_stack = contract.get("tech_stack", {})
    if "mobile" in tech_stack:
        return "mobile"
    if "desktop" in tech_stack:
        return "desktop"
    return "frontend"


def _load_config(contract: dict[str, Any]) -> Any:
    """Load the appropriate config from the registry based on contract.

    Returns a FrontendFrameworkConfig, MobileConfig, or DesktopConfig.
    All three share duck-typed fields: rules, golden_examples, file_structure,
    generation_order (for frontend), language, code_block_lang, display_name.
    """
    tech_stack = contract.get("tech_stack", {})
    platform = _detect_platform(contract)

    if platform == "mobile":
        mobile_name = tech_stack.get("mobile", "react_native")
        from app.agents.mobile_configs import get_mobile_config
        return get_mobile_config(mobile_name)

    if platform == "desktop":
        desktop_name = tech_stack.get("desktop", "electron")
        from app.agents.desktop_configs import get_desktop_config
        return get_desktop_config(desktop_name)

    # Frontend (default)
    frontend_name = tech_stack.get("frontend", "Next.js")
    from app.agents.frontend_frameworks import get_frontend_framework_config
    from app.services.tech_stack import resolve_tech_name
    key = resolve_tech_name(frontend_name)
    if key:
        try:
            return get_frontend_framework_config(key)
        except (ValueError, KeyError):
            pass

    # Try direct name
    try:
        return get_frontend_framework_config(frontend_name)
    except (ValueError, KeyError):
        pass

    # Ultimate fallback: nextjs
    return get_frontend_framework_config("nextjs")


def get_frontend_framework(contract: dict[str, Any]) -> str:
    """Extract frontend framework from architecture contract.

    Falls back to 'Next.js' if not specified.
    """
    tech_stack = contract.get("tech_stack", {})
    frontend = tech_stack.get("frontend", "Next.js")

    # Resolve alias
    from app.services.tech_stack import resolve_tech_name, ALL_STACKS
    key = resolve_tech_name(frontend)
    if key and key in ALL_STACKS:
        return ALL_STACKS[key].canonical_name
    return "Next.js"  # Default


def get_generation_order(framework: str) -> list[dict[str, str]]:
    """Get the file generation order for a frontend framework.

    First tries the FrontendFrameworkConfig registry, then falls back
    to the legacy hardcoded orders.
    """
    # Try config registry first
    try:
        from app.agents.frontend_frameworks import get_frontend_framework_config
        config = get_frontend_framework_config(framework)
        return [dict(step) for step in config.generation_order]
    except (ValueError, KeyError):
        pass

    return FRAMEWORK_GENERATION_ORDERS.get(framework, NEXTJS_GENERATION_ORDER)


def get_rules(framework: str) -> list[str]:
    """Get the mandatory rules for a frontend framework.

    First tries the FrontendFrameworkConfig registry, then falls back
    to the legacy hardcoded rules.
    """
    # Try config registry first
    try:
        from app.agents.frontend_frameworks import get_frontend_framework_config
        config = get_frontend_framework_config(framework)
        return list(config.rules)
    except (ValueError, KeyError):
        pass

    return FRONTEND_RULES.get(framework, DEFAULT_FRONTEND_RULES)


class Aanya:
    """Frontend Developer — hybrid template + AI code generation.

    Supports frontend, mobile, and desktop frameworks via config registries.
    """

    name = "aanya"
    display_name = "Aanya — Frontend Developer"
    default_complexity = TaskComplexity.HIGH
    default_model: str | None = None

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

        self.register_tool(ToolDefinition(
            name="write_file",
            description="Write a generated code file to the project.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "language": {"type": "string", "enum": ["typescript", "tsx", "css", "json"]},
                },
                "required": ["path", "content"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="read_file",
            description="Read a previously generated file for context.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="read_contract",
            description="Read the architecture contract.",
            parameters={
                "type": "object",
                "properties": {},
            },
        ))


    def register_tool(self, tool: "ToolDefinition") -> None:
        """Register a tool available to this agent."""
        self._tools[tool.name] = tool

    @property
    def tools(self) -> list["ToolDefinition"]:
        """All registered tools."""
        return list(self._tools.values())

    async def run(
        self,
        pipeline_run_id: str,
        context: dict[str, Any],
    ) -> AgentResult:
        """Execute with timing, logging, and error handling."""
        return await run_agent(self, pipeline_run_id, context)

    async def execute(
        self,
        pipeline_run_id: str,
        context: dict[str, Any],
    ) -> AgentResult:
        """Generate frontend code from approved architecture contract.

        Phase 1: Template engine generates config files (zero AI).
        Phase 2: Auto-generate types + API client (zero AI).
        Phase 3: AI generates components in dependency order using
                 framework config (rules + golden examples).
        """
        vikram_output = context.get("vikram")
        if not vikram_output:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error="No architecture contract from Vikram",
            )

        contract = vikram_output.get("contract", {})
        vanya_output = context.get("vanya", {})
        generated_files: dict[str, str] = {}

        # Detect platform and load config from registry
        platform = _detect_platform(contract)
        fw_config = _load_config(contract)

        # Get generation order from config (or legacy fallback)
        if hasattr(fw_config, "generation_order") and fw_config.generation_order:
            generation_order = [dict(step) for step in fw_config.generation_order]
        else:
            # Fallback for mobile/desktop configs that don't have generation_order
            framework = get_frontend_framework(contract)
            generation_order = get_generation_order(framework)

        # Determine framework display name
        framework_display = getattr(fw_config, "display_name", get_frontend_framework(contract))

        logger.info(
            "frontend_framework_selected",
            framework=framework_display,
            platform=platform,
            config_type=type(fw_config).__name__,
            rules_count=len(fw_config.rules),
            golden_examples_count=len(getattr(fw_config, "golden_examples", {})),
        )

        # Read user feedback if available
        user_feedback = context.get("__user_feedback__", "")

        # ── Phase 1: Template scaffolding (ZERO AI) ──
        try:
            from app.engine.template_engine import get_template_engine

            engine = get_template_engine()
            template_files = engine.render_all(contract, categories=["frontend"])

            for f in template_files:
                generated_files[f.path] = f.content

            logger.info(
                "template_phase_complete",
                files_generated=len(template_files),
            )
        except Exception as exc:
            from app.services.ai_router import _sanitize_error  # R27-FIX
            logger.warning("template_phase_failed", error=_sanitize_error(exc))

        # ── Phase 2: AI component generation (ordered) ──
        accumulated_code: dict[str, str] = {}

        # Include auto-generated types + API client as context
        for path, content in generated_files.items():
            if "types/index.ts" in path or "api-client.ts" in path:
                name = path.split("/")[-1].replace(".ts", "").replace("-", "_")
                accumulated_code[name] = content

        for step in generation_order:
            system_prompt = self._build_generation_prompt(
                step=step,
                contract=contract,
                accumulated_code=accumulated_code,
                design_spec=vanya_output.get("design_spec", ""),
                fw_config=fw_config,
                user_feedback=user_feedback,
            )

            try:
                response = await call_ai_with_continuation(self, 
                    messages=[{
                        "role": "user",
                        "content": f"Generate the {step['description']} for this project.",
                    }],
                    system_prompt=system_prompt,
                    task_type="general",
                    temperature=0.2,
                    max_continuations=5,
                )

                accumulated_code[step["name"]] = response.content
                generated_files[step["path"]] = response.content

                logger.info(
                    "ai_generation_step",
                    step=step["name"],
                    model=response.model_used,
                    tokens=response.output_tokens,
                )

            except Exception as exc:
                from app.services.ai_router import _sanitize_error  # R27-FIX
                safe_err = _sanitize_error(exc)
                logger.error("ai_generation_failed", step=step["name"], error=safe_err)
                # R28-FIX-1: Use sanitized error in accumulated_code too.
                # Raw exc can contain API keys from httpx.HTTPStatusError headers,
                # which flow into file_contents → context → downstream agents → user.
                accumulated_code[step["name"]] = f"// Generation failed: {safe_err}"

        output = {
            "generated_files": list(generated_files.keys()),
            # R8-FIX: Store actual file contents — ALL downstream agents
            # (karan, navya, deepika, fixer, aarav, docs, git, security)
            # read file_contents to analyze/fix code.
            "file_contents": dict(generated_files),
            "template_files_count": len([f for f in generated_files if "package.json" in f or "tsconfig" in f]),
            "ai_files": [s["path"] for s in generation_order],
            "file_count": len(generated_files),
            "generation_order": [s["name"] for s in generation_order],
            "framework": framework_display,
            "platform": platform,
        }

        await store_output(self, pipeline_run_id, output)

        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.COMPLETED,
            output=output,
        )

    def _build_generation_prompt(
        self,
        step: dict[str, str],
        contract: dict[str, Any],
        accumulated_code: dict[str, str],
        design_spec: str,
        fw_config: Any = None,
        user_feedback: str = "",
        # Legacy parameters (backward compat)
        framework: str = "Next.js",
        rules: list[str] | None = None,
    ) -> str:
        """Build context-rich, config-driven prompt for frontend generation.

        Prompt structure (mirrors Shubham's gold standard):
        1. WHAT TO BUILD — role + task + framework
        2. Architecture Contract — pages and frontend spec
        3. File Structure — expected project layout
        4. Mandatory Rules — framework-specific rules from config
        5. Golden Example — code pattern for this specific step
        6. Previously Generated Files — real code for dependency context
        7. Design Specification (if applicable)
        8. Completeness Rules — never truncate
        9. User Feedback (if present)
        10. GENERATE — output format instructions
        """
        import orjson

        # Get display name and code language from config
        display_name = getattr(fw_config, "display_name", framework)
        code_lang = getattr(fw_config, "code_block_lang", "tsx")
        config_rules = list(getattr(fw_config, "rules", []))
        golden_examples = getattr(fw_config, "golden_examples", {})
        file_structure = getattr(fw_config, "file_structure", {})

        # Fallback to legacy rules if config has none
        if not config_rules and rules:
            config_rules = rules
        elif not config_rules:
            config_rules = list(get_rules(framework))

        pages = contract.get("frontend", {}).get("pages", [])
        pages_json = orjson.dumps(pages, option=orjson.OPT_INDENT_2).decode("utf-8")

        prompt_parts: list[str] = []

        # ── 1. WHAT TO BUILD ──
        prompt_parts.extend([
            "You are Aanya, the Frontend Developer at NexSidi.",
            f"You are generating the **{step['description']}** for a **{display_name}** project.",
            f"Language: {getattr(fw_config, 'language', 'typescript')}.",
            "",
        ])

        # ── 2. Architecture Contract (frontend pages) ──
        prompt_parts.extend([
            "## Frontend Pages (from Architecture Contract)",
            f"```json\n{pages_json}\n```",
            "",
        ])

        # ── 3. File Structure ──
        if file_structure:
            prompt_parts.append(f"## {display_name} Project File Structure")
            for step_name, path in file_structure.items():
                prompt_parts.append(f"- `{step_name}` → `{path}`")
            prompt_parts.append("")

        # ── 4. Mandatory Rules ──
        prompt_parts.append(f"## MANDATORY {display_name} RULES (NEVER VIOLATE)")
        for rule in config_rules:
            prompt_parts.append(rule)
        prompt_parts.append("")

        # ── 5. Golden Example ──
        golden = golden_examples.get(step["name"])
        if golden:
            prompt_parts.extend([
                f"## GOLDEN EXAMPLE — {step['name']}",
                "Follow this EXACT pattern. Adapt names/fields from the contract.",
                f"```{code_lang}\n{golden}\n```",
                "",
            ])

        # ── 6. Previously Generated Files ──
        if accumulated_code:
            prompt_parts.append("## Previously Generated Files (REAL CODE — use exact names)")
            for name, code in accumulated_code.items():
                prompt_parts.append(f"\n### {name}\n```{code_lang}\n{code}\n```")
            prompt_parts.append("")

        # ── 7. Design Specification ──
        if design_spec and step["name"] in ("layout", "pages"):
            prompt_parts.extend([
                "## Design Specification (from Vanya)",
                str(design_spec),
                "",
            ])

        # ── 8. Completeness Rules ──
        prompt_parts.extend([
            "## COMPLETENESS RULES",
            "- Generate the COMPLETE file. NEVER stop mid-function or mid-component.",
            "- If the file needs 500+ lines, generate ALL of them. No shortcuts.",
            "- Every function must be fully implemented with real logic.",
            "- Every component must have complete rendering, event handlers, and state.",
            "- NEVER leave placeholder comments like 'add more here' or 'implement later'.",
            "",
        ])

        # ── 9. User Feedback ──
        if user_feedback:
            prompt_parts.extend([
                "## User Feedback (incorporate into generation)",
                user_feedback,
                "",
            ])

        # ── 10. GENERATE ──
        prompt_parts.extend([
            "## GENERATE",
            f"Output ONLY the {code_lang} code file. No markdown wrapping, no explanations.",
            "NEVER use 'TODO', placeholder text, or stub functions.",
            "Match entity/field names EXACTLY from the contract.",
        ])

        return "\n".join(prompt_parts)


# Register the agent
_aanya = Aanya()
register_agent(_aanya)
