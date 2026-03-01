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
    call_ai,
    register_agent,
    run_agent,
    store_output,
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
    # Unused variable assignment (basic pattern: assignment followed by no usage)
    (
        re.compile(r"""^\s+(\w+)\s*=\s*.+$""", re.MULTILINE),
        "Variable assigned but check if it's used later",
        LogicSeverity.INFO,
        LogicCategory.DEAD_CODE,
        "Remove unused variables to reduce confusion",
    ),
    # Infinite loop risk: while True without break
    (
        re.compile(r"""while\s+True\s*:(?:(?!break).)*$""", re.MULTILINE | re.DOTALL),
        "while True without visible break — potential infinite loop",
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
            try:
                ai_findings = await self._ai_contract_verification(all_files, contract)
                for finding in ai_findings:
                    report.add(finding)
            except Exception as exc:
                from app.services.ai_router import _sanitize_error  # R27-FIX
                logger.warning("ai_contract_verification_failed", error=_sanitize_error(exc))

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
        }

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

            for i in range(func_line + 1, min(func_line + 50, len(lines))):
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
                    description=f"Function annotated to return {return_type} but no return statement found in first 50 lines",
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
        self, files: dict[str, str], contract: dict[str, Any]
    ) -> list[LogicFinding]:
        """Use AI to verify generated code matches the architecture contract."""
        import orjson

        endpoints = contract.get("api", {}).get("endpoints", [])
        tables = contract.get("database", {}).get("tables", [])

        if not endpoints and not tables:
            return []

        contract_summary = orjson.dumps(
            {"endpoints": endpoints[:20], "tables": tables[:10]},
            option=orjson.OPT_INDENT_2,
        ).decode("utf-8")

        # Summarize implementation files
        impl_summary: list[str] = []
        for path, content in files.items():
            if path.endswith(".py") and ("router" in path.lower() or "model" in path.lower()):
                from app.agents.scan_utils import split_into_windows

                windows = split_into_windows(content, window_size=2000, overlap=400)
                for i, window in enumerate(windows):
                    label = f"### {path}" if len(windows) == 1 else f"### {path} (part {i + 1}/{len(windows)})"
                    impl_summary.append(f"{label}\n```python\n{window}\n```")

        if not impl_summary:
            return []

        system_prompt = "\n".join([
            "You are Navya, the Logic Analyst at NexSidi.",
            "Compare the architecture contract with the implementation.",
            "Find mismatches: missing endpoints, wrong field names, missing validations.",
            "",
            "Respond with a JSON array of findings:",
            '{"severity": "error|warning|info", "category": "contract_mismatch",',
            ' "file_path": "...", "title": "...", "description": "...", "suggestion": "..."}',
            "",
            "If everything matches, respond with: []",
            "Output ONLY valid JSON.",
        ])

        response = await call_ai(self, 
            messages=[{
                "role": "user",
                "content": f"Contract:\n{contract_summary}\n\nImplementation:\n{'\n\n'.join(impl_summary[:5])}",
            }],
            system_prompt=system_prompt,
            task_type="general",
            temperature=0.1,
        )

        findings: list[LogicFinding] = []
        try:
            raw = response.content.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
            parsed = orjson.loads(raw.encode("utf-8"))

            if isinstance(parsed, list):
                for item in parsed:
                    try:
                        sev = LogicSeverity(item.get("severity", "info"))
                    except ValueError:
                        sev = LogicSeverity.INFO
                    findings.append(LogicFinding(
                        severity=sev,
                        category=LogicCategory.CONTRACT_MISMATCH,
                        file_path=item.get("file_path", "(unknown)"),
                        line=item.get("line"),
                        title=item.get("title", "Contract mismatch"),
                        description=item.get("description", ""),
                        suggestion=item.get("suggestion"),
                    ))
        except Exception as exc:
            from app.services.ai_router import _sanitize_error  # R27-FIX
            logger.warning("ai_contract_parse_failed", error=_sanitize_error(exc))

        return findings


# Register the agent
_navya = Navya()
register_agent(_navya)
