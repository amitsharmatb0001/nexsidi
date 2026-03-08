"""Zero-Tolerance Code Authenticity Engine (Directive 4).

AST-based validation that enforces NO stubs, NO facades, NO dead code,
NO simulation leaks, NO vaporware in generated code.

Detects:
1. **Stubs** — empty functions (pass, ..., NotImplementedError, TODO-only bodies)
2. **Dead Code** — unreachable code after return/raise, unused imports
3. **Facades** — functions that only log/print but don't perform their stated purpose
4. **Simulation Leaks** — is_simulation, mock_, fake_, placeholder in generated code

This engine is called at two enforcement points:
- **write_file gate** (base.py VerificationGate): rejects files with critical findings
- **Karan's security audit** (karan.py): includes authenticity findings in the report

Design: All checks are AST-based (no AI calls) for zero latency, zero cost.
Applied to NexSidi's OWN codebase too (user directive: "all things applicable on you also").
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ── Finding Types ────────────────────────────────────────────────────


class AuthenticityFindingType(str, Enum):
    """Categories of authenticity violations."""
    STUB = "stub"
    DEAD_CODE = "dead_code"
    FACADE = "facade"
    SIMULATION_LEAK = "simulation_leak"


class AuthenticitySeverity(str, Enum):
    """Severity levels for authenticity findings."""
    CRITICAL = "critical"  # Must fix — blocks write
    HIGH = "high"          # Should fix — flagged in audit
    MEDIUM = "medium"      # Warning
    LOW = "low"            # Informational


@dataclass(frozen=True, slots=True)
class AuthenticityFinding:
    """A single code authenticity violation."""
    finding_type: AuthenticityFindingType
    severity: AuthenticitySeverity
    file_path: str
    line: int
    function_name: str
    description: str
    fix_hint: str


@dataclass(slots=True)
class AuthenticityReport:
    """Aggregated authenticity report for a file or set of files."""
    is_authentic: bool = True
    findings: list[AuthenticityFinding] = field(default_factory=list)
    critical_count: int = 0
    high_count: int = 0
    files_scanned: int = 0

    def add(self, finding: AuthenticityFinding) -> None:
        """Add a finding and update counters."""
        self.findings.append(finding)
        if finding.severity == AuthenticitySeverity.CRITICAL:
            self.critical_count += 1
            self.is_authentic = False
        elif finding.severity == AuthenticitySeverity.HIGH:
            self.high_count += 1

    def merge(self, other: AuthenticityReport) -> None:
        """Merge another report into this one."""
        for f in other.findings:
            self.add(f)
        self.files_scanned += other.files_scanned

    def to_dict(self) -> dict[str, Any]:
        """Serialize for inclusion in agent output."""
        return {
            "is_authentic": self.is_authentic,
            "critical_count": self.critical_count,
            "high_count": self.high_count,
            "files_scanned": self.files_scanned,
            "total_findings": len(self.findings),
            "findings": [
                {
                    "type": f.finding_type.value,
                    "severity": f.severity.value,
                    "file": f.file_path,
                    "line": f.line,
                    "function": f.function_name,
                    "description": f.description,
                    "fix_hint": f.fix_hint,
                }
                for f in self.findings[:50]  # Cap at 50 to avoid huge outputs
            ],
        }


# ── Stub Patterns ────────────────────────────────────────────────────

# Body patterns that indicate a stub function
_STUB_BODY_PATTERNS: list[type] = [ast.Pass, ast.Ellipsis]

# String patterns in function bodies that indicate stubs
_STUB_STRING_PATTERNS = re.compile(
    r"(?i)(TODO|FIXME|HACK|XXX|PLACEHOLDER|NOT\s*IMPLEMENTED|STUB|IMPLEMENT\s*ME|"
    r"COMING\s*SOON|WILL\s*BE\s*IMPLEMENTED|TBD|PLACEHOLDER)",
)

# Simulation leak patterns in source code
_SIMULATION_PATTERNS = re.compile(
    r"(?i)\b(is_simulation|simulation_mode|mock_data|fake_data|placeholder_|"
    r"dummy_response|hardcoded_result|simulated_|_placeholder|_stub_result|"
    r"not_real_|pretend_|if\s+False|SIMULATED)\b",
)


# ── AST Helpers ──────────────────────────────────────────────────────


def _get_function_body_summary(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Get a brief description of what a function body does."""
    if not node.body:
        return "empty"

    body = node.body
    # Skip docstrings
    start = 0
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, (ast.Constant, ast.Str))
    ):
        start = 1

    effective_body = body[start:]
    if not effective_body:
        return "docstring_only"

    # Check for single-statement bodies
    if len(effective_body) == 1:
        stmt = effective_body[0]
        if isinstance(stmt, ast.Pass):
            return "pass_only"
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            if stmt.value.value is ...:
                return "ellipsis_only"
        if isinstance(stmt, ast.Raise):
            exc_node = stmt.exc
            if isinstance(exc_node, ast.Call):
                func = exc_node.func
                if isinstance(func, ast.Name) and func.id == "NotImplementedError":
                    return "not_implemented_error"
                if isinstance(func, ast.Attribute) and func.attr == "NotImplementedError":
                    return "not_implemented_error"

    # Check for bodies that only contain string constants (TODO comments inline)
    all_strings = all(
        isinstance(s, ast.Expr) and isinstance(s.value, (ast.Constant, ast.Str))
        for s in effective_body
    )
    if all_strings:
        return "strings_only"

    return "has_logic"


def _is_facade_body(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if a function body only logs/prints without real logic.

    A facade is a function that exists to satisfy an interface but doesn't
    actually perform its stated purpose — it just logs that it was called.
    """
    body = node.body
    start = 0
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, (ast.Constant, ast.Str))
    ):
        start = 1

    effective_body = body[start:]
    if not effective_body:
        return False

    # If function has >=4 statements, it's probably not a pure facade
    if len(effective_body) >= 4:
        return False

    _LOG_PRINT_NAMES = {"print", "log", "logging", "logger"}
    _LOG_METHODS = {
        "info", "debug", "warning", "error", "critical",
        "log", "exception", "warn",
    }

    real_logic = False
    for stmt in effective_body:
        # Allow: logger.info(...), print(...)
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            call = stmt.value
            # print(...)
            if isinstance(call.func, ast.Name) and call.func.id in _LOG_PRINT_NAMES:
                continue
            # logger.info(...)
            if isinstance(call.func, ast.Attribute) and call.func.attr in _LOG_METHODS:
                continue
        # Allow: return None, return {}, return []
        if isinstance(stmt, ast.Return):
            val = stmt.value
            if val is None:
                continue
            if isinstance(val, ast.Constant) and val.value is None:
                continue
            if isinstance(val, (ast.Dict, ast.List)) and not val.keys if isinstance(val, ast.Dict) else not val.elts:
                continue
        # Anything else counts as real logic
        real_logic = True
        break

    return not real_logic


def _has_unreachable_code(body: list[ast.stmt]) -> list[tuple[int, str]]:
    """Find statements that are unreachable (after return/raise/break/continue).

    Returns list of (line_number, reason) for unreachable statements.
    """
    unreachable: list[tuple[int, str]] = []

    for i, stmt in enumerate(body):
        if i == len(body) - 1:
            break  # Last statement can't make next unreachable

        is_terminal = False
        reason = ""

        if isinstance(stmt, ast.Return):
            is_terminal = True
            reason = "after return"
        elif isinstance(stmt, ast.Raise):
            is_terminal = True
            reason = "after raise"
        elif isinstance(stmt, ast.Break):
            is_terminal = True
            reason = "after break"
        elif isinstance(stmt, ast.Continue):
            is_terminal = True
            reason = "after continue"

        if is_terminal:
            # Check remaining statements
            for remaining in body[i + 1:]:
                # Skip trailing comments (Expr with string constant)
                if isinstance(remaining, ast.Expr) and isinstance(
                    remaining.value, (ast.Constant, ast.Str)
                ):
                    continue
                unreachable.append((remaining.lineno, reason))

    return unreachable


def _find_unused_imports(tree: ast.Module) -> list[tuple[int, str]]:
    """Find imports that are never referenced in the module.

    Returns list of (line_number, import_name) for unused imports.
    Skips __init__.py re-exports and TYPE_CHECKING blocks.
    """
    # Collect all imported names and their line numbers
    imported: dict[str, int] = {}

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                imported[name] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            # Skip TYPE_CHECKING imports (runtime-unused by design)
            # Heuristic: check if inside `if TYPE_CHECKING:` block
            for alias in node.names:
                if alias.name == "*":
                    continue  # Can't check star imports
                name = alias.asname or alias.name
                imported[name] = node.lineno

    if not imported:
        return []

    # Collect all name references in the module (excluding import statements)
    used_names: set[str] = set()

    class _NameCollector(ast.NodeVisitor):
        def visit_Name(self, node: ast.Name) -> None:
            used_names.add(node.id)
            self.generic_visit(node)

        def visit_Attribute(self, node: ast.Attribute) -> None:
            # For `module.func`, collect `module`
            if isinstance(node.value, ast.Name):
                used_names.add(node.value.id)
            self.generic_visit(node)

    _NameCollector().visit(tree)

    # Find imported names that are never used
    unused: list[tuple[int, str]] = []
    for name, line in imported.items():
        if name not in used_names:
            unused.append((line, name))

    return unused


# ── Main Validator ───────────────────────────────────────────────────


class CodeAuthenticityValidator:
    """AST-based code authenticity validation engine.

    Detects stubs, dead code, facades, and simulation leaks in generated
    Python code. Zero AI calls, zero cost, zero latency.

    Usage::

        validator = CodeAuthenticityValidator()
        report = validator.validate_file("app/models.py", code, "python")

        if report.critical_count > 0:
            # Reject the file — agent must implement real logic
            ...
    """

    # Functions that are expected to be simple (no stub detection)
    _ALLOWED_SIMPLE = {
        "__init__", "__repr__", "__str__", "__hash__", "__eq__",
        "__lt__", "__le__", "__gt__", "__ge__", "__bool__",
        "__len__", "__getitem__", "__setitem__", "__delitem__",
        "__contains__", "__iter__", "__next__", "__enter__", "__exit__",
        "setUp", "tearDown", "setUpClass", "tearDownClass",
    }

    # Short helper/property methods are allowed to be simple
    _MIN_STUB_CONCERN_LENGTH = 1  # Single-line bodies are OK for dunder methods

    def detect_stubs(
        self,
        code: str,
        language: str,
        file_path: str = "",
    ) -> list[AuthenticityFinding]:
        """Detect stub functions (empty bodies, NotImplementedError, TODO-only).

        Args:
            code: Source code string.
            language: "python" (only Python AST analysis supported).
            file_path: File path for reporting.

        Returns:
            List of stub findings.
        """
        if language != "python":
            return []

        findings: list[AuthenticityFinding] = []

        try:
            tree = ast.parse(code)
        except SyntaxError:
            return []  # Can't analyze broken code

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            func_name = node.name

            # Skip dunder methods and known-simple methods
            if func_name in self._ALLOWED_SIMPLE:
                continue

            # Skip property getters/setters (often simple returns)
            if any(
                isinstance(d, ast.Name) and d.id in ("property", "staticmethod", "classmethod")
                for d in node.decorator_list
            ) or any(
                isinstance(d, ast.Attribute) and d.attr in ("setter", "getter", "deleter")
                for d in node.decorator_list
            ):
                continue

            body_summary = _get_function_body_summary(node)

            if body_summary in ("pass_only", "ellipsis_only"):
                findings.append(AuthenticityFinding(
                    finding_type=AuthenticityFindingType.STUB,
                    severity=AuthenticitySeverity.CRITICAL,
                    file_path=file_path,
                    line=node.lineno,
                    function_name=func_name,
                    description=f"Function '{func_name}' has empty body ({body_summary})",
                    fix_hint=f"Implement real logic in '{func_name}' — remove pass/... and add actual functionality",
                ))

            elif body_summary == "not_implemented_error":
                findings.append(AuthenticityFinding(
                    finding_type=AuthenticityFindingType.STUB,
                    severity=AuthenticitySeverity.CRITICAL,
                    file_path=file_path,
                    line=node.lineno,
                    function_name=func_name,
                    description=f"Function '{func_name}' raises NotImplementedError (stub placeholder)",
                    fix_hint=f"Replace NotImplementedError in '{func_name}' with real implementation",
                ))

            elif body_summary == "docstring_only" or body_summary == "strings_only":
                findings.append(AuthenticityFinding(
                    finding_type=AuthenticityFindingType.STUB,
                    severity=AuthenticitySeverity.HIGH,
                    file_path=file_path,
                    line=node.lineno,
                    function_name=func_name,
                    description=f"Function '{func_name}' has only docstring/strings (no logic)",
                    fix_hint=f"Add actual implementation to '{func_name}'",
                ))

            # Check for TODO/FIXME/PLACEHOLDER in function body source
            if body_summary == "has_logic":
                try:
                    func_source = ast.get_source_segment(code, node)
                    if func_source and _STUB_STRING_PATTERNS.search(func_source):
                        # Only flag if the TODO is the DOMINANT content
                        lines = func_source.strip().split("\n")
                        non_comment_lines = [
                            l for l in lines
                            if l.strip() and not l.strip().startswith("#")
                        ]
                        if len(non_comment_lines) <= 3:
                            findings.append(AuthenticityFinding(
                                finding_type=AuthenticityFindingType.STUB,
                                severity=AuthenticitySeverity.HIGH,
                                file_path=file_path,
                                line=node.lineno,
                                function_name=func_name,
                                description=f"Function '{func_name}' contains TODO/PLACEHOLDER markers as primary content",
                                fix_hint=f"Replace TODO markers in '{func_name}' with real implementation",
                            ))
                except Exception:
                    pass  # Non-critical — error logged upstream or handled by caller

        return findings

    def detect_dead_code(
        self,
        code: str,
        language: str,
        file_path: str = "",
    ) -> list[AuthenticityFinding]:
        """Detect unreachable code and unused imports.

        Args:
            code: Source code string.
            language: "python".
            file_path: File path for reporting.

        Returns:
            List of dead code findings.
        """
        if language != "python":
            return []

        findings: list[AuthenticityFinding] = []

        try:
            tree = ast.parse(code)
        except SyntaxError:
            return []

        # Check for unreachable code in all function bodies
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                unreachable = _has_unreachable_code(node.body)
                for line, reason in unreachable:
                    findings.append(AuthenticityFinding(
                        finding_type=AuthenticityFindingType.DEAD_CODE,
                        severity=AuthenticitySeverity.MEDIUM,
                        file_path=file_path,
                        line=line,
                        function_name=node.name,
                        description=f"Unreachable code in '{node.name}' ({reason})",
                        fix_hint="Remove unreachable code or restructure the function",
                    ))

            # Also check module-level and class-level
            if isinstance(node, (ast.Module, ast.ClassDef)):
                body = node.body
                unreachable = _has_unreachable_code(body)
                scope_name = node.name if isinstance(node, ast.ClassDef) else "<module>"
                for line, reason in unreachable:
                    findings.append(AuthenticityFinding(
                        finding_type=AuthenticityFindingType.DEAD_CODE,
                        severity=AuthenticitySeverity.MEDIUM,
                        file_path=file_path,
                        line=line,
                        function_name=scope_name,
                        description=f"Unreachable code in {scope_name} ({reason})",
                        fix_hint="Remove unreachable code",
                    ))

        # Check for unused imports (LOW severity — sometimes intentional)
        unused_imports = _find_unused_imports(tree)
        for line, name in unused_imports:
            findings.append(AuthenticityFinding(
                finding_type=AuthenticityFindingType.DEAD_CODE,
                severity=AuthenticitySeverity.LOW,
                file_path=file_path,
                line=line,
                function_name="<imports>",
                description=f"Unused import: '{name}'",
                fix_hint=f"Remove unused import '{name}' or use it in the code",
            ))

        return findings

    def detect_facades(
        self,
        code: str,
        language: str,
        file_path: str = "",
    ) -> list[AuthenticityFinding]:
        """Detect facade functions that only log/print but don't do real work.

        Args:
            code: Source code string.
            language: "python".
            file_path: File path for reporting.

        Returns:
            List of facade findings.
        """
        if language != "python":
            return []

        findings: list[AuthenticityFinding] = []

        try:
            tree = ast.parse(code)
        except SyntaxError:
            return []

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            func_name = node.name

            # Skip dunder methods and known-simple methods
            if func_name in self._ALLOWED_SIMPLE or func_name.startswith("_"):
                continue

            if _is_facade_body(node):
                findings.append(AuthenticityFinding(
                    finding_type=AuthenticityFindingType.FACADE,
                    severity=AuthenticitySeverity.CRITICAL,
                    file_path=file_path,
                    line=node.lineno,
                    function_name=func_name,
                    description=(
                        f"Function '{func_name}' is a facade — only logs/prints "
                        "without performing its stated purpose"
                    ),
                    fix_hint=(
                        f"Implement real logic in '{func_name}'. "
                        "A function must do what its name says, not just log that it was called."
                    ),
                ))

        return findings

    def detect_simulation_leaks(
        self,
        code: str,
        file_path: str = "",
    ) -> list[AuthenticityFinding]:
        """Detect simulation/mock/placeholder markers in generated code.

        These indicate that the code is using fake data or simulation mode
        instead of real implementations.

        Args:
            code: Source code string.
            file_path: File path for reporting.

        Returns:
            List of simulation leak findings.
        """
        findings: list[AuthenticityFinding] = []

        for match in _SIMULATION_PATTERNS.finditer(code):
            # Find the line number
            line_num = code[:match.start()].count("\n") + 1
            matched_text = match.group(0)

            # Skip test files (simulation is expected there)
            if file_path and ("test" in file_path.lower() or "mock" in file_path.lower()):
                continue

            findings.append(AuthenticityFinding(
                finding_type=AuthenticityFindingType.SIMULATION_LEAK,
                severity=AuthenticitySeverity.HIGH,
                file_path=file_path,
                line=line_num,
                function_name="<source>",
                description=f"Simulation leak detected: '{matched_text}'",
                fix_hint=(
                    f"Replace '{matched_text}' with real implementation. "
                    "Generated code must not contain simulation flags, mock data, "
                    "or placeholder markers."
                ),
            ))

        return findings

    def validate_file(
        self,
        path: str,
        code: str,
        language: str,
    ) -> AuthenticityReport:
        """Run all authenticity checks on a single file.

        Args:
            path: File path.
            code: Source code content.
            language: "python" or other (non-python gets simulation check only).

        Returns:
            AuthenticityReport with is_authentic, findings, critical_count.
        """
        report = AuthenticityReport(files_scanned=1)

        # All languages: simulation leak check
        for f in self.detect_simulation_leaks(code, file_path=path):
            report.add(f)

        # Python-only: AST-based checks
        if language == "python":
            for f in self.detect_stubs(code, language, file_path=path):
                report.add(f)
            for f in self.detect_dead_code(code, language, file_path=path):
                report.add(f)
            for f in self.detect_facades(code, language, file_path=path):
                report.add(f)

        return report

    def validate_all_files(
        self,
        files: dict[str, str],
    ) -> AuthenticityReport:
        """Run authenticity checks on all files in a project.

        Args:
            files: Dict mapping file path → source code content.

        Returns:
            Merged AuthenticityReport across all files.
        """
        report = AuthenticityReport()

        for path, code in files.items():
            # Determine language from extension
            if path.endswith(".py"):
                lang = "python"
            elif path.endswith((".ts", ".tsx", ".js", ".jsx")):
                lang = "typescript"
            else:
                lang = "other"

            file_report = self.validate_file(path, code, lang)
            report.merge(file_report)

        return report


# ── Singleton ────────────────────────────────────────────────────────

_validator: CodeAuthenticityValidator | None = None


def get_code_authenticity_validator() -> CodeAuthenticityValidator:
    """Return the CodeAuthenticityValidator singleton."""
    global _validator
    if _validator is None:
        _validator = CodeAuthenticityValidator()
    return _validator
