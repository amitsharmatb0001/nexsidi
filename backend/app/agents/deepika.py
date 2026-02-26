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
    BaseAgent,
    ToolDefinition,
    register_agent,
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
        re.compile(r"""\.all\(\)(?!.*limit|.*offset|.*paginate)"""),
        "Unbounded .all() query — no LIMIT clause",
        PerfSeverity.HIGH,
        PerfCategory.UNBOUNDED_QUERY,
        "Returns ALL rows — catastrophic for large tables",
        "Add .limit() and .offset() for pagination",
    ),
    # Sync I/O in async context
    (
        re.compile(r"""async\s+def\s+\w+.*:\s*\n(?:.*\n)*?.*(?:open\(|os\.path|requests\.(?:get|post)|time\.sleep)""", re.MULTILINE),
        "Synchronous I/O call inside async function",
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


# ── Deepika Agent ──────────────────────────────────────────────────


class Deepika(BaseAgent):
    """Performance Analyst — detects performance issues and anti-patterns."""

    name = "deepika"
    display_name = "Deepika — Performance Analyst"
    default_complexity = TaskComplexity.MEDIUM

    def __init__(self) -> None:
        super().__init__()

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
        try:
            ai_findings = await self._ai_perf_analysis(all_files, context)
            for finding in ai_findings:
                report.add(finding)
        except Exception as exc:
            logger.warning("ai_perf_analysis_failed", error=str(exc))

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
        }

        await self.store_output(pipeline_run_id, output)

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
            indexes = {idx.get("column", "") for idx in table.get("indexes", [])}

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
        self, files: dict[str, str], context: dict[str, Any]
    ) -> list[PerfFinding]:
        """Use AI to find complex performance issues."""
        import orjson

        # Focus on backend service/router files where perf matters most
        critical_files: list[str] = []
        for path, content in files.items():
            if path.endswith(".py") and ("router" in path or "service" in path):
                truncated = content[:2500]
                critical_files.append(f"### {path}\n```python\n{truncated}\n```")

        if not critical_files:
            return []

        system_prompt = "\n".join([
            "You are Deepika, the Performance Analyst at NexSidi.",
            "Analyze the code for performance issues: N+1 queries, missing indexes,",
            "unbounded queries, sync-in-async, memory leaks, algorithm complexity.",
            "",
            "Respond with a JSON array of findings:",
            '{"severity": "critical|high|medium|low", "category": "...",',
            ' "file_path": "...", "title": "...", "description": "...",',
            ' "impact": "...", "suggestion": "..."}',
            "",
            "If no issues found, respond with: []",
            "Output ONLY valid JSON.",
        ])

        response = await self.call_ai(
            messages=[{"role": "user", "content": f"Analyze:\n\n{''.join(critical_files[:5])}"}],
            system_prompt=system_prompt,
            task_type="general",
            temperature=0.1,
        )

        findings: list[PerfFinding] = []
        try:
            raw = response.content.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
            parsed = orjson.loads(raw.encode("utf-8"))

            if isinstance(parsed, list):
                for item in parsed:
                    sev = PerfSeverity(item.get("severity", "low"))
                    cat_str = item.get("category", "inefficient_algorithm")
                    try:
                        cat = PerfCategory(cat_str)
                    except ValueError:
                        cat = PerfCategory.INEFFICIENT_ALGORITHM

                    findings.append(PerfFinding(
                        severity=sev,
                        category=cat,
                        file_path=item.get("file_path", "(unknown)"),
                        line=item.get("line"),
                        title=item.get("title", "AI-detected perf issue"),
                        description=item.get("description", ""),
                        impact=item.get("impact", "Performance degradation"),
                        suggestion=item.get("suggestion", "Review and optimize"),
                    ))
        except Exception as exc:
            logger.warning("ai_perf_parse_failed", error=str(exc))

        return findings


# Register the agent
_deepika = Deepika()
register_agent(_deepika)
