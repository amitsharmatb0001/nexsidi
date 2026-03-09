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

import json
from typing import Any

import structlog

from app.agents.base import (
    AgentInterruptRequest,
    AgentResult,
    AgentStatus,
    ASK_AGENT_TOOL,
    INTERRUPT_TOOL,
    ToolDefinition,
    WEB_SEARCH_TOOL,
    WEB_SCRAPE_TOOL,
    call_ai_with_tools,
    clamp_completeness,
    handle_web_tool,
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
    # 1.4-FIX: Compile-check generated TS/JS files in sandboxed subprocess.
    ToolDefinition(
        name="compile_check",
        description=(
            "Run TypeScript compiler (tsc --noEmit) on generated .ts/.tsx files "
            "to catch type errors before handoff. Returns compiler output."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path of the TypeScript file to compile-check",
                },
            },
            "required": ["path"],
        },
    ),
    # 1.4-FIX: Self-review tool — cheap model critique before task_complete.
    ToolDefinition(
        name="self_review",
        description=(
            "Request an independent code review of your generated files. "
            "A separate AI reviewer checks for: missing imports, wrong prop types, "
            "inconsistent routing, missing error boundaries. "
            "Call this BEFORE task_complete."
        ),
        parameters={
            "type": "object",
            "properties": {
                "focus": {
                    "type": "string",
                    "description": "What to focus the review on (e.g. 'routing', 'components', 'all')",
                },
            },
            "required": ["focus"],
        },
    ),
    # 6B: Run frontend tests inside sandbox container
    ToolDefinition(
        name="run_frontend_test",
        description=(
            "Run vitest/jest on the generated frontend code inside the sandbox. "
            "Use AFTER generating all frontend code. Fix failures and re-run."
        ),
        parameters={
            "type": "object",
            "properties": {
                "sandbox_id": {
                    "type": "string",
                    "description": "Docker container ID of the sandbox",
                },
                "test_command": {
                    "type": "string",
                    "description": "Test command, e.g. 'npx vitest run --reporter=verbose'",
                },
            },
            "required": ["sandbox_id"],
        },
    ),
    ToolDefinition(
        name="run_type_check",
        description=(
            "Run TypeScript type-checking (tsc --noEmit) on the generated frontend code."
        ),
        parameters={
            "type": "object",
            "properties": {
                "sandbox_id": {
                    "type": "string",
                    "description": "Docker container ID of the sandbox",
                },
            },
            "required": ["sandbox_id"],
        },
    ),
    # I1-FIX: Dynamic agent re-dispatch
    INTERRUPT_TOOL,
    # AGENTIC-FIX: Inter-agent communication + web research
    ASK_AGENT_TOOL,
    WEB_SEARCH_TOOL,
    WEB_SCRAPE_TOOL,
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
        # PHASE-E: Extra verification rules from mistake memory.
        self._verification_rules: list[Any] = []
        try:
            from app.services.mistake_memory import mistake_memory
            self._verification_rules = mistake_memory.build_validation_rules(
                "aanya", "frontend_generation", ""
            )
        except Exception as _mm_exc:
            logger.debug("mistake_memory_init_failed", error=str(_mm_exc)[:200])

        # CHANGE-2: Persistent gate instance (loop detection history preserved)
        # + rejection counter for max-rejection cap.
        from app.agents.base import VerificationGate
        self._gate = VerificationGate(extra_rules=self._verification_rules)
        self._rejection_counts: dict[str, int] = {}
        self._unresolved: dict[str, list[str]] = {}
        # AUDIT-T2-2: Initialize counters properly instead of fragile getattr pattern
        self._consecutive_rejections: int = 0
        self._consecutive_errors: int = 0
        # CHANGE-4: Export registry for hallucination detection
        self._export_registry: dict[str, set[str]] = {}

    def observe(self, tool_name: str, result: str) -> "Observation":
        """PHASE-G: System evaluates tool result autonomously.

        PHASE-I: Feeds pass/fail into AdaptiveComplexity for cross-goal
        model escalation (cheap → expensive only when needed).
        """
        from app.agents.base import Observation

        adaptive = getattr(self, "_adaptive", None)

        if tool_name == "write_file":
            if "REJECTED" in result:
                # AUDIT-T3-4: Track rejection reason — only escalate if same error repeats
                _rejection_reason = result[:200]
                _last_reason = getattr(self, "_last_rejection_reason", "")
                self._last_rejection_reason = _rejection_reason
                if _rejection_reason != _last_reason:
                    # Different rejection reason — reset counter
                    self._consecutive_rejections = 1
                else:
                    self._consecutive_rejections = getattr(self, "_consecutive_rejections", 0) + 1
                if adaptive:
                    adaptive.on_verification_fail()
                if self._consecutive_rejections >= 3:
                    self._consecutive_rejections = 0
                    self._last_rejection_reason = ""
                    return Observation(
                        needs_escalation=True,
                        override_result=result + "\n\n⚡ MODEL ESCALATED: Switching to more capable model.",
                    )
            else:
                self._consecutive_rejections = 0
                if adaptive:
                    adaptive.on_verification_pass()

        if result.startswith("[ERROR]") or result.startswith("Error"):
            self._consecutive_errors = getattr(self, "_consecutive_errors", 0) + 1
            if self._consecutive_errors >= 2:
                self._consecutive_errors = 0
                return Observation(needs_escalation=True)
        else:
            self._consecutive_errors = 0

        return Observation()

    async def __call__(self, tool_name: str, tool_input: dict) -> str:
        # AGENTIC-FIX: Delegate shared tools (web_search, web_scrape, ask_agent)
        shared_result = await handle_web_tool(tool_name, tool_input)
        if shared_result is not None:
            return shared_result

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
        # 1.4-FIX: Compile-check and self-review tools
        elif tool_name == "compile_check":
            return await self._compile_check(**tool_input)
        elif tool_name == "self_review":
            return await self._self_review(**tool_input)
        # I1-FIX: Dynamic agent re-dispatch
        elif tool_name == "request_agent_rerun":
            return self._request_agent_rerun(**tool_input)
        # 6B: Run frontend tests inside sandbox container
        elif tool_name == "run_frontend_test":
            sandbox_id = tool_input.get("sandbox_id") or self._pipeline_context.get("sandbox_id", "")
            if not sandbox_id:
                return json.dumps({"error": "No sandbox_id available"})
            test_cmd = tool_input.get("test_command", "npx vitest run --reporter=verbose 2>&1 || npm test -- --watchAll=false 2>&1")
            cmd = ["docker", "exec", sandbox_id, "sh", "-c", test_cmd]
            try:
                from app.engine.execution_engine import _run_subprocess
                result = await _run_subprocess(cmd, timeout=120)
                return json.dumps({
                    "exit_code": result.returncode,
                    "stdout": result.stdout[-3000:],
                    "stderr": result.stderr[-1000:],
                    "passed": result.returncode == 0,
                })
            except Exception as exc:
                return json.dumps({"error": f"Frontend test failed: {str(exc)[:300]}"})
        # 6B: Run TypeScript type-checking inside sandbox
        elif tool_name == "run_type_check":
            sandbox_id = tool_input.get("sandbox_id") or self._pipeline_context.get("sandbox_id", "")
            if not sandbox_id:
                return json.dumps({"error": "No sandbox_id available"})
            cmd = ["docker", "exec", sandbox_id, "npx", "tsc", "--noEmit"]
            try:
                from app.engine.execution_engine import _run_subprocess
                result = await _run_subprocess(cmd, timeout=60)
                return json.dumps({
                    "exit_code": result.returncode,
                    "stdout": result.stdout[-3000:],
                    "stderr": result.stderr[-1000:],
                    "passed": result.returncode == 0,
                })
            except Exception as exc:
                return json.dumps({"error": f"Type check failed: {str(exc)[:300]}"})
        else:
            return f"Unknown tool: {tool_name}"

    # V7.1-FIX: Reject oversized files (LLM could generate multi-MB output)
    _MAX_FILE_SIZE = 500 * 1024  # 500KB

    async def _write_file(self, path: str, content: str) -> str:
        if len(content) > self._MAX_FILE_SIZE:
            return f"Error: file too large ({len(content)} bytes, max {self._MAX_FILE_SIZE})"
        # CHANGE-2: Use persistent gate (loop detection history preserved).
        result = self._gate.verify(path, content, self._files)
        if not result.passed:
            # CHANGE-2: Max rejection cap — after 5 rejections, accept with warnings.
            self._rejection_counts[path] = self._rejection_counts.get(path, 0) + 1
            if self._rejection_counts[path] >= 5:
                self._files[path] = content
                self._unresolved[path] = result.failures[:3]
                logger.warning(
                    "max_rejections_reached",
                    path=path,
                    rejections=self._rejection_counts[path],
                    unresolved=len(result.failures),
                )
                return (
                    f"ACCEPTED WITH WARNINGS (max rejections reached for {path}). "
                    f"Unresolved issues: {'; '.join(result.failures[:3])}. "
                    "Proceeding — downstream quality gates will catch these. Continue with other files."
                )
            return result.rejection_message()

        self._rejection_counts.pop(path, None)
        self._files[path] = content

        # CHANGE-4: Build export registry for TS/JS hallucination detection
        if path.endswith((".ts", ".tsx", ".js", ".jsx")):
            import re as _re
            names = set()
            # Extract exported names from TS/JS
            for m in _re.finditer(r"export\s+(?:default\s+)?(?:function|class|const|let|var|type|interface|enum)\s+(\w+)", content):
                names.add(m.group(1))
            if names:
                self._export_registry[path] = names

        msg = f"Written {path} ({len(content)} chars)"
        if result.warnings:
            msg += "\n⚠ Warnings:\n" + "\n".join(f"  • {w}" for w in result.warnings[:3])

        # CHANGE-30: Auto-inject file manifest — agent PERCEIVES its own output
        manifest_lines = ["\n\n📂 FILES YOU HAVE GENERATED (use exact names for imports):"]
        for fpath in sorted(self._files.keys()):
            fcontent = self._files[fpath]
            if not fcontent:
                continue
            fline_count = fcontent.count("\n") + 1
            fexports = self._export_registry.get(fpath, set())
            fexport_str = f" — exports: {', '.join(sorted(fexports)[:10])}" if fexports else ""
            manifest_lines.append(f"  - `{fpath}` ({fline_count} lines){fexport_str}")
        if len(manifest_lines) > 1:
            manifest_lines.append("  ⚠ USE EXACT NAMES ABOVE for import statements!")
            msg += "\n".join(manifest_lines)

        return msg

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

    async def _compile_check(self, path: str) -> str:
        """1.4-FIX: Run tsc --noEmit on a generated TypeScript file.

        Writes all generated files to a temp directory with a minimal tsconfig,
        then runs tsc. Returns compiler output capped at 5KB.
        """
        import subprocess
        import tempfile

        code = self._files.get(path)
        if not code:
            return f"File not found: {path}. Available: {list(self._files.keys())[:20]}"
        if not path.endswith((".ts", ".tsx")):
            return "compile_check only supports .ts/.tsx files."

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                import os
                import json as _json
                # Write all generated files
                for fpath, content in self._files.items():
                    full = os.path.join(tmpdir, fpath)
                    os.makedirs(os.path.dirname(full), exist_ok=True)
                    with open(full, "w", encoding="utf-8") as f:
                        f.write(content)

                # Write minimal tsconfig.json
                tsconfig = {
                    "compilerOptions": {
                        "target": "ES2020",
                        "module": "ESNext",
                        "moduleResolution": "node",
                        "jsx": "react-jsx",
                        "noEmit": True,
                        "strict": False,  # Lenient — catch only real errors
                        "skipLibCheck": True,
                        "esModuleInterop": True,
                    },
                    "include": ["./**/*.ts", "./**/*.tsx"],
                }
                with open(os.path.join(tmpdir, "tsconfig.json"), "w") as f:
                    _json.dump(tsconfig, f)

                result = subprocess.run(
                    ["npx", "tsc", "--noEmit", "--pretty"],
                    capture_output=True, text=True, timeout=30, cwd=tmpdir,
                )
                output = (result.stdout + result.stderr)[:5120]
                status = "PASS" if result.returncode == 0 else f"FAIL (exit {result.returncode})"
                return f"[{status}]\n{output}" if output else f"[{status}]"
        except FileNotFoundError:
            return "[SKIP] tsc/npx not available in this environment."
        except subprocess.TimeoutExpired:
            return "[TIMEOUT] TypeScript compilation exceeded 30s limit."
        except Exception as exc:
            return f"[ERROR] {type(exc).__name__}: {str(exc)[:500]}"

    async def _self_review(self, focus: str = "all") -> str:
        """1.4-FIX: Request a cheap-model review of generated frontend code."""
        if not self._files:
            return "No files to review yet. Write files first."

        code_summary_parts = []
        budget = 15_000
        for path, content in self._files.items():
            chunk = f"\n--- {path} ---\n{content[:3000]}"
            if len(chunk) > budget:
                break
            code_summary_parts.append(chunk)
            budget -= len(chunk)
        code_summary = "".join(code_summary_parts)

        review_prompt = (
            f"Review the following generated frontend code. Focus: {focus}.\n"
            "Check for:\n"
            "1. Missing imports (React, hooks, components)\n"
            "2. Inconsistent prop types between parent/child components\n"
            "3. Broken routing (wrong paths, missing route definitions)\n"
            "4. Missing error boundaries or loading states\n"
            "5. Hardcoded API URLs that should use environment variables\n"
            "6. Accessibility issues (missing alt text, aria labels)\n\n"
            "Return a numbered list of issues found, or 'NO ISSUES FOUND' if clean.\n\n"
            f"Code:\n{code_summary}"
        )

        try:
            from app.services.ai_router import get_ai_router, AIRequest, AIMessage
            from app.agents.base import TaskComplexity
            # AUDIT-T1-10: Use singleton — AIRouter() creates orphaned instances
            router = get_ai_router()
            response = await router.call(AIRequest(
                messages=[AIMessage(role="user", content=review_prompt)],
                complexity=TaskComplexity.LOW,
                max_tokens=1000,
                run_id=self._pipeline_run_id,
                agent_name="aanya_self_review",
            ))
            return f"## Self-Review Results:\n{response.content}"
        except Exception as exc:
            return f"Self-review unavailable: {type(exc).__name__}: {str(exc)[:200]}"

    def _list_files(self) -> str:
        if not self._files:
            return "No files written yet"
        lines = [f"- {path} ({len(content)} chars)" for path, content in self._files.items()]
        return "\n".join(lines)

    async def _ask_agent(self, agent_name: str, question: str, context: str = "") -> str:
        # V1-FIX: Go straight to oracle — pipeline runs agents sequentially,
        # so the message bus BLPOP always times out (target agent never listens).
        # When concurrent agent execution is added, re-enable message bus here.
        if self._pipeline_context:
            from app.services.agent_oracle import query_agent_context
            return query_agent_context(self._pipeline_context, agent_name, question)

        return f"Agent {agent_name} not available. Proceed with best judgment."

    def _task_complete(self, summary: str) -> str:
        # CHANGE-14: Completion gate — verify all required files before
        # allowing the LLM to declare "done". Prevents premature completion.
        goal_tracker = getattr(self, "_goal_tracker", None)
        if goal_tracker and not goal_tracker.is_all_done():
            missing = [
                g.file_path for g in goal_tracker.goals
                if not g.completed and not g.failed
            ]
            if missing:
                return (
                    f"NOT READY: {len(missing)} file(s) still need to be generated:\n"
                    + "\n".join(f"  - {f}" for f in missing[:10])
                    + "\nWrite these files first, then call task_complete again."
                )
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
        """Verify imports resolve using shared code_validator.

        CHANGE-4: Also checks imported NAMES against the export registry.
        """
        code = self._files.get(path, "")
        if not code:
            return f"File not found: {path}"
        from app.agents.tools.code_validator import check_imports, extract_project_files

        project_files, req_packages = extract_project_files(self._pipeline_context)
        # Include files from current session and backend refs
        for fp in list(self._files) + list(self._backend_files):
            if fp.endswith(".py"):
                mod = fp.replace("/", ".").replace("\\", ".")
                if mod.endswith(".py"):
                    mod = mod[:-3]
                project_files.add(mod)
        base_result = check_imports(path, code, project_files, req_packages)

        # CHANGE-4: Check imported names against export registry (TS/JS)
        import re as _re
        name_issues: list[str] = []
        # For TS/JS files, check named imports: import { X, Y } from './path'
        named_imports = _re.findall(r"import\s+\{([^}]+)\}\s+from\s+['\"]([^'\"]+)['\"]", code)
        for names_str, import_path in named_imports:
            # Find matching file in registry
            for reg_path, reg_names in self._export_registry.items():
                # Match by filename stem
                import_stem = import_path.rstrip("/").split("/")[-1].replace("@/", "")
                reg_stem = reg_path.split("/")[-1].rsplit(".", 1)[0]
                if import_stem == reg_stem or import_path.endswith(reg_stem):
                    imported = [n.strip() for n in names_str.split(",")]
                    for imp in imported:
                        if imp and imp not in reg_names:
                            name_issues.append(
                                f"'{imp}' not exported by '{import_path}'. "
                                f"Available: {sorted(reg_names)[:5]}"
                            )
                    break
        if name_issues:
            base_result += "\n\n⚠ Export validation:\n" + "\n".join(f"  • {i}" for i in name_issues)
        return base_result

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
            pass  # Expected: resolved key not found — fall through to direct name

    # Try direct name
    try:
        return get_frontend_framework_config(frontend_name)
    except (ValueError, KeyError):
        pass  # Expected: direct name not found — fall through to fallback

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
        pass  # Expected: config not registered — fall through to legacy orders

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
        pass  # Expected: config not registered — fall through to legacy rules

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
        # CHANGE-28: Store reference so _build_agentic_system_prompt can access
        # actual file contents to extract exports for real context sharing.
        self._backend_file_contents_ref = backend_file_contents

        # ── Phase 2: Agentic AI generation (tool loop) ──
        tool_handler = AanyaToolHandler(
            pipeline_run_id=pipeline_run_id,
            generated_files=generated_files,
            pipeline_context=context,  # For agent oracle fallback in ask_backend/ask_architect
            backend_files=backend_file_contents,  # AUDIT-FIX: lazy-load ref
        )

        # PHASE-H: Initialize goal tracker for progress tracking
        from app.agents.base import GoalTracker, AdaptiveComplexity
        goal_tracker = GoalTracker(generation_order)

        # PHASE-I: Start with cheapest model, escalate on failures.
        # Wire into tool handler so observe() can update on pass/fail.
        adaptive = AdaptiveComplexity()
        tool_handler._adaptive = adaptive
        tool_handler._goal_tracker = goal_tracker  # CHANGE-14: Wire for completion gate

        logger.info(
            "agentic_phase_start",
            framework=framework_display,
            files_to_generate=len(generation_order),
            template_files_available=len(generated_files),
            goals=goal_tracker.summary(),
        )

        # FIX-40: Inject rejected approaches so Aanya avoids user-rejected UI patterns
        from app.agents.base import build_rejection_context
        _rejection_ctx = build_rejection_context(context)

        system_prompt = self._build_agentic_system_prompt(
            contract=contract,
            generation_order=generation_order,
            backend_files=list(backend_file_contents.keys()),
            fw_config=fw_config,
            framework_display=framework_display,
            design_spec=vanya_output.get("design_spec", ""),
            user_feedback=(user_feedback + _rejection_ctx) if _rejection_ctx else user_feedback,
        )

        # SDD-FIX: Inject Software Design Document context so Aanya sees
        # the full project scope — user roles (for protected routes/UI),
        # integrations, and page-level design decisions from the design phase.
        _sdd = context.get("__sdd__")
        if _sdd and isinstance(_sdd, dict):
            _sdd_parts: list[str] = ["\n## Software Design Document — Project Context"]
            _overview = _sdd.get("project_overview", {})
            if _overview.get("assumptions"):
                _sdd_parts.append(
                    "### Design Assumptions\n"
                    + "\n".join(f"- {a}" for a in _overview["assumptions"][:10])
                )
            _roles = _sdd.get("user_roles", [])
            if _roles:
                _role_strs = []
                for r in _roles[:8]:
                    if isinstance(r, dict):
                        _role_strs.append(f"- **{r.get('name', r.get('role', 'user'))}**: {r.get('permissions', r.get('description', ''))}")
                    else:
                        _role_strs.append(f"- {r}")
                _sdd_parts.append("### User Roles & Permissions\n" + "\n".join(_role_strs))
                _sdd_parts.append(
                    "**Your frontend MUST implement role-based routing and "
                    "conditional UI rendering for these roles.**"
                )
            _fe = _sdd.get("frontend", {})
            _pages = _fe.get("pages", [])
            if _pages:
                _page_strs = []
                for p in _pages[:15]:
                    if isinstance(p, dict):
                        _page_strs.append(f"- `{p.get('path', p.get('route', '/'))}` — {p.get('name', p.get('description', ''))}")
                    else:
                        _page_strs.append(f"- {p}")
                _sdd_parts.append(
                    "### SDD Pages (MUST implement ALL)\n" + "\n".join(_page_strs)
                )
            _integrations = _sdd.get("integrations", [])
            if _integrations:
                _sdd_parts.append(
                    "### Integrations Required\n"
                    + "\n".join(f"- {i}" for i in _integrations[:6])
                )
            _sec = _sdd.get("security", {})
            _compliance = _sec.get("compliance_flags", [])
            if _compliance:
                _sdd_parts.append(
                    "### Compliance Requirements\n"
                    + "\n".join(f"- {c}" for c in _compliance[:5])
                )
            if len(_sdd_parts) > 1:  # Has content beyond the header
                system_prompt += "\n".join(_sdd_parts) + "\n"

        # PHASE-3: Check inbox for messages from other agents
        _prev_outputs = {
            "vikram": f"Contract for {contract.get('project_name', 'project')}",
            "shubham": f"Backend: {len(backend_file_contents)} files",
            "vanya": f"Design spec: {bool(vanya_output.get('design_spec'))}",
        }
        try:
            from app.agents.base import check_inbox, format_inbox_for_prompt
            _inbox_msgs = await check_inbox(self.name, pipeline_run_id)
            if _inbox_msgs:
                _inbox_text = format_inbox_for_prompt(_inbox_msgs)
                _prev_outputs["_agent_messages"] = _inbox_text
                # AUTHORITY directives from Tilotma/Vikram override normal flow
                if "AUTHORITY" in _inbox_text:
                    system_prompt += (
                        "\n\n⚠ AUTHORITY DIRECTIVE RECEIVED — you MUST comply:\n"
                        + _inbox_text
                    )
        except Exception as _ib_exc:
            logger.debug("aanya_inbox_failed", error=str(_ib_exc)[:100])

        # PHASE-10: Enrich prompt with learned knowledge, lessons, and warnings
        try:
            from app.services.dynamic_prompt_builder import get_dynamic_prompt_builder
            _dpb = get_dynamic_prompt_builder()
            system_prompt = await _dpb.build_system_prompt(
                agent_name=self.name,
                task_context={
                    "task_type": "frontend_generation",
                    "framework": frontend_framework,
                    "task_summary": f"Generate {framework_display} frontend ({len(generation_order)} files)",
                    "previous_agent_outputs": _prev_outputs,
                },
                base_prompt_fallback=system_prompt,
            )
        except Exception as _dpb_exc:
            logger.debug("dynamic_prompt_fallback", agent=self.name, error=str(_dpb_exc)[:100])

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
                complexity=adaptive.current,  # PHASE-I: Start cheap, escalate on failure
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
                    await tracker.record(response, agent_name=self.name, model_key="high")
            except Exception as _cost_exc:
                logger.debug("cost_tracking_failed", error=str(_cost_exc)[:200])

        except Exception as exc:
            # R28-FIX-1: Sanitize exceptions so API keys never appear in logs or
            # in accumulated_code output.  Use _sanitize_error instead of str(exc).
            from app.services.ai_router import _sanitize_error  # R27-FIX
            safe_err = _sanitize_error(exc)
            logger.error("agentic_generation_failed", error=safe_err)
            # Non-fatal: continue with whatever was written before the exception

        # PHASE-H: Track goal completion based on which files were generated
        for goal in goal_tracker.goals:
            if goal.file_path in generated_files and generated_files.get(goal.file_path):
                goal_tracker.mark_complete(goal.id)
        logger.info("goal_tracking_complete", **goal_tracker.summary())

        # generated_files has been mutated in place by AanyaToolHandler._write_file
        # (handler holds a reference to the same dict)
        frontend_files = [p for p in generated_files if "frontend/" in p or "mobile/" in p or "desktop/" in p]

        # ── Layer 1: Builder Self-Check (1.4-FIX: BLOCKING) ──
        # Two-layer validation:
        #   Layer 1a: Deterministic checks (syntax, brackets, empty files)
        #   Layer 1b: CHANGE-26: LLM-driven semantic evaluation
        self_check = self._run_self_check(generated_files, contract, framework_display)

        # CHANGE-26: LLM semantic self-evaluation — the agent evaluates its OWN
        # output against the contract. This shifts agency FROM pipeline TO the LLM.
        try:
            llm_eval = await self._run_llm_self_evaluation(
                generated_files, contract, pipeline_run_id,
            )
            if llm_eval.get("missing_implementations"):
                for issue in llm_eval["missing_implementations"]:
                    self_check.setdefault("error_details", []).append({
                        "file": issue.get("file", "(missing)"),
                        "issue": issue.get("issue", "LLM-detected gap"),
                    })
                    self_check["errors"] = self_check.get("errors", 0) + 1
                self_check["passed"] = False
            if llm_eval.get("wrong_imports"):
                for issue in llm_eval["wrong_imports"]:
                    self_check.setdefault("error_details", []).append({
                        "file": issue.get("file", "unknown"),
                        "issue": issue.get("issue", "wrong import"),
                    })
                    self_check["errors"] = self_check.get("errors", 0) + 1
                self_check["passed"] = False
            self_check["llm_evaluation"] = llm_eval
            logger.info(
                "llm_self_evaluation_complete",
                missing=len(llm_eval.get("missing_implementations", [])),
                wrong_imports=len(llm_eval.get("wrong_imports", [])),
                completeness_pct=clamp_completeness(llm_eval.get("completeness_pct", 0)),
            )
        except Exception as exc:
            from app.services.ai_router import _sanitize_error
            logger.warning("llm_self_evaluation_failed", error=_sanitize_error(exc))

        _self_fix_cycles = 0

        while self_check.get("errors", 0) > 0 and _self_fix_cycles < 3:
            _self_fix_cycles += 1
            error_details = self_check.get("error_details", [])
            error_summary = "\n".join(
                f"- {e['file']}: {e['issue']}" for e in error_details[:10]
            )
            fix_prompt = (
                f"Your generated code has {self_check['errors']} error(s) that MUST be fixed:\n\n"
                f"{error_summary}\n\n"
                "Fix each error by calling write_file with corrected content. "
                "Then call task_complete when all errors are resolved."
            )

            # CHANGE-21: Adaptive prompt revision on 2nd+ cycle
            if _self_fix_cycles >= 2:
                chronic = {
                    p: r for p, r in getattr(tool_handler, "_unresolved", {}).items()
                    if getattr(tool_handler, "_rejection_counts", {}).get(p, 0) >= 3
                }
                if chronic:
                    guidance_parts = [
                        "\n## TARGETED GUIDANCE (based on repeated failures):"
                    ]
                    for _path, _reasons in list(chronic.items())[:3]:
                        guidance_parts.append(
                            f"- {_path}: keeps failing because: "
                            f"{_reasons[0] if _reasons else 'unknown'}"
                        )
                    try:
                        from app.services.mistake_memory import mistake_memory
                        _lessons = mistake_memory.build_lessons_prompt(
                            self.name, "frontend_generation",
                            f"errors: {error_summary[:300]}",
                        )
                        if _lessons:
                            guidance_parts.append(_lessons[:500])
                    except Exception as _mm_exc:
                        logger.debug("mistake_memory_lookup_failed", error=str(_mm_exc)[:200])
                    system_prompt = system_prompt + "\n".join(guidance_parts)

            logger.warning(
                "self_check_fix_cycle",
                cycle=_self_fix_cycles,
                errors=self_check.get("errors", 0),
            )
            tool_handler._done = False
            try:
                await call_ai_with_tools(
                    self,
                    messages=[{"role": "user", "content": fix_prompt}],
                    system_prompt=system_prompt,
                    task_type="general",
                    tool_handler=tool_handler,
                    max_tool_rounds=5,
                )
            except Exception as exc:
                from app.services.ai_router import _sanitize_error
                logger.error("self_fix_cycle_failed", cycle=_self_fix_cycles, error=_sanitize_error(exc))
                break
            self_check = self._run_self_check(generated_files, contract, framework_display)

        logger.info(
            "self_check_complete",
            errors=self_check.get("errors", 0),
            warnings=self_check.get("warnings", 0),
            passed=self_check.get("passed", False),
            fix_cycles=_self_fix_cycles,
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
            "self_fix_cycles": _self_fix_cycles,
            "goal_tracker": goal_tracker.summary(),  # PHASE-H
            "adaptive_complexity": adaptive.summary(),  # PHASE-I
        }

        # PHASE-2: Agent self-testing — start frontend and test pages in browser
        try:
            from app.services.agent_sandbox import AgentSandbox
            from app.services.project_workspace import get_workspace
            _ws = get_workspace(pipeline_run_id)
            for _fp, _fc in generated_files.items():
                _ws.write_file(_fp, _fc, agent=self.name)
            _sandbox = AgentSandbox(run_id=pipeline_run_id, workspace_path=_ws.get_docker_bind_mount())
            _start = await _sandbox.start_frontend(framework=frontend_framework)
            if _start.get("status") in ("running", "simulated", "started_unhealthy"):
                # Test each page from the contract
                _pages = contract.get("frontend", {}).get("pages", [])
                _test_results = []
                for _pg in _pages[:8]:  # Test first 8 pages
                    _route = _pg.get("route", "/")
                    _tr = await _sandbox.test_page(path=_route)
                    _test_results.append({
                        "route": _route,
                        "loaded": _tr.loaded,
                        "js_errors": len(_tr.js_errors),
                        "missing_elements": _tr.missing_elements,
                        "error": _tr.error,
                    })
                output["self_test_results"] = {
                    "server_started": True,
                    "simulated": _start.get("status") == "simulated",
                    "pages_tested": len(_test_results),
                    "pages_loaded": sum(1 for t in _test_results if t["loaded"]),
                    "details": _test_results,
                }
                logger.info(
                    "frontend_self_test_complete",
                    tested=len(_test_results),
                    loaded=sum(1 for t in _test_results if t["loaded"]),
                    simulated=_start.get("status") == "simulated",
                )
            else:
                output["self_test_results"] = {
                    "server_started": False,
                    "error": _start.get("error", "Frontend start failed"),
                }
                logger.warning("frontend_self_test_server_failed", status=_start.get("status"))
            await _sandbox.cleanup()
        except Exception as _st_exc:
            logger.warning("frontend_self_test_skipped", error=str(_st_exc)[:200])

        # PHASE-3: Report failures to Shubham if self-test found broken endpoints
        try:
            _self_test = output.get("self_test_results", {})
            _failed_pages = [
                d for d in _self_test.get("details", [])
                if not d.get("loaded") and d.get("error")
            ]
            if _failed_pages:
                from app.agents.base import report_error_to_agent
                for _fp in _failed_pages[:3]:  # Report first 3
                    await report_error_to_agent(
                        from_agent=self.name,
                        to_agent="shubham",
                        pipeline_run_id=pipeline_run_id,
                        error_type="broken_page",
                        file_path=_fp.get("route", "/"),
                        description=f"Page failed to load: {_fp.get('error', 'unknown')}",
                    )
            # Notify others about frontend completion
            from app.agents.base import notify_agents
            await notify_agents(
                from_agent=self.name,
                pipeline_run_id=pipeline_run_id,
                message=(
                    f"Frontend generation complete: {len(generated_files)} files, "
                    f"{framework_display} framework. "
                    f"Self-test: {_self_test.get('pages_loaded', 'N/A')}/{_self_test.get('pages_tested', 'N/A')} pages loaded."
                ),
                to_agents=["karan", "aarav"],
            )
        except Exception as _notify_exc:
            logger.debug("aanya_notify_failed", error=str(_notify_exc)[:100])

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

    # ── CHANGE-26: LLM-driven semantic self-evaluation ────────────────

    async def _run_llm_self_evaluation(
        self,
        generated_files: dict[str, str],
        contract: dict[str, Any],
        pipeline_run_id: str,
    ) -> dict[str, Any]:
        """CHANGE-26: The agent evaluates its OWN output against the contract.

        The LLM reads its own generated frontend files, compares to the contract's
        page/component requirements, and reports what's missing. This is the
        agent exercising JUDGMENT, not just running bracket checks.
        """
        # Build compact summary of generated files
        file_summaries: list[str] = []
        for path, content in sorted(generated_files.items()):
            if not content:
                continue
            line_count = content.count("\n") + 1
            # Extract exported names for TS/TSX/JS files
            exports: list[str] = []
            if path.endswith((".ts", ".tsx", ".js", ".jsx")):
                import re as _re
                for m in _re.finditer(
                    r"export\s+(?:default\s+)?(?:function|class|const|let|var|type|interface|enum)\s+(\w+)",
                    content,
                ):
                    exports.append(m.group(1))
            export_str = f" — exports: {', '.join(exports[:8])}" if exports else ""
            file_summaries.append(f"  {path} ({line_count} lines){export_str}")

        files_text = "\n".join(file_summaries)

        # Extract contract frontend requirements
        pages = contract.get("frontend", {}).get("pages", [])
        page_summary = ""
        if pages:
            pg_lines = []
            for pg in pages[:15]:
                if isinstance(pg, dict):
                    pg_lines.append(f"  - {pg.get('name', pg.get('title', 'unknown'))}: {pg.get('description', '')[:80]}")
                elif isinstance(pg, str):
                    pg_lines.append(f"  - {pg}")
            page_summary = "Required pages:\n" + "\n".join(pg_lines)

        eval_prompt = (
            "You are reviewing frontend code YOU just generated. Be brutally honest.\n\n"
            f"## Contract Requirements\n{page_summary}\n\n"
            f"## Files You Generated\n{files_text}\n\n"
            "## Your Task\n"
            "Compare what the contract REQUIRES vs what you ACTUALLY generated.\n"
            "Find:\n"
            "1. Missing implementations — pages/components required but NOT generated\n"
            "2. Wrong imports — files importing components/hooks that don't exist\n"
            "3. Completeness percentage\n\n"
            "Respond in JSON:\n"
            "{\n"
            '  "missing_implementations": [{"file": "path or (missing)", "issue": "description"}],\n'
            '  "wrong_imports": [{"file": "path", "issue": "description"}],\n'
            '  "completeness_pct": 0-100,\n'
            '  "verdict": "PASS" or "FAIL",\n'
            '  "reasoning": "brief explanation"\n'
            "}\n"
        )

        from app.services.ai_router import get_ai_router, AIRequest, AIMessage
        from app.agents.base import TaskComplexity

        router = get_ai_router()
        response = await router.call(AIRequest(
            messages=[AIMessage(role="user", content=eval_prompt)],
            complexity=TaskComplexity.LOW,
            max_tokens=1000,
            agent_name=f"{self.name}_self_eval",
        ))

        try:
            from app.services.pipeline import get_run_cost_tracker
            tracker = get_run_cost_tracker(pipeline_run_id)
            if tracker is not None:
                await tracker.record(response, agent_name=self.name, model_key="low")
        except Exception as _cost_exc:
            logger.debug("cost_tracking_failed", error=str(_cost_exc)[:200])

        from app.utils.json_parser import parse_json
        result = parse_json(response.content, fallback={})
        if not isinstance(result, dict):
            result = {}
        return result

    @staticmethod
    def _extract_python_exports(content: str) -> list[str]:
        """CHANGE-28: Extract top-level class/function names from Python code."""
        import ast as _ast
        try:
            tree = _ast.parse(content)
            names: list[str] = []
            for node in _ast.iter_child_nodes(tree):
                if isinstance(node, (_ast.ClassDef, _ast.FunctionDef, _ast.AsyncFunctionDef)):
                    names.append(node.name)
            return names
        except SyntaxError:
            return []

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
            "- **run_frontend_test(sandbox_id, test_command)** — run vitest/jest inside the sandbox",
            "- **run_type_check(sandbox_id)** — run tsc --noEmit to check TypeScript types",
            "- **task_complete(summary)** — signal completion (call LAST when all files written AND tested)",
            "",
            "## MANDATORY: Run Tests Before Completion",
            "After writing ALL frontend files, you MUST:",
            "1. Call `run_type_check` to verify TypeScript types compile cleanly. Fix any type errors.",
            "2. Call `run_frontend_test` with the appropriate test command (e.g. 'npx vitest run --reporter=verbose').",
            "3. If tests fail, FIX the code and re-run. Repeat until tests pass.",
            "4. You are FORBIDDEN from calling `task_complete` until BOTH type-check and tests pass.",
            "5. A `task_complete` without preceding successful test runs is a FAILURE.",
            "",
        ])

        # ── 2. Architecture Contract ──
        prompt_parts.extend([
            "## Architecture Contract (SINGLE SOURCE OF TRUTH)",
            f"```json\n{contract_json}\n```",
            "",
        ])

        # ── 3. Backend files available (CHANGE-28: include exports) ──
        # Show Aanya the actual exports from Shubham's backend files so she
        # can use correct names for API types, schemas, etc. This is REAL
        # context sharing: the agent SEES what the other agent produced.
        if backend_files:
            prompt_parts.extend([
                "## Backend Files Available (from Shubham)",
                "These are the ACTUAL backend files with their exports.",
                "Use read_file to inspect full content. Use EXACT export names for types.",
            ])
            # Get actual file contents if available to extract exports
            _backend_contents = getattr(self, "_backend_file_contents_ref", {}) or {}
            for bf in backend_files[:25]:
                bf_content = _backend_contents.get(bf, "")
                if bf_content and bf.endswith(".py"):
                    bf_exports = self._extract_python_exports(bf_content)
                    export_str = f" — exports: **{', '.join(bf_exports[:8])}**" if bf_exports else ""
                    prompt_parts.append(f"- `{bf}`{export_str}")
                else:
                    prompt_parts.append(f"- `{bf}`")
            if len(backend_files) > 25:
                prompt_parts.append(f"  ... and {len(backend_files) - 25} more")
            prompt_parts.append("⚠ When creating TypeScript types, match the EXACT names above!")
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

        # ── 5b. MANDATORY FOLDER STRUCTURE (FIX-21) ──
        # Without this, AI dumps all components in App.tsx or creates flat layout.
        prompt_parts.extend([
            "## MANDATORY FOLDER STRUCTURE (NEVER dump all code in one file)",
            "",
            "### Next.js:",
            "```",
            "frontend/",
            "├── src/",
            "│   ├── app/                  # App router pages",
            "│   │   ├── layout.tsx",
            "│   │   ├── page.tsx",
            "│   │   └── {route}/",
            "│   │       └── page.tsx",
            "│   ├── components/           # ONE component per file",
            "│   │   ├── ui/               # Reusable UI (Button, Input, Card)",
            "│   │   └── {feature}/        # Feature-specific components",
            "│   ├── hooks/                # Custom React hooks",
            "│   ├── lib/                  # API client, utils",
            "│   │   ├── api-client.ts",
            "│   │   └── utils.ts",
            "│   ├── types/                # TypeScript interfaces",
            "│   │   └── index.ts",
            "│   └── styles/",
            "│       └── globals.css",
            "├── public/",
            "├── package.json",
            "├── Dockerfile",
            "├── .env.local.example",
            "└── tsconfig.json",
            "```",
            "",
            "### React (Vite):",
            "```",
            "frontend/",
            "├── src/",
            "│   ├── main.tsx",
            "│   ├── App.tsx               # Routes ONLY",
            "│   ├── components/",
            "│   ├── pages/",
            "│   ├── hooks/",
            "│   ├── lib/",
            "│   ├── types/",
            "│   └── styles/",
            "├── package.json",
            "├── Dockerfile",
            "├── .env.local.example",
            "└── vite.config.ts",
            "```",
            "",
            "RULES:",
            "1. NEVER put more than one component per file",
            "2. Each page is a separate file in pages/ or app/",
            "3. Shared UI components in components/ui/",
            "4. API calls ONLY through lib/api-client.ts",
            "5. No business logic in components — use hooks/ for shared state logic",
            "",
        ])

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

        # ── 8. Design Specification (FIX-35: Read Vanya's full design system) ──
        if design_spec:
            prompt_parts.append("## APPROVED UI/UX DESIGN SYSTEM (from Vanya)")
            prompt_parts.append("You MUST follow this design system. Do NOT invent your own colors or typography.")
            if isinstance(design_spec, dict):
                import orjson as _orjson
                # Structured design spec — format key sections for clarity
                if "colors" in design_spec:
                    prompt_parts.append(f"### Color Palette\n{_orjson.dumps(design_spec['colors'], option=_orjson.OPT_INDENT_2).decode()}")
                if "typography" in design_spec:
                    prompt_parts.append(f"### Typography\n{_orjson.dumps(design_spec['typography'], option=_orjson.OPT_INDENT_2).decode()}")
                if "spacing" in design_spec:
                    prompt_parts.append(f"### Spacing Scale\n{_orjson.dumps(design_spec['spacing'], option=_orjson.OPT_INDENT_2).decode()}")
                if "components" in design_spec:
                    prompt_parts.append(f"### Component Hierarchy\n{_orjson.dumps(design_spec['components'], option=_orjson.OPT_INDENT_2).decode()}")
                if "layout" in design_spec:
                    prompt_parts.append(f"### Layout Guidelines\n{_orjson.dumps(design_spec['layout'], option=_orjson.OPT_INDENT_2).decode()}")
                # Include any remaining keys not already printed
                remaining = {k: v for k, v in design_spec.items()
                             if k not in ("colors", "typography", "spacing", "components", "layout")}
                if remaining:
                    prompt_parts.append(f"### Additional Design Tokens\n{_orjson.dumps(remaining, option=_orjson.OPT_INDENT_2).decode()}")
            else:
                prompt_parts.append(str(design_spec))
            prompt_parts.append("")

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

        # ── 10b. CRITICAL: API URL Configuration (MASTER-PROMPT-FIX) ──
        prompt_parts.extend([
            "## CRITICAL: API URL Configuration — NEVER Hardcode URLs",
            "",
            "The generated frontend MUST read ALL API URLs from environment variables.",
            "NEVER hardcode `http://localhost:8000` or any URL in source code.",
            "",
            "### For React/Vite projects:",
            "Create `src/lib/api.ts` or `src/services/api.ts` with:",
            "```typescript",
            "const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';",
            "const WS_BASE_URL = import.meta.env.VITE_WS_URL || 'ws://localhost:8000';",
            "",
            "export const apiClient = {",
            "  baseURL: API_BASE_URL,",
            "  async get(path: string) { return fetch(`${API_BASE_URL}${path}`, ...) },",
            "  async post(path: string, data: any) { ... },",
            "};",
            "```",
            "",
            "### For Next.js projects:",
            "Use `NEXT_PUBLIC_API_URL` for client-side and `API_URL` for server-side:",
            "```typescript",
            "const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';",
            "```",
            "",
            "### JWT Token Handling:",
            "Add a request interceptor that attaches the JWT token to every API call:",
            "```typescript",
            "// In api.ts — attach Authorization header",
            "const token = localStorage.getItem('access_token');",
            "if (token) headers['Authorization'] = `Bearer ${token}`;",
            "```",
            "",
            "### .env.local.example (MUST be generated):",
            "Write a `.env.local.example` file listing all required env vars:",
            "```",
            "VITE_API_URL=http://localhost:8000",
            "VITE_WS_URL=ws://localhost:8000",
            "```",
            "",
            "**VIOLATION: Any API call using a hardcoded URL like `fetch('http://localhost:8000/...')` is WRONG.**",
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
            "6. **MUST** generate an API client file (src/lib/api.ts or equivalent) that reads URL from env var.",
            "7. **MUST** generate a `.env.local.example` file listing all required frontend env vars.",
            "8. When ALL files are written, call task_complete with a summary.",
            "",
            "Write ONLY valid TypeScript/JSX code — no markdown fences, no explanations.",
            "Match entity/field names EXACTLY from the contract.",
        ])

        # ── 12. Mistake Memory (1.5-FIX) ──
        # Close the Fixer → Builder learning loop: Fixer records mistakes
        # after fixing code. Aanya reads them here to avoid repeating errors.
        try:
            from app.services.mistake_memory import mistake_memory
            lessons = mistake_memory.build_lessons_prompt(
                self.name, "frontend_generation",
                f"framework={framework_display}, files={len(generation_order)}",
            )
            if lessons:
                prompt_parts.append(lessons)

            # CHANGE-25: Proactive error prevention
            patterns = mistake_memory.analyze_error_patterns(self.name)
            if patterns:
                warning_parts = ["\n## PROACTIVE WARNINGS (your top error types):"]
                for p in patterns[:3]:
                    warning_parts.append(
                        f"- {p['type']} ({p['pct']:.0%} of past errors): "
                        f"{p['example'][:100]}"
                    )
                warning_parts.append(
                    "Pay EXTRA attention to avoiding these error types."
                )
                prompt_parts.append("\n".join(warning_parts))
        except Exception as _mm_exc:
            logger.debug("mistake_memory_prompt_failed", error=str(_mm_exc)[:200])

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
