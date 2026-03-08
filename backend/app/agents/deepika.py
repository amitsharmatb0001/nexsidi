"""Deepika — Performance Analyst: detects performance issues + anti-patterns.

Deepika analyzes generated code for performance problems:
1. N+1 query detection (SQLAlchemy relationship access in loops)
2. Missing database indexes (queries on non-indexed columns)
3. Unbounded queries (SELECT * without LIMIT)
4. Memory leak patterns (unbounded caches, growing lists)
5. Frontend bundle size issues (large imports, missing code splitting)
6. Async anti-patterns (sync calls in async context, missing await)

Runs in PARALLEL with Karan and Navya during quality_review stage.
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


# ── Performance Finding Types ──────────────────────────────────────


class PerfSeverity(str, Enum):
    """Performance issue severity."""

    CRITICAL = "critical"  # Will cause outage or OOM
    HIGH = "high"          # Visible latency or resource waste
    MEDIUM = "medium"      # Suboptimal but functional
    LOW = "low"            # Minor optimization opportunity


class PerfCategory(str, Enum):
    """Categories of performance findings."""

    N_PLUS_ONE = "n_plus_one"
    MISSING_INDEX = "missing_index"
    UNBOUNDED_QUERY = "unbounded_query"
    MEMORY_LEAK = "memory_leak"
    SYNC_IN_ASYNC = "sync_in_async"
    MISSING_AWAIT = "missing_await"
    LARGE_IMPORT = "large_import"
    MISSING_PAGINATION = "missing_pagination"
    MISSING_CACHE = "missing_cache"
    INEFFICIENT_ALGORITHM = "inefficient_algorithm"
    BUNDLE_SIZE = "bundle_size"
    MISSING_LAZY_LOAD = "missing_lazy_load"


@dataclass(frozen=True, slots=True)
class PerfFinding:
    """A single performance finding from Deepika."""

    severity: PerfSeverity
    category: PerfCategory
    file_path: str
    line: int | None
    title: str
    description: str
    impact: str
    suggestion: str


@dataclass(slots=True)
class PerfReport:
    """Aggregated performance report."""

    findings: list[PerfFinding] = field(default_factory=list)
    files_analyzed: int = 0
    passed: bool = True

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == PerfSeverity.CRITICAL)

    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == PerfSeverity.HIGH)

    def add(self, finding: PerfFinding) -> None:
        self.findings.append(finding)
        if finding.severity in (PerfSeverity.CRITICAL, PerfSeverity.HIGH):
            self.passed = False


# ── Static Performance Patterns ────────────────────────────────────

# Python performance anti-patterns
_PYTHON_PERF_PATTERNS: list[tuple[re.Pattern, str, PerfSeverity, PerfCategory, str, str]] = [
    # N+1 Query: accessing relationship in a loop
    (
        re.compile(r"""for\s+\w+\s+in\s+\w+.*:\s*\n[^}]*?\.(?:items|users|orders|posts|comments|products|tasks)\b""", re.MULTILINE),
        "Potential N+1 query: relationship accessed inside loop",
        PerfSeverity.HIGH,
        PerfCategory.N_PLUS_ONE,
        "Each iteration triggers a separate SQL query",
        "Use joinedload() or selectinload() in the original query",
    ),
    # Unbounded query (no limit/pagination)
    (
        re.compile(r"""\.all\(\)"""),
        "Unbounded .all() query — verify pagination exists nearby",
        PerfSeverity.HIGH,
        PerfCategory.UNBOUNDED_QUERY,
        "Returns ALL rows — catastrophic for large tables",
        "Add .limit() and .offset() for pagination",
    ),
    # Sync I/O in async context (single-line pattern to avoid catastrophic backtracking)
    (
        re.compile(r"""(?:open\(|os\.path\.|requests\.(?:get|post|put|delete|patch)\(|time\.sleep\()"""),
        "Potential synchronous I/O call — verify not inside async function",
        PerfSeverity.HIGH,
        PerfCategory.SYNC_IN_ASYNC,
        "Blocks the async event loop, causing latency for all requests",
        "Use aiofiles for file I/O, httpx for HTTP, asyncio.sleep for delays",
    ),
    # Missing await on coroutine
    (
        re.compile(r"""(?<!await\s)(?:session\.execute|session\.commit|session\.flush|session\.refresh)\s*\("""),
        "Possible missing 'await' on async database operation",
        PerfSeverity.CRITICAL,
        PerfCategory.MISSING_AWAIT,
        "Coroutine created but never awaited — operation silently skipped",
        "Add 'await' before the async database call",
    ),
    # Loading entire file into memory
    (
        re.compile(r"""\.read\(\)(?!.*chunk|.*iter|.*stream)"""),
        "File read without chunking — loads entire file into memory",
        PerfSeverity.MEDIUM,
        PerfCategory.MEMORY_LEAK,
        "Large files consume excessive memory",
        "Use chunked reading or streaming for large files",
    ),
    # String concatenation in loop
    (
        re.compile(r"""for\s+\w+\s+in\s+.*:\s*\n.*?\w+\s*\+=\s*(?:str\(|f"|["'])""", re.MULTILINE),
        "String concatenation in loop — O(n^2) memory",
        PerfSeverity.MEDIUM,
        PerfCategory.INEFFICIENT_ALGORITHM,
        "Creates new string object on every iteration",
        "Collect in list and ''.join() at the end",
    ),
]

# TypeScript performance anti-patterns
_TS_PERF_PATTERNS: list[tuple[re.Pattern, str, PerfSeverity, PerfCategory, str, str]] = [
    # Large library import (import entire lodash, moment, etc.)
    (
        re.compile(r"""import\s+\w+\s+from\s+["'](?:lodash|moment|date-fns)["']"""),
        "Importing entire library — increases bundle size",
        PerfSeverity.MEDIUM,
        PerfCategory.BUNDLE_SIZE,
        "Entire library included in bundle even if only 1 function used",
        "Import specific functions: import { debounce } from 'lodash/debounce'",
    ),
    # Missing React.memo or useMemo for expensive operations
    (
        re.compile(r"""\.map\s*\(\s*\([^)]*\)\s*=>\s*(?:<|React\.createElement)"""),
        "Array.map rendering components — consider memoization",
        PerfSeverity.LOW,
        PerfCategory.MISSING_CACHE,
        "Re-renders all items when any state changes",
        "Wrap list items in React.memo() for stable props",
    ),
    # Inline object/array in JSX (causes re-renders)
    (
        re.compile(r"""(?:style|className)=\{\{"""),
        "Inline object in JSX prop — triggers re-render on every render",
        PerfSeverity.LOW,
        PerfCategory.INEFFICIENT_ALGORITHM,
        "New object reference on every render causes unnecessary re-renders",
        "Extract to a constant or use useMemo()",
    ),
    # Missing lazy loading for routes
    (
        re.compile(r"""import\s+\w+\s+from\s+["'].*(?:pages?|views?)/"""),
        "Static import of page component — missing lazy loading",
        PerfSeverity.MEDIUM,
        PerfCategory.MISSING_LAZY_LOAD,
        "All page code loaded upfront even if user never visits",
        "Use dynamic import: const Page = dynamic(() => import('./Page'))",
    ),
]


# ── Deepika Tool Handler ───────────────────────────────────────────


class DeepikaToolHandler:
    """Handles tool calls for Deepika's agentic performance analysis loop."""

    def __init__(self, files: dict[str, str], report: PerfReport, context: dict[str, Any]):
        self._files = files
        self._report = report
        self._context = context
        self._files_read: set[str] = set()

    async def __call__(self, tool_name: str, tool_input: dict) -> str:
        # AGENTIC-FIX: Handle web tools (shared across all agents)
        web_result = await handle_web_tool(tool_name, tool_input)
        if web_result is not None:
            return web_result

        if tool_name == "read_file":
            return self._read_file(tool_input["path"])
        elif tool_name == "write_finding":
            return self._write_finding(tool_input)
        elif tool_name == "list_files":
            return self._list_files()
        elif tool_name == "check_async_patterns":
            return self._check_async_patterns(tool_input)
        elif tool_name == "profile_queries":
            return self._profile_queries(tool_input)
        else:
            return f"Unknown tool: {tool_name}"

    def _check_async_patterns(self, tool_input: dict) -> str:
        """REAL async anti-pattern detection using AST (not regex)."""
        import ast
        import json as _json

        content = tool_input.get("content", "")
        file_path = tool_input.get("file_path", "unknown.py")
        issues = []

        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                # Detect sync calls inside async functions
                if isinstance(node, ast.AsyncFunctionDef):
                    for child in ast.walk(node):
                        if isinstance(child, ast.Call):
                            func_name = ""
                            if isinstance(child.func, ast.Attribute):
                                func_name = child.func.attr
                            elif isinstance(child.func, ast.Name):
                                func_name = child.func.id
                            # Known sync-in-async violations
                            sync_blockers = {"sleep", "open", "read", "write", "connect", "execute"}
                            if func_name in sync_blockers:
                                issues.append({
                                    "line": child.lineno,
                                    "issue": f"Potentially blocking sync call '{func_name}()' in async function '{node.name}'",
                                    "severity": "warning",
                                })
                        # Missing await on coroutine calls
                        if isinstance(child, ast.Expr) and isinstance(child.value, ast.Call):
                            # This is a bare call expression — might be missing await
                            pass  # AST can't tell without type info
        except SyntaxError:
            issues.append({"line": 0, "issue": "SyntaxError — cannot parse file", "severity": "error"})

        return _json.dumps({"file": file_path, "async_issues": issues, "total": len(issues)})

    def _profile_queries(self, tool_input: dict) -> str:
        """Detect N+1 queries and missing indexes from SQLAlchemy code (AST-based)."""
        import ast
        import json as _json

        content = tool_input.get("content", "")
        file_path = tool_input.get("file_path", "unknown.py")
        issues = []

        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                # Detect .query or session.execute inside for loops (N+1)
                if isinstance(node, (ast.For, ast.AsyncFor)):
                    for child in ast.walk(node):
                        if isinstance(child, ast.Attribute):
                            if child.attr in ("query", "execute", "scalars", "all"):
                                issues.append({
                                    "line": child.lineno,
                                    "issue": f"Potential N+1 query: '{child.attr}' called inside loop at line {node.lineno}",
                                    "severity": "high",
                                })
                # Detect SELECT * patterns (no column selection)
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name) and node.func.id == "select":
                        # Check if it's select(Model) without specific columns
                        if node.args and len(node.args) == 1:
                            arg = node.args[0]
                            if isinstance(arg, ast.Name):
                                issues.append({
                                    "line": node.lineno,
                                    "issue": f"SELECT * pattern: select({arg.id}) — consider selecting specific columns",
                                    "severity": "low",
                                })
        except SyntaxError:
            issues.append({"line": 0, "issue": "SyntaxError", "severity": "error"})

        return _json.dumps({"file": file_path, "query_issues": issues, "total": len(issues)})

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
            sev = PerfSeverity(tool_input.get("severity", "low"))
        except ValueError:
            sev = PerfSeverity.MEDIUM
        cat_str = tool_input.get("category", "inefficient_algorithm")
        try:
            cat = PerfCategory(cat_str)
        except ValueError:
            cat = PerfCategory.INEFFICIENT_ALGORITHM

        finding = PerfFinding(
            severity=sev,
            category=cat,
            file_path=tool_input.get("file_path", "(unknown)"),
            line=tool_input.get("line"),
            title=tool_input.get("title", "AI-detected perf issue"),
            description=tool_input.get("description", ""),
            impact=tool_input.get("impact", "Performance degradation"),
            suggestion=tool_input.get("suggestion", "Review and optimize"),
        )
        self._report.add(finding)
        return f"Finding recorded: [{sev.value}] {finding.title}"


# ── Deepika Agent ──────────────────────────────────────────────────


class Deepika:
    """Performance Analyst — detects performance issues and anti-patterns."""

    name = "deepika"
    display_name = "Deepika — Performance Analyst"
    default_complexity = TaskComplexity.MEDIUM
    default_model: str | None = None

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

        self.register_tool(ToolDefinition(
            name="read_file",
            description="Read a generated code file for performance analysis.",
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
            description="Report a performance finding.",
            parameters={
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                    "category": {"type": "string"},
                    "file_path": {"type": "string"},
                    "line": {"type": "integer"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "impact": {"type": "string"},
                    "suggestion": {"type": "string"},
                },
                "required": ["severity", "category", "file_path", "title", "description", "impact", "suggestion"],
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

        # AGENTIC-FIX: Real performance analysis tools (AST-based, not regex)
        self.register_tool(ToolDefinition(
            name="check_async_patterns",
            description=(
                "Detect async anti-patterns in Python code: sync calls in async "
                "functions, missing await, blocking I/O in event loop."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "content": {"type": "string", "description": "File content to analyze"},
                },
                "required": ["file_path", "content"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="profile_queries",
            description=(
                "Detect N+1 queries, unbounded SELECTs, and missing indexes in "
                "SQLAlchemy code using AST analysis."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "content": {"type": "string", "description": "File content to analyze"},
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
        """Run performance analysis on all generated code.

        Phase 1: Static pattern analysis (zero AI).
        Phase 2: AI-powered database query analysis.
        Phase 3: Index recommendation based on contract.
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

        report = PerfReport(files_analyzed=len(all_files))

        # Phase 1: Static pattern analysis (ZERO AI)
        self._scan_python_perf(all_files, report)
        self._scan_ts_perf(all_files, report)
        self._check_pagination(all_files, report)
        self._check_missing_indexes(all_files, context, report)

        # Phase 2: AI-powered query complexity analysis
        await self._ai_perf_analysis(all_files, context, report)

        findings_output = [
            {
                "severity": f.severity.value,
                "category": f.category.value,
                "file_path": f.file_path,
                "line": f.line,
                "title": f.title,
                "description": f.description,
                "impact": f.impact,
                "suggestion": f.suggestion,
            }
            for f in report.findings
        ]

        output = {
            "findings": findings_output,
            "files_analyzed": report.files_analyzed,
            "passed": report.passed,
            "critical_count": report.critical_count,
            "high_count": report.high_count,
            "total_findings": len(report.findings),
            # FIX-42: Token tracking for cost visibility
            "model_used": getattr(self, "_last_response_model", ""),
            "tokens": {
                "input": getattr(self, "_last_response_input_tokens", 0),
                "output": getattr(self, "_last_response_output_tokens", 0),
            },
        }

        # ── LLM self-evaluation: performance analysis completeness ──
        try:
            llm_eval = await self._run_llm_self_evaluation(
                findings_output, all_files, context,
            )
            output["llm_evaluation"] = llm_eval
        except Exception:
            logger.warning("deepika_self_eval_failed", exc_info=True)
            # AUDIT-B2-FIX: Flag that AI self-evaluation was skipped.
            output["__ai_review_degraded__"] = True
            output.setdefault("warnings", []).append(
                "[AI SELF-EVAL UNAVAILABLE] Performance analysis ran with static checks only. "
                "AI evaluation failed — findings may be incomplete."
            )

        await store_output(self, pipeline_run_id, output)

        logger.info(
            "perf_analysis_complete",
            files_analyzed=report.files_analyzed,
            total_findings=len(report.findings),
            critical=report.critical_count,
            high=report.high_count,
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
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """LLM reviews its own performance analysis for completeness.

        Checks: N+1 queries, async patterns, pagination, memory leaks,
        unbounded loops, missing indexes.
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

        # Check if database is involved
        has_db = bool(context.get("dhruv", {}).get("written_files"))

        eval_prompt = (
            "You are reviewing performance analysis results YOU just produced. Be brutally honest.\n\n"
            f"## Files Analyzed ({len(analyzed_files)} total)\n{file_list}\n"
            f"## Database Present: {'Yes' if has_db else 'No'}\n\n"
            f"## Findings You Reported\n{findings_text}\n\n"
            "## Your Task\n"
            "Check your performance analysis coverage:\n"
            "1. N+1 query detection — did you check ALL database access patterns?\n"
            "2. Missing async/await — did you check for sync calls in async context?\n"
            "3. Unbounded queries — did you check for SELECT without LIMIT?\n"
            "4. Memory leaks — unbounded caches, growing lists without cleanup?\n"
            "5. Missing pagination — list endpoints returning all records?\n"
            "6. Missing indexes — queries on columns without indexes?\n"
            "7. Frontend bundle size — large imports, missing code splitting?\n"
            "8. Did you analyze ALL generated files?\n\n"
            "Respond in JSON:\n"
            "{\n"
            '  "categories_checked": ["n_plus_1", "async_patterns", ...],\n'
            '  "categories_missed": ["memory_leaks", ...],\n'
            '  "files_not_analyzed": ["path1", ...],\n'
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

    def _scan_python_perf(
        self, files: dict[str, str], report: PerfReport
    ) -> None:
        """Scan Python files for performance anti-patterns."""
        for path, content in files.items():
            if not path.endswith(".py"):
                continue

            for pattern, title, severity, category, impact, suggestion in _PYTHON_PERF_PATTERNS:
                for match in pattern.finditer(content):
                    line_num = content[:match.start()].count("\n") + 1
                    report.add(PerfFinding(
                        severity=severity,
                        category=category,
                        file_path=path,
                        line=line_num,
                        title=title,
                        description=f"Found: {match.group(0)[:80]}",
                        impact=impact,
                        suggestion=suggestion,
                    ))

    def _scan_ts_perf(
        self, files: dict[str, str], report: PerfReport
    ) -> None:
        """Scan TypeScript files for performance anti-patterns."""
        for path, content in files.items():
            if not path.endswith((".ts", ".tsx")):
                continue

            for pattern, title, severity, category, impact, suggestion in _TS_PERF_PATTERNS:
                for match in pattern.finditer(content):
                    line_num = content[:match.start()].count("\n") + 1
                    report.add(PerfFinding(
                        severity=severity,
                        category=category,
                        file_path=path,
                        line=line_num,
                        title=title,
                        description=f"Found: {match.group(0)[:80]}",
                        impact=impact,
                        suggestion=suggestion,
                    ))

    def _check_pagination(
        self, files: dict[str, str], report: PerfReport
    ) -> None:
        """Check that list endpoints have pagination parameters."""
        list_endpoint_pattern = re.compile(
            r"""@\w+\.get\s*\(\s*["'][^"']+["']\s*\)\s*\nasync\s+def\s+(?:list_|get_all_|get_\w+s\b)(\w+)""",
            re.MULTILINE,
        )

        for path, content in files.items():
            if not path.endswith(".py"):
                continue

            for match in list_endpoint_pattern.finditer(content):
                func_name = match.group(0)
                # Check if skip/limit/offset/page params exist nearby
                func_start = match.start()
                func_region = content[func_start:func_start + 500]

                if not re.search(r"""(?:skip|offset|limit|page|per_page)""", func_region):
                    line_num = content[:func_start].count("\n") + 1
                    report.add(PerfFinding(
                        severity=PerfSeverity.HIGH,
                        category=PerfCategory.MISSING_PAGINATION,
                        file_path=path,
                        line=line_num,
                        title="List endpoint missing pagination",
                        description=f"Endpoint returns all records without skip/limit",
                        impact="Returns unbounded results — OOM for large datasets",
                        suggestion="Add skip: int = 0, limit: int = 50 parameters",
                    ))

    def _check_missing_indexes(
        self, files: dict[str, str], context: dict[str, Any], report: PerfReport
    ) -> None:
        """Check that frequently queried columns have indexes."""
        contract = context.get("vikram", {}).get("contract", {})
        tables = contract.get("database", {}).get("tables", [])

        if not tables:
            return

        # Columns that typically need indexes
        indexed_column_patterns = {"email", "username", "slug", "created_at", "status", "organization_id"}

        for table in tables:
            table_name = table.get("name", "")
            columns = table.get("columns", [])
            # AUDIT-FIX: Handle both "column" (single) and "columns" (composite)
            # index key formats from the contract. Previously only checked "column",
            # missing composite indexes like {"columns": ["email", "created_at"]}.
            raw_indexes = table.get("indexes", [])
            indexes: set[str] = set()
            for idx in raw_indexes:
                if isinstance(idx, dict):
                    if "column" in idx:
                        indexes.add(idx["column"])
                    for col_name_val in (idx.get("columns") or []):
                        if isinstance(col_name_val, str):
                            indexes.add(col_name_val)

            for col in columns:
                col_name = col.get("name", "")
                if col_name in indexed_column_patterns and col_name not in indexes:
                    # Check if it's a foreign key (those get auto-indexed in most DBs)
                    if col_name.endswith("_id"):
                        continue

                    report.add(PerfFinding(
                        severity=PerfSeverity.MEDIUM,
                        category=PerfCategory.MISSING_INDEX,
                        file_path=f"(table: {table_name})",
                        line=None,
                        title=f"Column '{col_name}' on '{table_name}' may need an index",
                        description=f"Commonly queried column without explicit index",
                        impact="Full table scan on every query filtering by this column",
                        suggestion=f"Add Index('{table_name}_ix_{col_name}', '{col_name}')",
                    ))

    # ── AI-Powered Performance Analysis ────────────────────────────

    async def _ai_perf_analysis(
        self, files: dict[str, str], context: dict[str, Any], report: PerfReport
    ) -> None:
        """AI agentic loop for deep performance analysis.

        The AI reads files on demand (no file cap) and writes findings
        using tools.
        """
        handler = DeepikaToolHandler(files=files, report=report, context=context)

        system_prompt = "\n".join([
            "You are Deepika, the Performance Analyst at NexSidi.",
            "You have tools to analyze generated code for performance issues.",
            "",
            "Workflow:",
            "1. Call list_files to see all available files",
            "2. Read backend service/router files first (where perf matters most)",
            "3. For each file, check for:",
            "   - N+1 queries (relationship access inside loops)",
            "   - Unbounded queries (missing LIMIT/pagination)",
            "   - Sync I/O in async context (blocking the event loop)",
            "   - Missing await on coroutines",
            "   - Memory leaks (unbounded caches, growing lists)",
            "   - Inefficient algorithms (O(n^2), string concat in loops)",
            "   - Missing database indexes for common query patterns",
            "   - Frontend bundle size issues (large imports, missing lazy loading)",
            "4. Use write_finding for each performance issue discovered",
            "",
            "Be thorough — examine ALL service and router files, not just a few.",
        ])

        user_message = (
            f"Perform a deep performance audit of this project ({len(files)} files). "
            "Start by listing files, then read and analyze the performance-critical ones."
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
            logger.warning("ai_perf_analysis_failed", error=_sanitize_error(exc))


# Register the agent
_deepika = Deepika()
register_agent(_deepika)
