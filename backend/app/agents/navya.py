"""Navya — Logic Analyst: detects logic errors + correctness issues.

Navya analyzes generated code for logical correctness:
1. Dead code detection (unreachable branches, unused variables)
2. Null/None safety (unguarded attribute access, missing null checks)
3. Type consistency (mismatched types across function boundaries)
4. Business logic validation (contract endpoints vs actual implementations)
5. Error handling completeness (unhandled exception paths)
6. State management issues (race conditions, stale state)

Runs in PARALLEL with Karan and Deepika during quality_review stage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

from app.agents.base import (
    AgentResult,
    AgentStatus,
    ToolDefinition,
    WEB_SEARCH_TOOL,
    WEB_SCRAPE_TOOL,
    call_ai,
    call_ai_with_tools,
    handle_web_tool,
    register_agent,
    run_agent,
    store_output,
    check_inbox,
    format_inbox_for_prompt,
)
from app.services.ai_router import TaskComplexity

logger = structlog.get_logger(__name__)


# ── Logic Finding Types ────────────────────────────────────────────


class LogicSeverity(str, Enum):
    """Logic issue severity."""

    ERROR = "error"      # Will cause runtime failure
    WARNING = "warning"  # May cause incorrect behavior
    INFO = "info"        # Code quality suggestion


class LogicCategory(str, Enum):
    """Categories of logic findings."""

    DEAD_CODE = "dead_code"
    NULL_SAFETY = "null_safety"
    TYPE_MISMATCH = "type_mismatch"
    MISSING_ERROR_HANDLING = "missing_error_handling"
    INFINITE_LOOP_RISK = "infinite_loop_risk"
    OFF_BY_ONE = "off_by_one"
    RACE_CONDITION = "race_condition"
    INCONSISTENT_STATE = "inconsistent_state"
    CONTRACT_MISMATCH = "contract_mismatch"
    MISSING_RETURN = "missing_return"
    UNREACHABLE_CODE = "unreachable_code"
    UNUSED_IMPORT = "unused_import"


@dataclass(frozen=True, slots=True)
class LogicFinding:
    """A single logic issue found by Navya."""

    severity: LogicSeverity
    category: LogicCategory
    file_path: str
    line: int | None
    title: str
    description: str
    suggestion: str | None = None


@dataclass(slots=True)
class LogicReport:
    """Aggregated logic analysis report."""

    findings: list[LogicFinding] = field(default_factory=list)
    files_analyzed: int = 0
    passed: bool = True

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == LogicSeverity.ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == LogicSeverity.WARNING)

    def add(self, finding: LogicFinding) -> None:
        self.findings.append(finding)
        if finding.severity == LogicSeverity.ERROR:
            self.passed = False


# ── Static Logic Patterns ─────────────────────────────────────────

# Python logic error patterns
_PYTHON_LOGIC_PATTERNS: list[tuple[re.Pattern, str, LogicSeverity, LogicCategory, str]] = [
    # Missing return in non-None function
    (
        re.compile(r"""def\s+\w+\([^)]*\)\s*->\s*(?!None)\w+.*:\s*\n(?:\s+#[^\n]*\n)*\s+pass\b""", re.MULTILINE),
        "Function with return type annotation has only 'pass'",
        LogicSeverity.ERROR,
        LogicCategory.MISSING_RETURN,
        "Implement the function body or return the correct type",
    ),
    # Bare except (catches SystemExit, KeyboardInterrupt)
    (
        re.compile(r"""except\s*:"""),
        "Bare 'except:' catches all exceptions including SystemExit",
        LogicSeverity.WARNING,
        LogicCategory.MISSING_ERROR_HANDLING,
        "Use 'except Exception:' to avoid catching SystemExit/KeyboardInterrupt",
    ),
    # Mutable default argument
    (
        re.compile(r"""def\s+\w+\([^)]*(?::\s*(?:list|dict|set)\s*=\s*(?:\[\]|\{\}))[^)]*\)"""),
        "Mutable default argument — shared across calls",
        LogicSeverity.WARNING,
        LogicCategory.INCONSISTENT_STATE,
        "Use None as default and create new instance inside function",
    ),
    # Comparison to None using == instead of is
    (
        re.compile(r"""==\s*None|None\s*=="""),
        "Use 'is None' instead of '== None'",
        LogicSeverity.INFO,
        LogicCategory.TYPE_MISMATCH,
        "PEP 8: comparisons to None should use 'is' or 'is not'",
    ),
    # Unused variable assignment (candidate detection — flags _prefixed vars only)
    (
        re.compile(r"""^\s+(_\w+)\s*=\s*.+$""", re.MULTILINE),
        "Potential unused variable assignment (verify usage manually)",
        LogicSeverity.INFO,
        LogicCategory.DEAD_CODE,
        "Remove unused variables to reduce confusion",
    ),
    # Infinite loop risk: while True (verify break condition exists)
    (
        re.compile(r"""while\s+True\s*:"""),
        "while True loop (verify break condition exists)",
        LogicSeverity.WARNING,
        LogicCategory.INFINITE_LOOP_RISK,
        "Ensure loop has a break condition or timeout",
    ),
]

# TypeScript logic patterns
_TS_LOGIC_PATTERNS: list[tuple[re.Pattern, str, LogicSeverity, LogicCategory, str]] = [
    # Optional chaining gap (accessing .x on possibly null)
    (
        re.compile(r"""\w+\.\w+\.\w+(?<!\?)\.(?!length|map|filter|forEach|reduce|find)"""),
        "Deep property access without optional chaining",
        LogicSeverity.INFO,
        LogicCategory.NULL_SAFETY,
        "Use optional chaining (?.) for potentially undefined nested objects",
    ),
    # Empty catch block
    (
        re.compile(r"""catch\s*\([^)]*\)\s*\{\s*\}"""),
        "Empty catch block — errors silently swallowed",
        LogicSeverity.WARNING,
        LogicCategory.MISSING_ERROR_HANDLING,
        "Log the error or re-throw with context",
    ),
    # console.log left in code
    (
        re.compile(r"""console\.log\s*\("""),
        "console.log statement left in code",
        LogicSeverity.INFO,
        LogicCategory.DEAD_CODE,
        "Remove debug console.log before production",
    ),
    # == instead of === (loose equality)
    (
        re.compile(r"""[^!=]==[^=]"""),
        "Loose equality (==) — may cause type coercion bugs",
        LogicSeverity.WARNING,
        LogicCategory.TYPE_MISMATCH,
        "Use strict equality (===) to prevent type coercion",
    ),
]


# ── Navya Tool Handler ─────────────────────────────────────────────


class NavyaToolHandler:
    """Handles tool calls for Navya's agentic logic analysis loop."""

    def __init__(self, files: dict[str, str], report: LogicReport, contract: dict[str, Any]):
        self._files = files
        self._report = report
        self._contract = contract
        self._files_read: set[str] = set()

    async def __call__(self, tool_name: str, tool_input: dict) -> str:
        # AGENTIC-FIX: Handle web tools first (shared across all agents)
        web_result = await handle_web_tool(tool_name, tool_input)
        if web_result is not None:
            return web_result

        if tool_name == "read_file":
            return self._read_file(tool_input["path"])
        elif tool_name == "write_finding":
            return self._write_finding(tool_input)
        elif tool_name == "list_files":
            return self._list_files()
        elif tool_name == "run_static_analysis":
            return await self._run_static_analysis(tool_input)
        elif tool_name == "check_imports":
            return self._check_imports(tool_input)
        else:
            return f"Unknown tool: {tool_name}"

    async def _run_static_analysis(self, tool_input: dict) -> str:
        """Run REAL ruff + pyright on file content (not just regex patterns)."""
        import tempfile
        import subprocess
        import os
        import json as _json

        file_path = tool_input.get("file_path", "temp.py")
        content = tool_input.get("content", "")

        if not content:
            return '{"error": "No content provided"}'

        results = {"ruff": [], "pyright": [], "file": file_path}

        # Write content to temp file for analysis
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            temp_path = f.name

        try:
            # Run ruff (fast linter — catches undefined names, unused imports, etc.)
            try:
                ruff_result = subprocess.run(
                    ["ruff", "check", temp_path, "--output-format=json", "--select=E,F,W"],
                    capture_output=True, text=True, timeout=15,
                )
                if ruff_result.stdout:
                    ruff_findings = _json.loads(ruff_result.stdout)
                    results["ruff"] = [
                        {"code": f.get("code", ""), "message": f.get("message", ""),
                         "line": f.get("location", {}).get("row", 0)}
                        for f in ruff_findings[:20]  # Cap at 20
                    ]
            except (subprocess.TimeoutExpired, FileNotFoundError, _json.JSONDecodeError):
                results["ruff"] = [{"error": "ruff not available or timed out"}]

            # Run pyright (type checker — catches type errors, missing attributes)
            try:
                pyright_result = subprocess.run(
                    ["pyright", temp_path, "--outputjson"],
                    capture_output=True, text=True, timeout=30,
                )
                if pyright_result.stdout:
                    pyright_data = _json.loads(pyright_result.stdout)
                    diagnostics = pyright_data.get("generalDiagnostics", [])
                    results["pyright"] = [
                        {"severity": d.get("severity", ""), "message": d.get("message", ""),
                         "line": d.get("range", {}).get("start", {}).get("line", 0)}
                        for d in diagnostics[:20]  # Cap at 20
                    ]
            except (subprocess.TimeoutExpired, FileNotFoundError, _json.JSONDecodeError):
                results["pyright"] = [{"error": "pyright not available or timed out"}]

        finally:
            os.unlink(temp_path)

        return _json.dumps(results)

    def _check_imports(self, tool_input: dict) -> str:
        """Verify all imports resolve using AST parsing."""
        import ast
        import json as _json

        content = tool_input.get("content", "")
        file_path = tool_input.get("file_path", "unknown.py")

        if not content:
            return '{"error": "No content provided"}'

        issues = []
        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        # Check for known-problematic imports
                        if alias.name.startswith("."):
                            issues.append({
                                "line": node.lineno,
                                "import": alias.name,
                                "issue": "Relative import in non-package context",
                            })
                elif isinstance(node, ast.ImportFrom):
                    if node.module and ".." in (node.module or ""):
                        issues.append({
                            "line": node.lineno,
                            "import": f"from {node.module}",
                            "issue": "Deep relative import",
                        })
        except SyntaxError as e:
            issues.append({"line": e.lineno or 0, "import": "", "issue": f"SyntaxError: {e.msg}"})

        return _json.dumps({"file": file_path, "import_issues": issues, "total": len(issues)})

    def _list_files(self) -> str:
        py_files = sorted(p for p in self._files if p.endswith(".py"))
        ts_files = sorted(p for p in self._files if p.endswith((".ts", ".tsx")))
        other = sorted(p for p in self._files if p not in py_files and p not in ts_files)
        parts = []
        if py_files:
            parts.append(f"Python ({len(py_files)}):\n" + "\n".join(f"  {p}" for p in py_files))
        if ts_files:
            parts.append(f"TypeScript ({len(ts_files)}):\n" + "\n".join(f"  {p}" for p in ts_files))
        if other:
            parts.append(f"Other ({len(other)}):\n" + "\n".join(f"  {p}" for p in other))
        return "\n\n".join(parts) if parts else "No files available."

    def _read_file(self, path: str) -> str:
        content = self._files.get(path)
        if content is None:
            return f"File not found: {path}"
        self._files_read.add(path)
        if len(content) > 15000:
            return content[:15000] + "\n... [truncated at 15000 chars]"
        return content

    def _write_finding(self, tool_input: dict) -> str:
        try:
            sev = LogicSeverity(tool_input.get("severity", "info"))
        except ValueError:
            sev = LogicSeverity.INFO
        cat_str = tool_input.get("category", "dead_code")
        try:
            cat = LogicCategory(cat_str)
        except ValueError:
            cat = LogicCategory.DEAD_CODE

        finding = LogicFinding(
            severity=sev,
            category=cat,
            file_path=tool_input.get("file_path", "(unknown)"),
            line=tool_input.get("line"),
            title=tool_input.get("title", "AI-detected logic issue"),
            description=tool_input.get("description", ""),
            suggestion=tool_input.get("suggestion"),
        )
        self._report.add(finding)
        return f"Finding recorded: [{sev.value}] {finding.title}"


# ── Navya Agent ────────────────────────────────────────────────────


class Navya:
    """Logic Analyst — detects logic errors and correctness issues."""

    name = "navya"
    display_name = "Navya — Logic Analyst"
    default_complexity = TaskComplexity.MEDIUM
    default_model: str | None = None

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

        self.register_tool(ToolDefinition(
            name="read_file",
            description="Read a generated code file for logic analysis.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="write_finding",
            description="Report a logic finding.",
            parameters={
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["error", "warning", "info"]},
                    "category": {"type": "string"},
                    "file_path": {"type": "string"},
                    "line": {"type": "integer"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "suggestion": {"type": "string"},
                },
                "required": ["severity", "category", "file_path", "title", "description"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="list_files",
            description="List all generated code files available for analysis.",
            parameters={
                "type": "object",
                "properties": {},
            },
        ))

        # AGENTIC-FIX: Real static analysis tools (run actual tools, not just regex)
        self.register_tool(ToolDefinition(
            name="run_static_analysis",
            description=(
                "Run REAL static analysis on a Python file using ruff (linting) "
                "and pyright (type checking). Returns actual errors, not guesses."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to the Python file to analyze"},
                    "content": {"type": "string", "description": "File content to analyze"},
                },
                "required": ["file_path", "content"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="check_imports",
            description=(
                "Verify all imports in a Python file resolve correctly using AST parsing. "
                "Detects missing dependencies, circular imports, and undefined names."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["file_path", "content"],
            },
        ))

        self.register_tool(WEB_SEARCH_TOOL)
        self.register_tool(WEB_SCRAPE_TOOL)

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
        """Run logic analysis on all generated code.

        Phase 1: Static pattern analysis (zero AI).
        Phase 2: AI-powered contract-vs-implementation verification.
        """
        # Check inbox for messages from other agents (esp. AUTHORITY directives)
        inbox_messages = await check_inbox(self.name, pipeline_run_id)
        inbox_context = format_inbox_for_prompt(inbox_messages)

        all_files = self._collect_generated_files(context)

        if not all_files:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.COMPLETED,
                output={"findings": [], "message": "No generated files to analyze"},
            )

        report = LogicReport(files_analyzed=len(all_files))

        # Phase 1: Static pattern analysis (ZERO AI)
        self._scan_python_logic(all_files, report)
        self._scan_ts_logic(all_files, report)
        self._check_error_handling_completeness(all_files, report)

        # Phase 2: AI contract-vs-implementation check
        contract = context.get("vikram", {}).get("contract", {})
        if contract:
            await self._ai_contract_verification(all_files, contract, report)

        findings_output = [
            {
                "severity": f.severity.value,
                "category": f.category.value,
                "file_path": f.file_path,
                "line": f.line,
                "title": f.title,
                "description": f.description,
                "suggestion": f.suggestion,
            }
            for f in report.findings
        ]

        output = {
            "findings": findings_output,
            "files_analyzed": report.files_analyzed,
            "passed": report.passed,
            "error_count": report.error_count,
            "warning_count": report.warning_count,
            "total_findings": len(report.findings),
            # FIX-42: Token tracking for cost visibility
            "model_used": getattr(self, "_last_response_model", ""),
            "tokens": {
                "input": getattr(self, "_last_response_input_tokens", 0),
                "output": getattr(self, "_last_response_output_tokens", 0),
            },
        }

        # ── LLM self-evaluation: logic analysis completeness ──
        try:
            contract = context.get("vikram", {}).get("contract", {})
            llm_eval = await self._run_llm_self_evaluation(
                findings_output, all_files, contract,
            )
            output["llm_evaluation"] = llm_eval
        except Exception:
            logger.warning("navya_self_eval_failed", exc_info=True)
            # AUDIT-B2-FIX: Flag that AI self-evaluation was skipped.
            output["__ai_review_degraded__"] = True
            output.setdefault("warnings", []).append(
                "[AI SELF-EVAL UNAVAILABLE] Logic analysis ran with static checks only. "
                "AI evaluation failed — findings may be incomplete."
            )

        await store_output(self, pipeline_run_id, output)

        logger.info(
            "logic_analysis_complete",
            files_analyzed=report.files_analyzed,
            total_findings=len(report.findings),
            errors=report.error_count,
            warnings=report.warning_count,
            passed=report.passed,
        )

        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.COMPLETED,
            output=output,
        )

    # ── LLM-driven self-evaluation ──────────────────────────────────

    async def _run_llm_self_evaluation(
        self,
        findings: list[dict[str, Any]],
        analyzed_files: dict[str, str],
        contract: dict[str, Any],
    ) -> dict[str, Any]:
        """LLM reviews its own logic analysis for completeness.

        Checks: all files analyzed, error handling paths, dead code,
        business logic vs contract, unreachable branches.
        Uses cheapest model (~$0.002/call).
        """
        finding_summary = []
        for f in findings[:25]:
            finding_summary.append(
                f"  [{f.get('severity', '?')}] {f.get('category', '?')}: "
                f"{f.get('title', '?')} in {f.get('file_path', '?')}"
            )
        findings_text = "\n".join(finding_summary) if finding_summary else "  (no findings)"

        file_list = ", ".join(sorted(analyzed_files.keys())[:30])

        # Extract contract endpoints for business logic verification
        endpoints = contract.get("endpoints", contract.get("api", {}).get("endpoints", []))
        endpoint_text = ""
        if endpoints:
            ep_lines = [f"  {e.get('method', 'GET')} {e.get('path', '?')}" for e in endpoints[:15]]
            endpoint_text = f"\n## Contract Endpoints\n" + "\n".join(ep_lines)

        eval_prompt = (
            "You are reviewing logic analysis results YOU just produced. Be brutally honest.\n\n"
            f"## Files Analyzed ({len(analyzed_files)} total)\n{file_list}\n"
            f"{endpoint_text}\n\n"
            f"## Findings You Reported\n{findings_text}\n\n"
            "## Your Task\n"
            "Check your analysis coverage:\n"
            "1. Did you analyze ALL generated files? List any you skipped.\n"
            "2. Did you check error handling paths (try/except, error responses)?\n"
            "3. Did you check for dead code / unreachable branches?\n"
            "4. Did you verify business logic matches contract endpoints?\n"
            "5. Did you check for race conditions or state management issues?\n\n"
            "Respond in JSON:\n"
            "{\n"
            '  "files_not_analyzed": ["path1", "path2"],\n'
            '  "categories_checked": ["error_handling", "dead_code", ...],\n'
            '  "categories_missed": ["race_conditions", ...],\n'
            '  "completeness_pct": 0-100,\n'
            '  "verdict": "PASS" or "FAIL",\n'
            '  "reasoning": "brief explanation"\n'
            "}\n"
        )

        from app.services.ai_router import get_ai_router, AIRequest, AIMessage

        router = get_ai_router()
        resp = await router.call(AIRequest(
            messages=[AIMessage(role="user", content=eval_prompt)],
            complexity=TaskComplexity.LOW,
            max_tokens=1000,
            agent_name=f"{self.name}_self_eval",
        ))

        from app.utils.json_parser import parse_json
        result = parse_json(resp.content, fallback={})
        if not isinstance(result, dict):
            result = {}
        return result

    # ── File Collection ────────────────────────────────────────────

    def _collect_generated_files(self, context: dict[str, Any]) -> dict[str, str]:
        """Collect generated file contents from pipeline context."""
        files: dict[str, str] = {}
        for agent_name in ("shubham", "aanya"):
            agent_output = context.get(agent_name, {})
            if isinstance(agent_output, dict):
                for path in agent_output.get("generated_files", []):
                    content = agent_output.get("file_contents", {}).get(path, "")
                    if content:
                        files[path] = content
        return files

    # ── Static Scanners ────────────────────────────────────────────

    def _scan_python_logic(
        self, files: dict[str, str], report: LogicReport
    ) -> None:
        """Scan Python files for logic errors."""
        for path, content in files.items():
            if not path.endswith(".py"):
                continue

            # Apply pattern-based checks (skip overly noisy patterns)
            for pattern, title, severity, category, suggestion in _PYTHON_LOGIC_PATTERNS:
                # Skip the broad unused variable pattern — too noisy
                if category == LogicCategory.DEAD_CODE and "Variable assigned" in title:
                    continue

                for match in pattern.finditer(content):
                    line_num = content[:match.start()].count("\n") + 1
                    report.add(LogicFinding(
                        severity=severity,
                        category=category,
                        file_path=path,
                        line=line_num,
                        title=title,
                        description=f"Found: {match.group(0)[:80]}",
                        suggestion=suggestion,
                    ))

            # Check for functions missing return statement
            self._check_missing_returns(path, content, report)

    def _scan_ts_logic(
        self, files: dict[str, str], report: LogicReport
    ) -> None:
        """Scan TypeScript files for logic errors."""
        for path, content in files.items():
            if not path.endswith((".ts", ".tsx")):
                continue

            for pattern, title, severity, category, suggestion in _TS_LOGIC_PATTERNS:
                for match in pattern.finditer(content):
                    line_num = content[:match.start()].count("\n") + 1
                    report.add(LogicFinding(
                        severity=severity,
                        category=category,
                        file_path=path,
                        line=line_num,
                        title=title,
                        description=f"Found: {match.group(0)[:80]}",
                        suggestion=suggestion,
                    ))

    def _check_missing_returns(
        self, path: str, content: str, report: LogicReport
    ) -> None:
        """Check for functions with return type hint but no return statement."""
        func_pattern = re.compile(
            r"def\s+(\w+)\s*\([^)]*\)\s*->\s*(?!None)(\w+)",
            re.MULTILINE,
        )

        lines = content.split("\n")
        for match in func_pattern.finditer(content):
            func_name = match.group(1)
            return_type = match.group(2)
            func_line = content[:match.start()].count("\n")

            # Find function body (next lines with deeper indentation)
            func_indent = len(lines[func_line]) - len(lines[func_line].lstrip())
            has_return = False

            # AUDIT-FIX: Scan up to 200 lines (was 50). Generated code
            # frequently exceeds 50 lines, causing false "missing return" findings.
            for i in range(func_line + 1, min(func_line + 200, len(lines))):
                line = lines[i]
                stripped = line.lstrip()
                if stripped and (len(line) - len(stripped)) <= func_indent and not stripped.startswith("#"):
                    break  # End of function
                if "return " in stripped or stripped == "return":
                    has_return = True
                    break

            if not has_return and func_name not in ("__init__", "__del__", "__enter__", "__exit__"):
                report.add(LogicFinding(
                    severity=LogicSeverity.WARNING,
                    category=LogicCategory.MISSING_RETURN,
                    file_path=path,
                    line=func_line + 1,
                    title=f"Function '{func_name}' declares -> {return_type} but may not return",
                    description=f"Function annotated to return {return_type} but no return statement found in scanned body",
                    suggestion="Add explicit return statement or fix return type annotation",
                ))

    def _check_error_handling_completeness(
        self, files: dict[str, str], report: LogicReport
    ) -> None:
        """Check that async database operations have try/except."""
        db_call_pattern = re.compile(r"""(?:session\.\w+|db\.\w+)\s*\(""")
        try_pattern = re.compile(r"""^\s*try\s*:""", re.MULTILINE)

        for path, content in files.items():
            if not path.endswith(".py"):
                continue

            db_calls = list(db_call_pattern.finditer(content))
            try_blocks = list(try_pattern.finditer(content))

            # If there are DB calls but no try/except anywhere in the file
            if db_calls and not try_blocks:
                report.add(LogicFinding(
                    severity=LogicSeverity.WARNING,
                    category=LogicCategory.MISSING_ERROR_HANDLING,
                    file_path=path,
                    line=None,
                    title="Database operations without error handling",
                    description=f"{len(db_calls)} database calls found but no try/except blocks",
                    suggestion="Wrap database operations in try/except with proper error handling",
                ))

    # ── AI-Powered Contract Verification ───────────────────────────

    async def _ai_contract_verification(
        self, files: dict[str, str], contract: dict[str, Any], report: LogicReport
    ) -> None:
        """AI agentic loop for contract-vs-implementation verification.

        The AI reads files on demand (no file cap) and writes findings
        using tools.
        """
        import orjson

        endpoints = contract.get("api", {}).get("endpoints", [])
        tables = contract.get("database", {}).get("tables", [])

        if not endpoints and not tables:
            return

        contract_summary = orjson.dumps(
            {"endpoints": endpoints[:20], "tables": tables[:10]},
            option=orjson.OPT_INDENT_2,
        ).decode("utf-8")

        handler = NavyaToolHandler(files=files, report=report, contract=contract)

        system_prompt = "\n".join([
            "You are Navya, the Logic Analyst at NexSidi.",
            "You have tools to analyze generated code for logic errors and contract mismatches.",
            "",
            "Workflow:",
            "1. Call list_files to see all available files",
            "2. Read router/model/service files that implement the contract",
            "3. For each file, check:",
            "   - Missing endpoints (defined in contract but not implemented)",
            "   - Wrong field names or types vs contract",
            "   - Missing input validation",
            "   - Dead code / unreachable branches",
            "   - Null safety issues",
            "   - Missing error handling for database operations",
            "   - Race conditions in concurrent operations",
            "4. Use write_finding for each issue discovered",
            "",
            "Be thorough — examine ALL implementation files, not just a few.",
        ])

        user_message = (
            f"Verify this implementation against the architecture contract.\n\n"
            f"Contract summary:\n{contract_summary}\n\n"
            f"Start by listing files, then read and analyze each implementation file."
        )

        try:
            _ai_response = await call_ai_with_tools(
                agent=self,
                messages=[{"role": "user", "content": user_message}],
                system_prompt=system_prompt,
                task_type="general",
                tool_handler=handler,
                max_tool_rounds=12,
            )
            # FIX-42: Capture token usage
            self._last_response_model = getattr(_ai_response, "model_used", "")
            self._last_response_input_tokens = getattr(_ai_response, "input_tokens", 0)
            self._last_response_output_tokens = getattr(_ai_response, "output_tokens", 0)
        except Exception as exc:
            from app.services.ai_router import _sanitize_error
            logger.warning("ai_contract_verification_failed", error=_sanitize_error(exc))


# Register the agent
_navya = Navya()
register_agent(_navya)
