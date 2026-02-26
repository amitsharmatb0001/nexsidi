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
    BaseAgent,
    ToolDefinition,
    register_agent,
)
from app.services.ai_router import TaskComplexity

logger = structlog.get_logger(__name__)


# ── Fixer Configuration ───────────────────────────────────────────

MAX_FIX_ITERATIONS: int = 5

# Model escalation by iteration number (AUDIT FIX #18)
ITERATION_MODEL_MAP: dict[int, tuple[str, bool]] = {
    # iteration -> (model_override, enable_thinking)
    1: ("gemini-2.5-pro", False),
    2: ("gemini-2.5-pro", False),
    3: ("claude-sonnet-4-6", False),
    4: ("claude-sonnet-4-6", False),
    5: ("claude-sonnet-4-6", True),   # Extended thinking on last attempt
}


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


# ── Fixer Agent ────────────────────────────────────────────────────


class Fixer(BaseAgent):
    """Autonomous Error Correction — reads errors, generates targeted fixes.

    Max 5 iterations with model escalation.
    """

    name = "fixer"
    display_name = "Fixer — Error Correction"
    default_complexity = TaskComplexity.HIGH

    def __init__(self) -> None:
        super().__init__()

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
            description="Write the fixed code to a file.",
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
            name="read_error_report",
            description="Read the structured error report from quality gates / testing.",
            parameters={
                "type": "object",
                "properties": {},
            },
        ))

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

            model_override, enable_thinking = ITERATION_MODEL_MAP.get(
                iteration, ("claude-sonnet-4-6", False)
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
            else:
                # If fix failed, try next error (don't re-attempt same one immediately)
                failed = remaining_errors.pop(0)
                remaining_errors.append(failed)

        report.errors_remaining = len(remaining_errors)

        if report.errors_remaining == 0:
            report.status = FixStatus.FIXED
        elif report.errors_fixed > 0:
            report.status = FixStatus.PARTIAL
        elif iteration >= MAX_FIX_ITERATIONS:
            report.status = FixStatus.MAX_ITERATIONS
        else:
            report.status = FixStatus.FAILED

        output = {
            "status": report.status.value,
            "iterations": report.iterations,
            "errors_received": report.errors_received,
            "errors_fixed": report.errors_fixed,
            "errors_remaining": report.errors_remaining,
            "files_modified": report.files_modified,
            "attempts": [
                {
                    "iteration": a.iteration,
                    "error_file": a.error.file_path,
                    "error_type": a.error.error_type,
                    "model_used": a.model_used,
                    "fix_applied": a.fix_applied,
                    "success": a.success,
                }
                for a in report.attempts
            ],
        }

        await self.store_output(pipeline_run_id, output)

        logger.info(
            "fixer_complete",
            status=report.status.value,
            iterations=report.iterations,
            fixed=report.errors_fixed,
            remaining=report.errors_remaining,
            files_modified=report.files_modified,
        )

        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.COMPLETED if report.status == FixStatus.FIXED else AgentStatus.FAILED,
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
    ) -> FixAttempt:
        """Attempt to fix a single error."""
        import orjson

        # Get the current file content
        file_content = self._get_file_content(context, error.file_path)

        # Build the fix prompt
        error_json = orjson.dumps({
            "file_path": error.file_path,
            "line_number": error.line_number,
            "error_type": error.error_type,
            "error_message": error.error_message,
            "severity": error.severity,
        }, option=orjson.OPT_INDENT_2).decode("utf-8")

        system_prompt = "\n".join([
            "You are the Fixer agent at NexSidi.",
            "You receive a structured error report and the file that needs fixing.",
            "Your job: apply ONE targeted fix to resolve the error.",
            "",
            "## Rules",
            "1. Output ONLY the complete fixed file content — no markdown, no explanation",
            "2. Make the MINIMUM change needed to fix the error",
            "3. Do NOT refactor surrounding code",
            "4. Do NOT add new features",
            "5. Do NOT use TODO, pass, ..., or NotImplementedError",
            "6. Preserve ALL existing functionality",
            "7. Match the existing code style exactly",
        ])

        user_message = "\n".join([
            f"## Error Report",
            f"```json\n{error_json}\n```",
            "",
            f"## Current File ({error.file_path})",
            f"```\n{file_content}\n```" if file_content else "File not found in context",
            "",
            "Fix this error. Output ONLY the complete corrected file.",
        ])

        try:
            # Temporarily override model for this call
            original_model = self.default_model
            self.default_model = model_override

            response = await self.call_ai(
                messages=[{"role": "user", "content": user_message}],
                system_prompt=system_prompt,
                task_type="general",
                temperature=0.1,
                enable_thinking=enable_thinking,
            )

            self.default_model = original_model

            fixed_content = response.content.strip()
            # Strip markdown code fences if present
            if fixed_content.startswith("```"):
                fixed_content = fixed_content.split("\n", 1)[1].rsplit("```", 1)[0]

            # Validate the fix produced actual content
            if len(fixed_content) < 10:
                return FixAttempt(
                    iteration=iteration,
                    error=error,
                    model_used=response.model_used,
                    fix_applied="Fix produced empty/minimal content",
                    file_path=error.file_path,
                    file_content_before=file_content,
                    file_content_after="",
                    success=False,
                )

            # Check that the fix actually changed something
            if fixed_content == file_content:
                return FixAttempt(
                    iteration=iteration,
                    error=error,
                    model_used=response.model_used,
                    fix_applied="No changes made — fix identical to original",
                    file_path=error.file_path,
                    file_content_before=file_content,
                    file_content_after=file_content,
                    success=False,
                )

            return FixAttempt(
                iteration=iteration,
                error=error,
                model_used=response.model_used,
                fix_applied=f"Applied fix for {error.error_type} at {error.file_path}",
                file_path=error.file_path,
                file_content_before=file_content,
                file_content_after=fixed_content,
                success=True,
            )

        except Exception as exc:
            logger.error("fix_attempt_failed", iteration=iteration, error=str(exc))
            return FixAttempt(
                iteration=iteration,
                error=error,
                model_used=model_override,
                fix_applied=f"Fix attempt failed: {exc}",
                file_path=error.file_path,
                file_content_before=file_content,
                file_content_after="",
                success=False,
            )

    # ── Error Collection ───────────────────────────────────────────

    def _collect_errors(self, context: dict[str, Any]) -> list[SanitizedError]:
        """Collect and sanitize errors from quality gates + test results."""
        errors: list[SanitizedError] = []

        # From Karan (security findings)
        karan_output = context.get("karan", {})
        if isinstance(karan_output, dict):
            for finding in karan_output.get("findings", []):
                if finding.get("severity") in ("critical", "high"):
                    finding["source_agent"] = "karan"
                    errors.append(SanitizedError.from_finding(finding))

        # From Navya (logic findings)
        navya_output = context.get("navya", {})
        if isinstance(navya_output, dict):
            for finding in navya_output.get("findings", []):
                if finding.get("severity") == "error":
                    finding["source_agent"] = "navya"
                    errors.append(SanitizedError.from_finding(finding))

        # From Deepika (perf findings — only critical)
        deepika_output = context.get("deepika", {})
        if isinstance(deepika_output, dict):
            for finding in deepika_output.get("findings", []):
                if finding.get("severity") == "critical":
                    finding["source_agent"] = "deepika"
                    errors.append(SanitizedError.from_finding(finding))

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
        """Update a file's content in the pipeline context after fixing."""
        for agent_name in ("shubham", "aanya"):
            agent_output = context.get(agent_name, {})
            if isinstance(agent_output, dict):
                file_contents = agent_output.get("file_contents", {})
                if file_path in file_contents:
                    file_contents[file_path] = new_content
                    return


# Register the agent
_fixer = Fixer()
register_agent(_fixer)
