"""Aanya — Frontend Developer: hybrid template + AI code generation.

Aanya generates frontend code using a three-phase approach:
1. Template phase (ZERO AI): package.json, tsconfig.json, tailwind.config.js
2. Auto-generated (ZERO AI): types/index.ts (from Shubham's schemas),
   api/client.ts (from contract endpoints)
3. AI phase (agentic tool loop):
   Loads FrontendFrameworkConfig (or MobileConfig/DesktopConfig) from registry
   → injects rules + golden examples into a single comprehensive system prompt
   → uses call_ai_with_tools() so Aanya can write files, read backend output,
     validate syntax, and consult Shubham/Vikram as needed

Supports all 8 frontend frameworks (Next.js, React, Angular, Vue, Svelte,
Remix, Astro, Solid), 5 mobile frameworks (React Native, Flutter, Swift,
Kotlin Compose, Expo), and 2 desktop frameworks (Electron, Tauri).

Frontend types MATCH backend types exactly because Aanya can read backend
files written by Shubham via the read_file and ask_backend tools.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.agents.base import (
    AgentInterruptRequest,
    AgentResult,
    AgentStatus,
    INTERRUPT_TOOL,
    ToolDefinition,
    call_ai_with_tools,
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


# -- Agentic tool definitions --------------------------------------------------


AANYA_TOOLS = [
    ToolDefinition(
        name="write_file",
        description="Write a file to the project output. Use this to save generated frontend code.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to project root (e.g. 'frontend/src/components/Button.tsx')"},
                "content": {"type": "string", "description": "Complete file content to write"},
            },
            "required": ["path", "content"],
        },
    ),
    ToolDefinition(
        name="read_file",
        description="Read a previously generated file. Useful for reading backend schemas/models to align frontend types.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to read"},
            },
            "required": ["path"],
        },
    ),
    ToolDefinition(
        name="validate_syntax",
        description="Check TypeScript/JavaScript syntax validity. Returns 'OK' or error description.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to validate"},
                "content": {"type": "string", "description": "Content to validate (optional)"},
            },
            "required": ["path"],
        },
    ),
    ToolDefinition(
        name="list_files",
        description="List all files written so far in the project (both frontend and backend).",
        parameters={
            "type": "object",
            "properties": {},
        },
    ),
    ToolDefinition(
        name="ask_backend",
        description="Ask Shubham (backend engineer) about API contracts, endpoints, or data schemas.",
        parameters={
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Your question for the backend engineer"},
                "context": {"type": "string", "description": "Relevant context"},
            },
            "required": ["question"],
        },
    ),
    ToolDefinition(
        name="ask_architect",
        description="Ask Vikram (architect) about design decisions, UI/UX requirements, or feature scope.",
        parameters={
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Your question for the architect"},
                "context": {"type": "string", "description": "Relevant context"},
            },
            "required": ["question"],
        },
    ),
    ToolDefinition(
        name="task_complete",
        description="Signal that frontend generation is complete. Call when all required files are written.",
        parameters={
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Summary of what was generated"},
            },
            "required": ["summary"],
        },
    ),
    # I3-FIX: Import & type validation during frontend code generation
    ToolDefinition(
        name="check_imports",
        description=(
            "Verify all Python imports resolve to known modules. Call after writing "
            "a .py file (e.g. utility scripts, API clients)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path of the Python file to check"},
            },
            "required": ["path"],
        },
    ),
    ToolDefinition(
        name="check_types",
        description=(
            "Run type checking on a TypeScript or Python file. Returns warnings "
            "(non-blocking). Call after writing critical .ts/.tsx files."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path of the file to type-check"},
            },
            "required": ["path"],
        },
    ),
    # I1-FIX: Dynamic agent re-dispatch
    INTERRUPT_TOOL,
]


# -- Tool handler --------------------------------------------------------------


class AanyaToolHandler:
    """Handles tool calls from Aanya's agentic tool loop."""

    def __init__(
        self,
        pipeline_run_id: str,
        generated_files: dict[str, str],
        pipeline_context: dict | None = None,
        backend_files: dict[str, str] | None = None,
    ) -> None:
        self._pipeline_run_id = pipeline_run_id
        self._files = generated_files
        self._pipeline_context = pipeline_context or {}  # For agent oracle fallback
        self._backend_files = backend_files or {}  # AUDIT-FIX: lazy backend ref
        self._done = False
        self._summary = ""

    async def __call__(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == "write_file":
            return await self._write_file(**tool_input)
        elif tool_name == "read_file":
            return await self._read_file(**tool_input)
        elif tool_name == "validate_syntax":
            return await self._validate_syntax(**tool_input)
        elif tool_name == "list_files":
            return self._list_files()
        elif tool_name == "ask_backend":
            return await self._ask_agent("shubham", **tool_input)
        elif tool_name == "ask_architect":
            return await self._ask_agent("vikram", **tool_input)
        elif tool_name == "task_complete":
            return self._task_complete(**tool_input)
        # I3-FIX: Import & type validation during generation
        elif tool_name == "check_imports":
            return self._check_imports(**tool_input)
        elif tool_name == "check_types":
            return self._check_types(**tool_input)
        # I1-FIX: Dynamic agent re-dispatch
        elif tool_name == "request_agent_rerun":
            return self._request_agent_rerun(**tool_input)
        else:
            return f"Unknown tool: {tool_name}"

    async def _write_file(self, path: str, content: str) -> str:
        self._files[path] = content
        return f"Written {path} ({len(content)} chars)"

    async def _read_file(self, path: str) -> str:
        if path in self._files:
            content = self._files[path]
            if len(content) > 10000:
                return content[:10000] + "\n... [truncated]"
            return content
        # AUDIT-FIX: Lazy fallback — load from Shubham's backend files on demand
        if path in self._backend_files:
            content = self._backend_files[path]
            self._files[path] = content  # Cache for subsequent reads
            if len(content) > 10000:
                return content[:10000] + "\n... [truncated]"
            return content
        return f"File not found: {path}. Available: {list(self._files.keys())[:10]}"

    async def _validate_syntax(self, path: str, content: str | None = None) -> str:
        # For TypeScript/JavaScript files: basic checks (bracket balance, common patterns)
        # For .py files: use ast.parse
        code = content or self._files.get(path, "")
        if not code:
            return f"No content for {path}"
        if path.endswith(".py"):
            import ast
            try:
                ast.parse(code)
                return "OK"
            except SyntaxError as e:
                return f"SyntaxError at line {e.lineno}: {e.msg}"
        # For TS/JS: basic bracket balance check
        opens = code.count("{") + code.count("(") + code.count("[")
        closes = code.count("}") + code.count(")") + code.count("]")
        if abs(opens - closes) > 5:
            return f"Possible syntax issue: unbalanced brackets (opens={opens}, closes={closes})"
        return "OK"

    def _list_files(self) -> str:
        if not self._files:
            return "No files written yet"
        lines = [f"- {path} ({len(content)} chars)" for path, content in self._files.items()]
        return "\n".join(lines)

    async def _ask_agent(self, agent_name: str, question: str, context: str = "") -> str:
        # PATH A: Real-time message bus (works when agents run concurrently)
        try:
            from app.services.agent_message_bus import get_agent_message_bus
            bus = get_agent_message_bus()
            answer = await bus.ask(
                from_agent="aanya",
                to_agent=agent_name,
                pipeline_run_id=self._pipeline_run_id,
                question=question,
                context={"context": context},
                timeout=10.0,  # Short timeout — fall back to oracle quickly
            )
            return answer
        except Exception:
            pass  # Fall through to oracle

        # PATH B: Context oracle fallback — for sequential pipeline where the
        # target agent has already completed (Vikram and Shubham run before Aanya).
        if self._pipeline_context:
            from app.services.agent_oracle import query_agent_context
            return query_agent_context(self._pipeline_context, agent_name, question)

        return f"Agent {agent_name} not available. Proceed with best judgment."

    def _task_complete(self, summary: str) -> str:
        self._done = True
        self._summary = summary
        return f"Task marked complete: {summary}"

    # I1-FIX: Dynamic agent re-dispatch ────────────────────────────

    def _request_agent_rerun(
        self, target_agent: str, reason: str, required_changes: str
    ) -> str:
        """Request another agent to re-run with updated requirements.

        C2c-FIX: Uses configurable max_interrupts_per_pair from settings
        instead of hardcoded 2.  Also passes partial_output so work done
        before the interrupt isn't lost.
        """
        from app.config import get_settings

        max_interrupts = get_settings().max_interrupts_per_pair
        counter_key = f"__interrupt_count__aanya_{target_agent}__"
        count = self._pipeline_context.get(counter_key, 0)
        if count >= max_interrupts:
            return (
                f"Cannot request {target_agent} re-run: max {max_interrupts} interrupts "
                f"per agent pair reached ({count}/{max_interrupts}). Proceed with best judgment."
            )
        raise AgentInterruptRequest(
            requesting_agent="aanya",
            target_agent=target_agent,
            reason=reason,
            required_changes=required_changes,
            partial_output=dict(self._files),  # Preserve work done so far
        )

    # I3-FIX: Import & type validation during generation ─────────────

    def _check_imports(self, path: str) -> str:
        """Verify Python imports resolve using shared code_validator."""
        code = self._files.get(path, "")
        if not code:
            return f"File not found: {path}"
        from app.agents.tools.code_validator import check_imports, extract_project_files

        project_files = extract_project_files(self._pipeline_context)
        # Include files from current session and backend refs
        for fp in list(self._files) + list(self._backend_files):
            if fp.endswith(".py"):
                mod = fp.replace("/", ".").replace("\\", ".")
                if mod.endswith(".py"):
                    mod = mod[:-3]
                project_files.add(mod)
        return check_imports(path, code, project_files)

    def _check_types(self, path: str) -> str:
        """Run lightweight type checking via shared code_validator."""
        code = self._files.get(path, "")
        if not code:
            return f"File not found: {path}"
        from app.agents.tools.code_validator import check_types

        return check_types(path, code)


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
    Uses an agentic tool loop (call_ai_with_tools) so Aanya can write files
    iteratively, read Shubham's backend output, validate syntax, and consult
    other agents instead of passively receiving a single AI response.
    """

    name = "aanya"
    display_name = "Aanya — Frontend Developer"
    default_complexity = TaskComplexity.HIGH
    default_model: str | None = None

    # Agentic tool definitions exposed as a class-level list so
    # call_ai_with_tools() can access agent.tools in the standard way.
    tools = AANYA_TOOLS

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
        Phase 2: Agentic AI tool loop — Aanya uses write_file, read_file,
                 validate_syntax, ask_backend, ask_architect, and task_complete
                 tools to iteratively generate all required frontend files.
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

        # Snapshot backend file contents from Shubham's output so Aanya
        # can read them via the read_file tool.
        shubham_output = context.get("shubham", {})
        # AUDIT-FIX: Lazy-load backend files instead of eagerly copying all of them.
        # For 100-file projects, eager loading wastes several MB of memory when
        # Aanya only reads 3-5 backend files via the read_file tool.
        backend_file_contents: dict[str, str] = shubham_output.get("file_contents", {})

        # ── Phase 2: Agentic AI generation (tool loop) ──
        tool_handler = AanyaToolHandler(
            pipeline_run_id=pipeline_run_id,
            generated_files=generated_files,
            pipeline_context=context,  # For agent oracle fallback in ask_backend/ask_architect
            backend_files=backend_file_contents,  # AUDIT-FIX: lazy-load ref
        )

        system_prompt = self._build_agentic_system_prompt(
            contract=contract,
            generation_order=generation_order,
            backend_files=list(backend_file_contents.keys()),
            fw_config=fw_config,
            framework_display=framework_display,
            design_spec=vanya_output.get("design_spec", ""),
            user_feedback=user_feedback,
        )

        try:
            response = await call_ai_with_tools(
                self,
                messages=[{
                    "role": "user",
                    "content": (
                        "Generate all required frontend files for this project. "
                        "Use write_file for each file, read_file to inspect backend schemas, "
                        "validate_syntax to verify your code, and task_complete when done."
                    ),
                }],
                system_prompt=system_prompt,
                task_type="general",
                tool_handler=tool_handler,
                max_tool_rounds=30,
            )

            logger.info(
                "agentic_generation_complete",
                files_written=len([p for p in tool_handler._files if "frontend/" in p]),
                done_flag=tool_handler._done,
                model=response.model_used,
                tokens=response.output_tokens,
            )

            # COST-AGG-FIX: Record generation cost to shared pipeline-level tracker.
            try:
                from app.services.pipeline import get_run_cost_tracker
                tracker = get_run_cost_tracker(pipeline_run_id)
                if tracker is not None:
                    tracker.record(response, agent_name=self.name, model_key="high")
            except Exception:
                pass  # Cost tracking is non-fatal

        except Exception as exc:
            # R28-FIX-1: Sanitize exceptions so API keys never appear in logs or
            # in accumulated_code output.  Use _sanitize_error instead of str(exc).
            from app.services.ai_router import _sanitize_error  # R27-FIX
            safe_err = _sanitize_error(exc)
            logger.error("agentic_generation_failed", error=safe_err)
            # Non-fatal: continue with whatever was written before the exception

        # generated_files has been mutated in place by AanyaToolHandler._write_file
        # (handler holds a reference to the same dict)
        frontend_files = [p for p in generated_files if "frontend/" in p or "mobile/" in p or "desktop/" in p]

        # ── Layer 1: Builder Self-Check ──
        # Validate own output before downstream handoff — catch obvious issues
        # before reviewers waste AI calls.
        self_check = self._run_self_check(generated_files, contract, framework_display)

        logger.info(
            "self_check_complete",
            errors=self_check.get("errors", 0),
            warnings=self_check.get("warnings", 0),
            passed=self_check.get("passed", False),
        )

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
            "agentic_summary": tool_handler._summary,
            "self_check": self_check,
        }

        await store_output(self, pipeline_run_id, output)

        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.COMPLETED,
            output=output,
        )

    def _run_self_check(
        self,
        generated_files: dict[str, str],
        contract: dict[str, Any],
        framework: str,
    ) -> dict[str, Any]:
        """Layer 1 self-check: validate own output before downstream handoff.

        Checks (no AI — pure deterministic):
        1. TS/JS files have balanced brackets
        2. No placeholder/TODO comments
        3. No markdown code fences (LLM artifacts)
        4. Required pages from contract have corresponding files
        5. Non-empty file contents
        """
        import re

        errors: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        files_checked = 0

        for path, content in generated_files.items():
            if not content or len(content.strip()) < 10:
                errors.append({"file": path, "issue": "File is empty or near-empty"})
                continue

            files_checked += 1

            # 1. Bracket balance check for TS/JS/TSX/JSX
            if path.endswith((".ts", ".tsx", ".js", ".jsx")):
                opens = content.count("{") + content.count("(") + content.count("[")
                closes = content.count("}") + content.count(")") + content.count("]")
                if abs(opens - closes) > 5:
                    errors.append({
                        "file": path,
                        "issue": f"Severely unbalanced brackets: opens={opens}, closes={closes}",
                    })

            # 2. Python syntax check (for any .py files in frontend — like config)
            if path.endswith(".py"):
                import ast
                try:
                    ast.parse(content)
                except SyntaxError as e:
                    errors.append({
                        "file": path,
                        "issue": f"SyntaxError at line {e.lineno}: {e.msg}",
                    })

            # 3. Placeholder/TODO detection
            todo_matches = re.findall(
                r"(?:TODO|FIXME|HACK|XXX|PLACEHOLDER|implement later|add more here)",
                content, re.IGNORECASE,
            )
            if todo_matches:
                warnings.append({
                    "file": path,
                    "issue": f"Found {len(todo_matches)} placeholder comment(s): {todo_matches[:3]}",
                })

            # 4. Anti-hallucination: markdown fences in generated code
            if "```typescript" in content or "```tsx" in content or "```python" in content:
                errors.append({
                    "file": path,
                    "issue": "File contains markdown code fences (LLM artifact)",
                })

        # 5. Check contract pages have files
        pages = contract.get("frontend", {}).get("pages", [])
        if pages and not any("page" in p.lower() or "pages" in p.lower() for p in generated_files):
            warnings.append({
                "file": "(missing)",
                "issue": f"Contract defines {len(pages)} page(s) but no page files found",
            })

        return {
            "files_checked": files_checked,
            "errors": len(errors),
            "warnings": len(warnings),
            "error_details": errors[:20],
            "warning_details": warnings[:20],
            "passed": len(errors) == 0,
        }

    def _build_agentic_system_prompt(
        self,
        contract: dict[str, Any],
        generation_order: list[dict[str, str]],
        backend_files: list[str],
        fw_config: Any,
        framework_display: str,
        design_spec: str = "",
        user_feedback: str = "",
    ) -> str:
        """Build the comprehensive one-shot system prompt for Aanya's agentic loop.

        Unlike the old per-step prompts, this single prompt lists ALL files
        Aanya must generate. The AI then calls write_file iteratively,
        read_file to inspect backend output, validate_syntax, and finally
        task_complete when done.

        Prompt structure:
        1. Role and tools available
        2. Architecture contract
        3. Backend files already available (readable via read_file)
        4. Files to generate (full generation order)
        5. Framework-specific mandatory rules
        6. Golden examples
        7. Design specification (if applicable)
        8. Completeness rules
        9. User feedback (if present)
        10. Workflow instructions
        """
        import orjson

        display_name = getattr(fw_config, "display_name", framework_display)
        code_lang = getattr(fw_config, "code_block_lang", "tsx")
        config_rules = list(getattr(fw_config, "rules", []))
        golden_examples = getattr(fw_config, "golden_examples", {})
        file_structure = getattr(fw_config, "file_structure", {})

        if not config_rules:
            config_rules = list(get_rules(framework_display))

        contract_json = orjson.dumps(contract, option=orjson.OPT_INDENT_2).decode("utf-8")

        prompt_parts: list[str] = []

        # ── 1. Role and tools ──
        prompt_parts.extend([
            "You are Aanya, the Frontend Developer at NexSidi.",
            f"You are generating ALL frontend files for a **{display_name}** project.",
            f"Language: {getattr(fw_config, 'language', 'typescript')}.",
            "",
            "## Tools Available",
            "- **write_file(path, content)** — save a generated file (call once per file)",
            "- **read_file(path)** — read a backend file to align types/interfaces",
            "- **validate_syntax(path, content?)** — check TypeScript/JS bracket balance",
            "- **list_files()** — see all files written so far",
            "- **ask_backend(question, context?)** — ask Shubham about API contracts",
            "- **ask_architect(question, context?)** — ask Vikram about design decisions",
            "- **task_complete(summary)** — signal completion (call LAST when all files written)",
            "",
        ])

        # ── 2. Architecture Contract ──
        prompt_parts.extend([
            "## Architecture Contract (SINGLE SOURCE OF TRUTH)",
            f"```json\n{contract_json}\n```",
            "",
        ])

        # ── 3. Backend files available ──
        if backend_files:
            prompt_parts.extend([
                "## Backend Files Available (from Shubham)",
                "Use read_file to inspect these before writing frontend types/interfaces:",
            ])
            for bf in backend_files[:20]:  # cap to avoid huge prompts
                prompt_parts.append(f"- `{bf}`")
            if len(backend_files) > 20:
                prompt_parts.append(f"  ... and {len(backend_files) - 20} more")
            prompt_parts.append("")

        # ── 4. Files to generate ──
        prompt_parts.extend([
            "## Files You Must Generate",
            "Call write_file for EACH of these (in dependency order):",
        ])
        for step in generation_order:
            prompt_parts.append(f"- `{step['path']}` — {step['description']}")
        prompt_parts.append("")

        # ── 5. File Structure ──
        if file_structure:
            prompt_parts.append(f"## {display_name} Project File Structure")
            for step_name, path in file_structure.items():
                prompt_parts.append(f"- `{step_name}` → `{path}`")
            prompt_parts.append("")

        # ── 6. Mandatory Rules ──
        prompt_parts.append(f"## MANDATORY {display_name} RULES (NEVER VIOLATE)")
        for rule in config_rules:
            prompt_parts.append(rule)
        prompt_parts.append("")

        # ── 7. Golden Examples ──
        if golden_examples:
            prompt_parts.append("## GOLDEN EXAMPLES")
            for step_name, golden in golden_examples.items():
                prompt_parts.extend([
                    f"### {step_name}",
                    "Follow this EXACT pattern. Adapt names/fields from the contract.",
                    f"```{code_lang}\n{golden}\n```",
                    "",
                ])

        # ── 8. Design Specification ──
        if design_spec:
            prompt_parts.extend([
                "## Design Specification (from Vanya)",
                str(design_spec),
                "",
            ])

        # ── 9. Completeness Rules ──
        prompt_parts.extend([
            "## COMPLETENESS RULES",
            "- Generate COMPLETE files. NEVER stop mid-function or mid-component.",
            "- If a file needs 500+ lines, generate ALL of them. No shortcuts.",
            "- Every function must be fully implemented with real logic.",
            "- Every component must have complete rendering, event handlers, and state.",
            "- NEVER leave placeholder comments like 'add more here' or 'implement later'.",
            "- Each write_file call must contain the FULL file content, not a diff.",
            "",
        ])

        # ── 10. User Feedback ──
        if user_feedback:
            prompt_parts.extend([
                "## User Feedback (incorporate into generation)",
                user_feedback,
                "",
            ])

        # ── 11. Workflow instructions ──
        prompt_parts.extend([
            "## WORKFLOW",
            "1. Optionally call read_file on relevant backend files to align types.",
            "2. For each file in the generation list, call write_file with the full content.",
            "3. Optionally call validate_syntax on files you want to verify.",
            "4. Use ask_backend if you need clarification on API contracts or schemas.",
            "5. Use ask_architect if you need clarification on UI/UX scope.",
            "6. When ALL files are written, call task_complete with a summary.",
            "",
            "Write ONLY valid TypeScript/JSX code — no markdown fences, no explanations.",
            "Match entity/field names EXACTLY from the contract.",
        ])

        return "\n".join(prompt_parts)

    # ── Legacy helper kept for backward compatibility with existing tests ──

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
