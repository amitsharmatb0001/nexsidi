"""Fixer — Autonomous Error Correction: reads errors, fixes code, re-tests.

The Fixer agent receives structured error reports from Aarav (test executor)
and quality gates (Karan, Navya, Deepika), then applies targeted fixes.

Design (AUDIT FIX #18):
- Receives: structured error JSON + relevant file content + architecture contract
- Fixer NEVER sees raw user input in error messages
- Error context sanitized: only file_path, line_number, error_type, code_snippet
- ONE fix per iteration, max 5 iterations
- Model escalation: starts with Gemini 2.5 Pro, escalates to Sonnet 4.6

Model escalation chain:
  Iteration 1-2: Gemini 2.5 Pro (cheaper, handles most fixes)
  Iteration 3-4: Claude Sonnet 4.6 (harder fixes need stronger model)
  Iteration 5:   Claude Sonnet 4.6 + extended thinking (last resort)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

from app.agents.base import (
    AgentResult,
    AgentStatus,
    ToolDefinition,
    call_ai_with_tools,
    register_agent,
    resolve_model_override,
    run_agent,
    store_output,
)
from app.services.ai_router import TaskComplexity

logger = structlog.get_logger(__name__)


# ── Fixer Configuration ───────────────────────────────────────────

MAX_FIX_ITERATIONS: int = 5

# Model escalation by iteration number (AUDIT FIX #18)
# Uses MODELS registry keys (not raw model IDs) — mode-aware.
_ITERATION_MODELS: dict[str, dict[int, tuple[str, bool]]] = {
    "mixed": {
        1: ("gemini-pro", False),
        2: ("gemini-pro", False),
        3: ("sonnet", False),
        4: ("sonnet", False),
        5: ("sonnet", True),
    },
    "gemini": {
        1: ("gemini-pro", False),
        2: ("gemini-pro", False),
        3: ("gemini-3.1-flash-lite", False),
        4: ("gemini-3.1-flash-lite", False),
        5: ("gemini-3.1-pro", True),
    },
    "claude": {
        1: ("haiku", False),
        2: ("haiku", False),
        3: ("sonnet", False),
        4: ("sonnet", False),
        5: ("sonnet", True),
    },
}


def _get_iteration_model_map() -> dict[int, tuple[str, bool]]:
    """Get model escalation map for the current AI provider mode."""
    from app.services.ai_router import get_ai_mode
    mode = get_ai_mode()
    # R29-FIX-13: Fallback to "mixed" for unknown modes instead of KeyError.
    return _ITERATION_MODELS.get(mode, _ITERATION_MODELS["mixed"])


class FixStatus(str, Enum):
    """Outcome of a fix attempt."""

    FIXED = "fixed"
    PARTIAL = "partial"        # Some issues fixed, others remain
    FAILED = "failed"          # Fix attempt didn't resolve the issue
    MAX_ITERATIONS = "max_iterations"  # Hit iteration limit


@dataclass(slots=True)
class SanitizedError:
    """Sanitized error context — no raw user input, no secrets.

    AUDIT FIX #18: Fixer only sees structured, sanitized error data.
    """

    file_path: str
    line_number: int | None
    error_type: str          # e.g., "ImportError", "TypeError", "test_failure"
    error_message: str       # Sanitized: no user input, no secrets
    code_snippet: str        # ~10 lines around the error
    severity: str            # critical, high, medium, low
    source_agent: str        # Which agent reported this (karan, navya, deepika, aarav)

    @classmethod
    def from_finding(cls, finding: dict[str, Any]) -> SanitizedError:
        """Create from a quality gate finding dict."""
        return cls(
            file_path=finding.get("file_path", "(unknown)"),
            line_number=finding.get("line"),
            error_type=finding.get("category", "unknown"),
            error_message=_sanitize_message(finding.get("description", "")),
            code_snippet=finding.get("code_snippet", ""),
            severity=finding.get("severity", "medium"),
            source_agent=finding.get("source_agent", "unknown"),
        )

    @classmethod
    def from_test_error(cls, error: dict[str, Any]) -> SanitizedError:
        """Create from an Aarav test error dict."""
        return cls(
            file_path=error.get("file", "(unknown)"),
            line_number=error.get("line"),
            error_type=error.get("type", "test_failure"),
            error_message=_sanitize_message(error.get("details", "")),
            code_snippet=error.get("code_snippet", ""),
            severity="high",
            source_agent="aarav",
        )


@dataclass(slots=True)
class FixAttempt:
    """Record of a single fix attempt."""

    iteration: int
    error: SanitizedError
    model_used: str
    fix_applied: str          # Description of the fix
    file_path: str
    file_content_before: str  # Content before fix
    file_content_after: str   # Content after fix
    success: bool
    diff_mode: str = "write_file"  # "apply_diff" or "write_file"


@dataclass(slots=True)
class FixerReport:
    """Complete report from the Fixer agent."""

    iterations: int = 0
    attempts: list[FixAttempt] = field(default_factory=list)
    errors_received: int = 0
    errors_fixed: int = 0
    errors_remaining: int = 0
    status: FixStatus = FixStatus.FAILED
    files_modified: list[str] = field(default_factory=list)
    # I4-FIX: Severity classification for fast-path fix routing.
    # TRIVIAL = syntax/import/typo — pipeline can skip quality review.
    # MODERATE = logic fixes, type corrections.
    # STRUCTURAL = new files, API/schema changes — must re-run full quality.
    fix_severity: str = "STRUCTURAL"


def _classify_fix_severity(attempts: list[FixAttempt]) -> str:
    """Classify overall fix severity from individual attempts.

    I4-FIX: Drives fast-path routing in pipeline fix-retest loop.
    Conservative: any ambiguity defaults to STRUCTURAL (safe).
    """
    if not attempts:
        return "TRIVIAL"

    _STRUCTURAL = {
        "new file", "endpoint", "schema", "model change", "migration",
        "router", "new route", "database", "table",
    }
    _TRIVIAL = {
        "import", "syntax", "typo", "indent", "format", "missing comma",
        "bracket", "semicolon", "undefined variable", "undefined name",
        "missing colon", "missing parenthesis", "whitespace",
    }

    has_structural = False
    has_non_trivial = False

    for attempt in attempts:
        desc_lower = (attempt.fix_applied or "").lower()
        if any(p in desc_lower for p in _STRUCTURAL):
            has_structural = True
        elif not any(p in desc_lower for p in _TRIVIAL):
            has_non_trivial = True

    if has_structural:
        return "STRUCTURAL"
    if has_non_trivial:
        return "MODERATE"
    return "TRIVIAL"


def _sanitize_message(message: str) -> str:
    """Remove potential user input / secrets from error messages (AUDIT FIX #18)."""
    import re

    # Remove anything that looks like a token/key/password
    sanitized = re.sub(r"""(?:token|key|password|secret|auth)\s*[:=]\s*\S+""", "[REDACTED]", message, flags=re.IGNORECASE)
    # Remove email addresses
    sanitized = re.sub(r"""\b[\w.+-]+@[\w-]+\.[\w.]+\b""", "[EMAIL_REDACTED]", sanitized)
    # Truncate very long messages (may contain user data)
    if len(sanitized) > 500:
        sanitized = sanitized[:500] + "... [truncated]"
    return sanitized


# ── D3-FIX: Search/Replace Helpers ─────────────────────────────────


def _fuzzy_line_match(content: str, search: str) -> tuple[int, int] | None:
    """Find search text in content using line-by-line rstrip() comparison.

    Returns (start_pos, end_pos) in the original content, or None.
    """
    content_lines = content.split("\n")
    search_lines = [line.rstrip() for line in search.split("\n")]

    # Remove leading/trailing empty search lines
    while search_lines and not search_lines[0].strip():
        search_lines.pop(0)
    while search_lines and not search_lines[-1].strip():
        search_lines.pop()

    if not search_lines:
        return None

    for i in range(len(content_lines) - len(search_lines) + 1):
        matches = True
        for j, search_line in enumerate(search_lines):
            if content_lines[i + j].rstrip() != search_line:
                matches = False
                break
        if matches:
            # Calculate byte positions in original content
            start = sum(len(line) + 1 for line in content_lines[:i])
            end = sum(len(line) + 1 for line in content_lines[:i + len(search_lines)])
            # Don't include trailing newline of last matched line
            if end > 0 and end <= len(content) + 1:
                end -= 1
            return (start, min(end, len(content)))
    return None


def _normalized_find(content: str, search: str) -> tuple[int, int] | None:
    """Find search text using whitespace-normalized comparison.

    Collapses all whitespace runs to single spaces in both content and search,
    finds the match position, then maps back to original content positions.

    Returns (start_pos, end_pos) in the original content, or None.
    """
    import re as _re

    norm_search = _re.sub(r"\s+", " ", search).strip()
    if not norm_search:
        return None

    norm_content = _re.sub(r"\s+", " ", content).strip()
    norm_idx = norm_content.find(norm_search)
    if norm_idx == -1:
        return None

    # Map normalized positions back to original content
    # Walk through original content counting non-whitespace-collapsed chars
    orig_start = _map_norm_pos(content, norm_idx)
    orig_end = _map_norm_pos(content, norm_idx + len(norm_search))
    return (orig_start, orig_end)


def _map_norm_pos(original: str, norm_pos: int) -> int:
    """Map a position in whitespace-normalized text back to the original."""
    import re as _re

    norm_idx = 0
    in_ws = False
    for i, ch in enumerate(original):
        if norm_idx >= norm_pos:
            return i
        if ch in " \t\n\r":
            if not in_ws:
                norm_idx += 1  # Collapsed whitespace run = 1 space
                in_ws = True
        else:
            norm_idx += 1
            in_ws = False
    return len(original)


# ── Fixer Tool Handler ─────────────────────────────────────────────


class FixerToolHandler:
    """Handles tool calls from Fixer's agentic tool loop."""

    def __init__(
        self,
        context: dict,
        error: SanitizedError,
        error_report: dict,
        pipeline_run_id: str = "",
    ):
        self._context = context
        self._error = error
        self._error_report = error_report
        self._pipeline_run_id = pipeline_run_id
        self._written_files: dict[str, str] = {}  # path -> new content
        self._fix_description = ""
        self._validation_passed = False
        self._complete = False
        self._used_diff = False  # Track if apply_diff was used (for telemetry)

    async def __call__(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == "read_file":
            return await self._read_file(tool_input["path"])
        elif tool_name == "write_file":
            return self._write_file(tool_input["path"], tool_input["content"])
        elif tool_name == "apply_diff":
            return await self._apply_diff(tool_input["path"], tool_input["diff"])
        elif tool_name == "search_replace":
            return await self._search_replace(tool_input["path"], tool_input["operations"])
        elif tool_name == "validate_syntax":
            return self._validate_syntax(tool_input["path"], tool_input.get("content"))
        elif tool_name == "read_error_report":
            import json
            return json.dumps(self._error_report, indent=2)
        elif tool_name == "report_complete":
            return self._report_complete(
                tool_input["fix_description"],
                tool_input.get("validation_passed", False),
            )
        elif tool_name == "check_imports":
            return await self._check_imports(tool_input["path"])
        elif tool_name == "search_solution":
            return await self._search_solution(
                tool_input["error_message"],
                tool_input.get("context", ""),
            )
        else:
            return f"Unknown tool: {tool_name}"

    async def _read_file(self, path: str) -> str:
        # Check written_files first (latest version)
        if path in self._written_files:
            content = self._written_files[path]
            if len(content) > 50000:
                return content[:50000] + f"\n...[truncated at 50K, file is {len(content)} chars]"
            return content

        # D2-FIX: Try VFS first for offloaded files
        if self._pipeline_run_id:
            try:
                from app.services.vfs import get_vfs

                vfs = get_vfs()
                content = await vfs.read_file(self._pipeline_run_id, path)
                if content is not None:
                    if len(content) > 50000:
                        return content[:50000] + f"\n...[truncated at 50K, file is {len(content)} chars]"
                    return content
            except Exception:
                pass  # VFS unavailable — fall through to inline context

        # Fall back to inline context (original generated content)
        for agent in ("shubham", "aanya"):
            agent_out = self._context.get(agent, {})
            if isinstance(agent_out, dict):
                fc = agent_out.get("file_contents", {})
                if isinstance(fc, dict) and not fc.get("__vfs__"):
                    if path in fc:
                        content = fc[path]
                        if len(content) > 50000:
                            return content[:50000] + f"\n...[truncated at 50K, file is {len(content)} chars]"
                        return content
        return f"File not found: {path}"

    def _write_file(self, path: str, content: str) -> str:
        if len(content) < 10:
            return "Error: content too short — must be a complete file"
        self._written_files[path] = content
        return f"Written {path} ({len(content)} chars)"

    async def _apply_diff(self, path: str, diff_text: str) -> str:
        """Apply a unified diff to an existing file.

        REVIEW-FIX: Fixer now supports targeted patches instead of full file
        rewrites. This saves ~10x tokens and avoids accidentally deleting
        unrelated code. Falls back to write_file if diff application fails.

        Accepts standard unified diff format:
            --- a/path
            +++ b/path
            @@ -start,count +start,count @@
            -removed line
            +added line
             context line
        """
        # Get current file content
        current = await self._read_file(path)
        if current.startswith("File not found"):
            return f"Cannot apply diff: {current}"

        lines = current.split("\n")
        hunks = self._parse_unified_diff(diff_text)

        if not hunks:
            return "Error: could not parse any diff hunks. Expected unified diff format with @@ markers."

        # Apply hunks in REVERSE order (bottom-up) so line numbers stay valid
        hunks.sort(key=lambda h: h["old_start"], reverse=True)
        applied = 0
        errors: list[str] = []

        for hunk in hunks:
            old_start = hunk["old_start"] - 1  # 0-indexed
            old_lines = hunk["old_lines"]
            new_lines = hunk["new_lines"]

            # Fuzzy match: try exact position first, then search nearby (+/- 5 lines)
            match_offset = self._find_hunk_match(lines, old_lines, old_start)

            if match_offset is not None:
                # Replace old lines with new lines at matched position
                lines[match_offset:match_offset + len(old_lines)] = new_lines
                applied += 1
            else:
                # Context lines didn't match — report but continue with other hunks
                preview = old_lines[0] if old_lines else "(empty)"
                errors.append(
                    f"Hunk at line {hunk['old_start']} failed to match "
                    f"(expected: {preview!r})"
                )

        if applied == 0:
            return (
                f"Error: all {len(hunks)} hunks failed to apply. "
                f"The file may have changed. Errors: {'; '.join(errors)}. "
                f"Use write_file with complete content instead."
            )

        result = "\n".join(lines)
        self._written_files[path] = result
        self._used_diff = True

        msg = f"Applied {applied}/{len(hunks)} hunks to {path} ({len(result)} chars)"
        if errors:
            msg += f". Warnings: {'; '.join(errors)}"
        return msg

    @staticmethod
    def _parse_unified_diff(diff_text: str) -> list[dict]:
        """Parse unified diff text into a list of hunk dicts.

        Each hunk dict has:
            old_start: int (1-indexed line number in original file)
            old_lines: list[str] (lines to remove / match as context)
            new_lines: list[str] (lines to insert)
        """
        import re

        hunks: list[dict] = []
        hunk_header_re = re.compile(r"^@@\s+-(\d+)(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@")

        current_hunk: dict | None = None

        for raw_line in diff_text.split("\n"):
            # Skip file headers (--- and +++)
            if raw_line.startswith("--- ") or raw_line.startswith("+++ "):
                continue

            # Detect hunk header
            m = hunk_header_re.match(raw_line)
            if m:
                if current_hunk is not None:
                    hunks.append(current_hunk)
                current_hunk = {
                    "old_start": int(m.group(1)),
                    "old_lines": [],
                    "new_lines": [],
                }
                continue

            if current_hunk is None:
                continue

            if raw_line.startswith("-"):
                # Removed line: goes to old_lines only
                current_hunk["old_lines"].append(raw_line[1:])
            elif raw_line.startswith("+"):
                # Added line: goes to new_lines only
                current_hunk["new_lines"].append(raw_line[1:])
            elif raw_line.startswith(" ") or raw_line == "":
                # Context line: goes to BOTH old and new
                ctx = raw_line[1:] if raw_line.startswith(" ") else raw_line
                current_hunk["old_lines"].append(ctx)
                current_hunk["new_lines"].append(ctx)

        if current_hunk is not None:
            hunks.append(current_hunk)

        return hunks

    @staticmethod
    def _find_hunk_match(
        lines: list[str],
        old_lines: list[str],
        expected_start: int,
    ) -> int | None:
        """Find where old_lines match in the file, with fuzzy offset search.

        Tries exact position first, then searches +/- 10 lines around it.
        Returns the 0-indexed start position, or None if no match.
        """
        if not old_lines:
            # Pure insertion: return expected position
            return max(0, min(expected_start, len(lines)))

        def _matches_at(offset: int) -> bool:
            if offset < 0 or offset + len(old_lines) > len(lines):
                return False
            for a, b in zip(lines[offset:offset + len(old_lines)], old_lines):
                if a.rstrip() != b.rstrip():
                    return False
            return True

        # Try exact position first
        if _matches_at(expected_start):
            return expected_start

        # Fuzzy search: +/- 10 lines
        for delta in range(1, 11):
            if _matches_at(expected_start + delta):
                return expected_start + delta
            if _matches_at(expected_start - delta):
                return expected_start - delta

        return None

    # ── D3-FIX: Search/Replace ────────────────────────────────────────

    async def _search_replace(self, path: str, operations: list[dict]) -> str:
        """Apply search/replace operations to a file.

        D3-FIX: More reliable than unified diffs for LLM-generated patches.
        Each operation has:
            search: exact text to find (multi-line)
            replace: text to replace it with (multi-line)

        Three-tier matching:
        1. Exact string match
        2. Whitespace-normalized fuzzy match
        3. Line-by-line rstrip() match

        Operations are applied sequentially.  If a search string is not found,
        the operation is skipped with a warning.
        """
        current = await self._read_file(path)
        if current.startswith("File not found"):
            return f"Cannot apply search/replace: {current}"

        applied = 0
        errors: list[str] = []
        content = current

        for i, op in enumerate(operations):
            search = op.get("search", "")
            replace = op.get("replace", "")

            if not search:
                errors.append(f"Op {i + 1}: empty search string")
                continue

            # Tier 1: Exact string match
            if search in content:
                content = content.replace(search, replace, 1)
                applied += 1
                continue

            # Tier 2: Line-by-line fuzzy match (rstrip comparison)
            match_result = _fuzzy_line_match(content, search)
            if match_result is not None:
                start_pos, end_pos = match_result
                content = content[:start_pos] + replace + content[end_pos:]
                applied += 1
                continue

            # Tier 3: Whitespace-normalized match
            norm_idx = _normalized_find(content, search)
            if norm_idx is not None:
                start_pos, end_pos = norm_idx
                content = content[:start_pos] + replace + content[end_pos:]
                applied += 1
                continue

            # No match found
            preview = search[:60].replace("\n", "\\n")
            errors.append(f"Op {i + 1}: search text not found: {preview!r}")

        if applied == 0:
            return (
                f"Error: all {len(operations)} operations failed. "
                f"Errors: {'; '.join(errors)}. "
                f"Use write_file with complete content instead."
            )

        self._written_files[path] = content
        self._used_diff = True

        msg = f"Applied {applied}/{len(operations)} search/replace operations to {path}"
        if errors:
            msg += f". Warnings: {'; '.join(errors)}"
        return msg

    def _validate_syntax(self, path: str, content: str | None = None) -> str:
        code = content or self._written_files.get(path, "")
        if not code:
            return f"No content for {path}"
        if path.endswith(".py"):
            import ast
            try:
                ast.parse(code)
                return "OK — Python syntax is valid"
            except SyntaxError as e:
                return f"SyntaxError at line {e.lineno}: {e.msg}"
        # Non-Python: basic bracket balance
        opens = code.count("{") + code.count("(") + code.count("[")
        closes = code.count("}") + code.count(")") + code.count("]")
        if abs(opens - closes) > 5:
            return f"Possible issue: unbalanced brackets (opens={opens}, closes={closes})"
        return "OK"

    def _report_complete(self, fix_description: str, validation_passed: bool) -> str:
        self._fix_description = fix_description
        self._validation_passed = validation_passed
        self._complete = True
        return f"Fix reported as complete: {fix_description}"

    async def _check_imports(self, path: str) -> str:
        """Verify all imports in a Python file resolve to known modules.

        I3-FIX: Delegates to shared code_validator for consistency across
        Shubham, Aanya, and Fixer agents.
        """
        code = self._written_files.get(path, "")
        if not code:
            code = await self._read_file(path)
            if code.startswith("File not found"):
                return code

        from app.agents.tools.code_validator import check_imports, extract_project_files

        project_files = extract_project_files(self._context)
        # Also include files written in the current fix session
        for fp in self._written_files:
            if fp.endswith(".py"):
                mod = fp.replace("/", ".").replace("\\", ".")
                if mod.endswith(".py"):
                    mod = mod[:-3]
                project_files.add(mod)
        return check_imports(path, code, project_files)

    async def _search_solution(self, error_message: str, context: str = "") -> str:
        """Search web for a solution using the Research Agent.

        Uses Gemini with Google Search Grounding — zero extra cost.
        """
        try:
            from app.agents.research_agent import ResearchAgent

            researcher = ResearchAgent(project_id=self._pipeline_run_id)
            result = await researcher.search_for_solution(
                error_msg=error_message,
                context=context,
            )
            solution = result.get("solution", "")
            sources = result.get("sources", [])

            if not solution:
                return "No solution found via web search."

            response = f"Web search result:\n{solution[:1000]}"
            if sources:
                response += f"\n\nSources: {', '.join(sources[:3])}"
            return response

        except Exception as exc:
            from app.services.ai_router import _sanitize_error
            return f"Web search failed: {_sanitize_error(exc)}"


# ── Fixer Agent ────────────────────────────────────────────────────


class Fixer:
    """Autonomous Error Correction — reads errors, generates targeted fixes.

    Max 5 iterations with model escalation.
    """

    name = "fixer"
    display_name = "Fixer — Error Correction"
    default_complexity = TaskComplexity.HIGH
    default_model: str | None = None

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

        self.register_tool(ToolDefinition(
            name="read_file",
            description="Read a generated code file that needs fixing.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read."},
                },
                "required": ["path"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="write_file",
            description=(
                "Write a COMPLETE file. Use ONLY for new files or when the diff is "
                "larger than 50%% of the file. For small targeted fixes, PREFER apply_diff."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to write."},
                    "content": {"type": "string", "description": "Complete fixed file content."},
                },
                "required": ["path", "content"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="apply_diff",
            description=(
                "Apply a targeted unified diff patch to fix specific lines. "
                "Use standard unified diff format with @@ hunk headers. "
                "NOTE: Prefer search_replace over apply_diff — it's more "
                "reliable because it uses content matching instead of line numbers."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path to patch.",
                    },
                    "diff": {
                        "type": "string",
                        "description": (
                            "Unified diff text. Example:\n"
                            "--- a/app/models.py\n"
                            "+++ b/app/models.py\n"
                            "@@ -10,3 +10,3 @@\n"
                            " class User:\n"
                            "-    email = Column(String)\n"
                            "+    email = Column(String, unique=True, index=True)\n"
                            "     name = Column(String)\n"
                        ),
                    },
                },
                "required": ["path", "diff"],
            },
        ))

        # D3-FIX: Search/Replace tool — more reliable than unified diffs
        self.register_tool(ToolDefinition(
            name="search_replace",
            description=(
                "Apply search/replace operations to fix specific code sections. "
                "PREFERRED over apply_diff — more reliable because it uses content "
                "matching instead of line numbers. Each operation finds exact text "
                "and replaces it. Include 2-3 context lines in the search string "
                "for unique matching. Use for targeted fixes; use write_file for "
                "complete rewrites (>50%% of file changed)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path to modify.",
                    },
                    "operations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "search": {
                                    "type": "string",
                                    "description": (
                                        "Exact text to find (multi-line OK). "
                                        "Include enough surrounding context "
                                        "lines for unique matching."
                                    ),
                                },
                                "replace": {
                                    "type": "string",
                                    "description": "Replacement text.",
                                },
                            },
                            "required": ["search", "replace"],
                        },
                        "description": (
                            "List of search/replace operations applied sequentially."
                        ),
                    },
                },
                "required": ["path", "operations"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="read_error_report",
            description="Read the structured error report from quality gates / testing.",
            parameters={
                "type": "object",
                "properties": {},
            },
        ))

        self.register_tool(ToolDefinition(
            name="validate_syntax",
            description="Validate Python syntax of a file after fixing. Returns 'OK' or the syntax error.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to validate"},
                    "content": {"type": "string", "description": "Content to validate (uses written content if omitted)"},
                },
                "required": ["path"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="report_complete",
            description="Signal that the fix is complete and validated.",
            parameters={
                "type": "object",
                "properties": {
                    "fix_description": {"type": "string", "description": "Brief description of what was fixed"},
                    "validation_passed": {"type": "boolean", "description": "Whether syntax validation passed"},
                },
                "required": ["fix_description", "validation_passed"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="check_imports",
            description=(
                "Verify all imports in a Python file resolve to existing modules "
                "or project files. Returns a list of unresolved imports so you "
                "can fix them (add missing deps or correct import paths)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Python file path to check imports for.",
                    },
                },
                "required": ["path"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="search_solution",
            description=(
                "Search the web for a solution to an error you can't figure out. "
                "Uses Gemini with Google Search Grounding to find real answers. "
                "Call this BEFORE attempting a fix for unfamiliar errors."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "error_message": {
                        "type": "string",
                        "description": "The error message to search for.",
                    },
                    "context": {
                        "type": "string",
                        "description": "Additional context (framework, language, etc.)",
                    },
                },
                "required": ["error_message"],
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
        """Run the fix loop: read errors -> fix code -> repeat until max iterations.

        ONE fix per iteration. Model escalation per iteration.
        """
        # Collect all errors from quality gates + Aarav test results
        errors = self._collect_errors(context)

        if not errors:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.COMPLETED,
                output={"status": "no_errors", "message": "No errors to fix"},
            )

        report = FixerReport(errors_received=len(errors))

        # Prioritize: CRITICAL -> HIGH -> MEDIUM -> LOW
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        errors.sort(key=lambda e: priority_order.get(e.severity, 99))

        # Get contract for context
        contract = context.get("vikram", {}).get("contract", {})

        # Fix loop: one error per iteration, max MAX_FIX_ITERATIONS
        remaining_errors = list(errors)
        iteration = 0

        while remaining_errors and iteration < MAX_FIX_ITERATIONS:
            iteration += 1
            report.iterations = iteration
            current_error = remaining_errors[0]

            model_override, enable_thinking = _get_iteration_model_map().get(
                iteration, ("sonnet", False)
            )

            logger.info(
                "fixer_iteration",
                iteration=iteration,
                model=model_override,
                error_type=current_error.error_type,
                file=current_error.file_path,
                remaining=len(remaining_errors),
            )

            attempt = await self._attempt_fix(
                iteration=iteration,
                error=current_error,
                contract=contract,
                context=context,
                model_override=model_override,
                enable_thinking=enable_thinking,
                pipeline_run_id=pipeline_run_id,
            )

            report.attempts.append(attempt)

            if attempt.success:
                report.errors_fixed += 1
                remaining_errors.pop(0)

                # Update context with fixed file content
                if attempt.file_path and attempt.file_content_after:
                    self._update_file_in_context(
                        context, attempt.file_path, attempt.file_content_after
                    )
                    if attempt.file_path not in report.files_modified:
                        report.files_modified.append(attempt.file_path)

                # Record successful fix in mistake memory for future learning
                try:
                    from app.services.mistake_memory import mistake_memory
                    mistake_memory.record_failure(
                        agent_name=current_error.source_agent or "unknown",
                        task_type=current_error.error_type,
                        error=current_error.error_message,
                        fix=attempt.fix_applied,
                        context={
                            "file_path": current_error.file_path,
                            "severity": current_error.severity,
                        },
                    )
                except Exception:
                    pass  # Mistake memory is non-fatal
            else:
                # If fix failed, try next error (don't re-attempt same one immediately)
                failed = remaining_errors.pop(0)
                remaining_errors.append(failed)
                # AUDIT-FIX: Re-sort by severity after rotation so CRITICAL
                # errors aren't stuck behind LOW ones. Without this, a failed
                # CRITICAL gets appended after MEDIUMs, wasting stronger
                # model tiers on lower-priority errors.
                remaining_errors.sort(
                    key=lambda e: priority_order.get(e.severity, 99)
                )

        report.errors_remaining = len(remaining_errors)

        if report.errors_remaining == 0:
            report.status = FixStatus.FIXED
        elif report.errors_fixed > 0:
            report.status = FixStatus.PARTIAL
        elif iteration >= MAX_FIX_ITERATIONS:
            report.status = FixStatus.MAX_ITERATIONS
        else:
            report.status = FixStatus.FAILED

        # I4-FIX: Classify fix severity for fast-path routing
        report.fix_severity = _classify_fix_severity(report.attempts)

        output = {
            "status": report.status.value,
            "iterations": report.iterations,
            "errors_received": report.errors_received,
            "errors_fixed": report.errors_fixed,
            "errors_remaining": report.errors_remaining,
            "files_modified": report.files_modified,
            "fix_severity": report.fix_severity,
            "attempts": [
                {
                    "iteration": a.iteration,
                    "error_file": a.error.file_path,
                    "error_type": a.error.error_type,
                    "model_used": a.model_used,
                    "fix_applied": a.fix_applied,
                    "success": a.success,
                    "diff_mode": a.diff_mode,
                }
                for a in report.attempts
            ],
        }

        await store_output(self, pipeline_run_id, output)

        logger.info(
            "fixer_complete",
            status=report.status.value,
            iterations=report.iterations,
            fixed=report.errors_fixed,
            remaining=report.errors_remaining,
            files_modified=report.files_modified,
        )

        # R8-FIX: Return COMPLETED for both FIXED and PARTIAL statuses.
        # PARTIAL means some errors were fixed but others remain — the pipeline's
        # fix-retest loop (line 852-870) checks _has_errors_to_fix() and rewinds
        # to QUALITY_REVIEW. If we return FAILED here, the failure handler (line 814)
        # fires FIRST, sets run.status=FAILED, and breaks — the fix-retest check
        # is never reached, making the entire loop dead code.
        fixer_completed = report.status in (FixStatus.FIXED, FixStatus.PARTIAL)
        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.COMPLETED if fixer_completed else AgentStatus.FAILED,
            output=output,
        )

    # ── Fix Attempt ────────────────────────────────────────────────

    async def _attempt_fix(
        self,
        iteration: int,
        error: SanitizedError,
        contract: dict[str, Any],
        context: dict[str, Any],
        model_override: str,
        enable_thinking: bool,
        pipeline_run_id: str = "",
    ) -> FixAttempt:
        """Attempt to fix a single error using the agentic tool loop."""
        # Build error report for read_error_report tool
        error_report = {
            "file_path": error.file_path,
            "line_number": error.line_number,
            "error_type": error.error_type,
            "error_message": error.error_message,
            "code_snippet": error.code_snippet,
            "severity": error.severity,
            "source_agent": error.source_agent,
        }

        handler = FixerToolHandler(
            context=context,
            error=error,
            error_report=error_report,
            pipeline_run_id=pipeline_run_id,
        )

        # Original file content (for before/after comparison)
        file_before = handler._read_file(error.file_path)

        system_prompt = "\n".join([
            "You are the Fixer agent at NexSidi. Fix code errors using your tools.",
            "",
            "Workflow:",
            "1. Call read_error_report to get error details",
            "2. Call read_file to get the current file content",
            "3. For unfamiliar errors: call search_solution FIRST to find the right fix",
            "4. For ImportError/ModuleNotFoundError: call check_imports to find unresolved imports",
            "5. Apply the fix using ONE of these approaches:",
            "   a. PREFERRED: call apply_diff with a targeted unified diff patch",
            "   b. FALLBACK: call write_file with complete content (only for new files or >50% changes)",
            "6. For Python files: call validate_syntax to confirm fix is valid",
            "7. Call report_complete with a brief description and validation result",
            "",
            "Tools available (in priority order):",
            "- read_error_report: get structured error details",
            "- read_file: read current file content",
            "- search_replace: apply search/replace operations (PREFERRED for targeted fixes)",
            "- apply_diff: apply a unified diff patch (fallback if search_replace doesn't fit)",
            "- write_file: write complete file (LAST RESORT — only for new files or >50% changes)",
            "- validate_syntax: check Python syntax after fixing",
            "- check_imports: verify all Python imports resolve correctly",
            "- search_solution: search web for error solutions (use for unfamiliar errors)",
            "- report_complete: signal the fix is done",
            "",
            "PREFERRED — Use search_replace for targeted fixes:",
            "Provide the exact text to find and its replacement.",
            "Include 2-3 surrounding context lines for unique matching.",
            "Example: search_replace(path='app/models.py', operations=[",
            '  {"search": "class User(Base):\\n    email = Column(String)",',
            '   "replace": "class User(Base):\\n    email = Column(String, unique=True)"}',
            "])",
            "",
            "FALLBACK — Use apply_diff when unified diff format is clearer:",
            "```",
            "--- a/app/models.py",
            "+++ b/app/models.py",
            "@@ -25,3 +25,4 @@",
            " class User(Base):",
            "-    email = Column(String)",
            "+    email = Column(String, unique=True, index=True)",
            "+    email_verified = Column(Boolean, default=False)",
            "     name = Column(String(100))",
            "```",
            "",
            "write_file replaces the ENTIRE file — use only when necessary.",
            "",
            "Rules:",
            "- Make MINIMUM change needed to fix the error",
            "- Do NOT refactor surrounding code",
            "- Do NOT add new features",
            "- Preserve ALL existing functionality",
            "- Match the existing code style exactly",
            "- For ImportError: use check_imports to diagnose, then fix the import path or add the dependency",
            "- For unknown errors: use search_solution before guessing",
        ])

        user_message = (
            f"Fix this error in {error.file_path}: {error.error_type}\n"
            f"Call read_error_report first, then read_file, then write_file with the fix."
        )

        # AUDIT-FIX: Use a lightweight proxy instead of mutating self.default_model.
        # The Fixer singleton is shared across concurrent pipeline runs. Mutating
        # self.default_model causes a race: Pipeline A sets "gemini-pro", Pipeline B
        # sets "sonnet", Pipeline A reads "sonnet". The proxy is stack-local, so
        # concurrent pipelines get their own model override without interference.
        class _ModelProxy:
            """Stack-local proxy to avoid mutating the shared Fixer singleton."""
            def __init__(self, agent, model_key):
                self.name = agent.name
                self.display_name = agent.display_name
                self.default_complexity = agent.default_complexity
                self.default_model = resolve_model_override(model_key)
                self.tools = agent.tools

        proxy = _ModelProxy(self, model_override)

        try:
            response = await call_ai_with_tools(
                agent=proxy,
                messages=[{"role": "user", "content": user_message}],
                system_prompt=system_prompt,
                task_type="general",
                tool_handler=handler,
                max_tool_rounds=8,  # Allow up to 8 rounds for fix + validate + retry
            )
        except Exception as exc:
            from app.services.ai_router import _sanitize_error
            safe_err = _sanitize_error(exc)
            logger.error("fix_attempt_failed", iteration=iteration, error=safe_err)
            return FixAttempt(
                iteration=iteration,
                error=error,
                model_used=model_override,
                fix_applied=f"Fix attempt failed: {safe_err}",
                file_path=error.file_path,
                file_content_before=file_before,
                file_content_after="",
                success=False,
            )

        # COST-AGG-FIX: Record this fix-attempt's cost to the shared pipeline tracker.
        if pipeline_run_id:
            try:
                from app.services.pipeline import get_run_cost_tracker
                tracker = get_run_cost_tracker(pipeline_run_id)
                if tracker is not None:
                    await tracker.record(response, agent_name=self.name, model_key="high")
            except Exception:
                pass  # Cost tracking is non-fatal

        # Check results
        fixed_path = error.file_path
        raw_content = handler._written_files.get(fixed_path, "")

        # ROUND12-FIX: Strip markdown fences the LLM may wrap content in.
        # Guard with len(parts) > 1 to avoid IndexError on bare ``` lines.
        if raw_content.startswith("```"):
            parts = raw_content.split("\n", 1)
            if len(parts) > 1:
                fixed_content = parts[1].rsplit("```", 1)[0]
            else:
                fixed_content = ""
        else:
            fixed_content = raw_content

        if not fixed_content or len(fixed_content) < 10:
            return FixAttempt(
                iteration=iteration,
                error=error,
                model_used=model_override,
                fix_applied="No fix written",
                file_path=fixed_path,
                file_content_before=file_before,
                file_content_after="",
                success=False,
            )

        if fixed_content == file_before:
            return FixAttempt(
                iteration=iteration,
                error=error,
                model_used=model_override,
                fix_applied="No changes made",
                file_path=fixed_path,
                file_content_before=file_before,
                file_content_after=fixed_content,
                success=False,
            )

        success = handler._complete and (
            not fixed_path.endswith(".py") or handler._validation_passed
        )

        return FixAttempt(
            iteration=iteration,
            error=error,
            model_used=model_override,
            fix_applied=handler._fix_description or f"Fixed {error.error_type}",
            file_path=fixed_path,
            file_content_before=file_before,
            file_content_after=fixed_content,
            success=success,
            diff_mode="apply_diff" if handler._used_diff else "write_file",
        )

    # ── Error Collection ───────────────────────────────────────────

    def _collect_errors(self, context: dict[str, Any]) -> list[SanitizedError]:
        """Collect and sanitize errors from quality gates + test results.

        MUTATION-FIX: Creates shallow copies of finding dicts before adding
        ``source_agent`` to avoid mutating the shared pipeline context.
        """
        errors: list[SanitizedError] = []

        # From Karan (security findings)
        karan_output = context.get("karan", {})
        if isinstance(karan_output, dict):
            for finding in karan_output.get("findings", []):
                if finding.get("severity") in ("critical", "high"):
                    enriched = {**finding, "source_agent": "karan"}
                    errors.append(SanitizedError.from_finding(enriched))

        # From Navya (logic findings)
        navya_output = context.get("navya", {})
        if isinstance(navya_output, dict):
            for finding in navya_output.get("findings", []):
                if finding.get("severity") == "error":
                    enriched = {**finding, "source_agent": "navya"}
                    errors.append(SanitizedError.from_finding(enriched))

        # From Deepika (perf findings — only critical)
        deepika_output = context.get("deepika", {})
        if isinstance(deepika_output, dict):
            for finding in deepika_output.get("findings", []):
                if finding.get("severity") == "critical":
                    enriched = {**finding, "source_agent": "deepika"}
                    errors.append(SanitizedError.from_finding(enriched))

        # From Aarav (test failures)
        aarav_output = context.get("aarav", {})
        if isinstance(aarav_output, dict):
            for phase_result in aarav_output.get("phase_results", []):
                for error in phase_result.get("errors", []):
                    errors.append(SanitizedError.from_test_error(error))

        return errors

    def _get_file_content(self, context: dict[str, Any], file_path: str) -> str:
        """Get current file content from context."""
        for agent_name in ("shubham", "aanya"):
            agent_output = context.get(agent_name, {})
            if isinstance(agent_output, dict):
                content = agent_output.get("file_contents", {}).get(file_path, "")
                if content:
                    return content
        return ""

    def _update_file_in_context(
        self,
        context: dict[str, Any],
        file_path: str,
        new_content: str,
    ) -> None:
        """Update a file's content in the pipeline context after fixing.

        MUTATION-FIX: Creates a shallow copy of the agent output dict and
        file_contents dict before modifying, so the original cached output
        is preserved. The context dict itself is per-pipeline-run so
        updating the top-level key is safe.
        """
        for agent_name in ("shubham", "aanya"):
            agent_output = context.get(agent_name, {})
            if isinstance(agent_output, dict):
                file_contents = agent_output.get("file_contents", {})
                if file_path in file_contents:
                    # Copy-on-write: replace the whole agent output to
                    # avoid mutating the original dict stored in context engine
                    new_file_contents = {**file_contents, file_path: new_content}
                    context[agent_name] = {**agent_output, "file_contents": new_file_contents}
                    return


# Register the agent
_fixer = Fixer()
register_agent(_fixer)
