"""Agent Oracle: context-based fallback for inter-agent queries.

When an agent calls ``ask_agent()`` via the AgentMessageBus, the target
agent may have already COMPLETED (since the pipeline is sequential). In that
case, the message bus times out — but the answer is already available in the
pipeline context!

This module provides a context-based lookup that reconstructs a natural
answer from the target agent's completed output. It is used as a FALLBACK
when the message bus times out or is unavailable.

Usage in tool handlers::

    from app.services.agent_oracle import query_agent_context

    answer = await query_agent_context(
        context=self._pipeline_context,
        target_agent="vikram",
        question="What are the database tables?",
    )

The oracle is intentionally simple: it returns the most relevant portion
of the target agent's stored output as a JSON/text string. For more complex
inter-agent communication (parallel agents, real-time Q&A), the full
AgentMessageBus pub-sub should be used.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# Maximum characters to return from context lookups
_MAX_ORACLE_CHARS = 4000


def query_agent_context(
    context: dict[str, Any],
    target_agent: str,
    question: str,
) -> str:
    """Look up a completed agent's output and return relevant content.

    This is a SYNCHRONOUS fallback for when the AgentMessageBus is not
    available or has timed out. It reads the already-completed output from
    the pipeline context dict and returns the most relevant portion.

    Args:
        context: The pipeline run context dict (``run.context``).
        target_agent: Name of the agent to query (e.g., ``"vikram"``).
        question: The question being asked (used for relevance hints).

    Returns:
        A string containing the relevant portion of the agent's output,
        or a helpful "not available" message if the agent hasn't run yet.
    """
    agent_output = context.get(target_agent)

    if agent_output is None:
        logger.debug(
            "oracle_agent_not_found",
            target=target_agent,
            question_preview=question[:100],
        )
        return (
            f"Agent '{target_agent}' has not yet completed in this pipeline. "
            f"Proceed with your best judgment."
        )

    if not isinstance(agent_output, dict):
        return f"Agent '{target_agent}' output is not a dict: {type(agent_output).__name__}"

    # Special-case well-known agents to return the most relevant section
    relevant = _extract_relevant(agent_output, target_agent, question)

    logger.debug(
        "oracle_lookup",
        target=target_agent,
        question_preview=question[:100],
        result_length=len(relevant),
    )
    return relevant


def _extract_relevant(
    output: dict[str, Any],
    agent_name: str,
    question: str,
) -> str:
    """Extract the most relevant portion of an agent's output for a question.

    Uses simple keyword matching to select the best section.
    """
    question_lower = question.lower()

    # ── Vikram (architect) ──────────────────────────────────────────
    if agent_name == "vikram":
        contract = output.get("contract", {})
        if not contract:
            return _truncate(json.dumps(output, indent=2))

        # Determine which section the question is about
        if any(w in question_lower for w in ("table", "database", "column", "schema", "db")):
            section = contract.get("database", {})
            return _truncate(f"Database schema:\n{json.dumps(section, indent=2)}")

        if any(w in question_lower for w in ("endpoint", "api", "route", "rest", "auth")):
            section = contract.get("api", {})
            return _truncate(f"API contract:\n{json.dumps(section, indent=2)}")

        if any(w in question_lower for w in ("page", "frontend", "component", "ui")):
            section = contract.get("frontend", {})
            return _truncate(f"Frontend contract:\n{json.dumps(section, indent=2)}")

        if any(w in question_lower for w in ("tech", "stack", "framework")):
            section = contract.get("tech_stack", {})
            return _truncate(f"Tech stack:\n{json.dumps(section, indent=2)}")

        if any(w in question_lower for w in ("security", "auth_method", "cors", "rate")):
            section = contract.get("security", {})
            return _truncate(f"Security contract:\n{json.dumps(section, indent=2)}")

        # Default: return a summary (not the full contract which may be huge)
        summary = {
            "project_name": contract.get("project_name"),
            "tech_stack": contract.get("tech_stack"),
            "table_count": len(contract.get("database", {}).get("tables", [])),
            "endpoint_count": len(contract.get("api", {}).get("endpoints", [])),
            "page_count": len(contract.get("frontend", {}).get("pages", [])),
        }
        return _truncate(
            f"Architecture contract summary:\n{json.dumps(summary, indent=2)}\n\n"
            f"Ask a more specific question about tables, endpoints, pages, or tech stack "
            f"to get detailed contract sections."
        )

    # ── Shubham (backend) ───────────────────────────────────────────
    if agent_name == "shubham":
        file_contents = output.get("file_contents", {})
        if not file_contents:
            return "Shubham has not yet generated backend files."

        files_list = list(file_contents.keys())

        # Try to find a specific file mentioned in the question
        for path in files_list:
            filename = path.split("/")[-1]
            if filename.lower() in question_lower or path.lower() in question_lower:
                content = file_contents[path]
                return _truncate(f"File {path}:\n```\n{content}\n```")

        # Topic-based file selection
        if any(w in question_lower for w in ("model", "schema", "database", "orm")):
            for path in files_list:
                if "model" in path.lower():
                    return _truncate(f"File {path}:\n```\n{file_contents[path]}\n```")

        if any(w in question_lower for w in ("endpoint", "route", "api")):
            for path in files_list:
                if "router" in path.lower() or "route" in path.lower():
                    return _truncate(f"File {path}:\n```\n{file_contents[path]}\n```")

        if any(w in question_lower for w in ("auth", "security", "jwt")):
            for path in files_list:
                if "auth" in path.lower() or "security" in path.lower():
                    return _truncate(f"File {path}:\n```\n{file_contents[path]}\n```")

        # Default: return file list
        return f"Backend files generated by Shubham:\n" + "\n".join(f"  - {p}" for p in files_list)

    # ── Aanya (frontend) ───────────────────────────────────────────
    if agent_name == "aanya":
        file_contents = output.get("file_contents", {})
        if not file_contents:
            return "Aanya has not yet generated frontend files."

        files_list = list(file_contents.keys())

        # Topic-based file selection
        if any(w in question_lower for w in ("component", "page", "ui")):
            for path in files_list:
                if "component" in path.lower() or "page" in path.lower():
                    return _truncate(f"File {path}:\n```\n{file_contents[path]}\n```")

        if any(w in question_lower for w in ("api", "endpoint", "fetch", "axios")):
            for path in files_list:
                if "api" in path.lower() or "service" in path.lower():
                    return _truncate(f"File {path}:\n```\n{file_contents[path]}\n```")

        return f"Frontend files generated by Aanya:\n" + "\n".join(f"  - {p}" for p in files_list)

    # ── Aarav (test results) ────────────────────────────────────────
    if agent_name == "aarav":
        summary = {
            "passed": output.get("passed"),
            "failed": output.get("failed"),
            "phase_results": [
                {
                    "phase": p.get("phase"),
                    "status": p.get("status"),
                    "error_count": len(p.get("errors", [])),
                }
                for p in output.get("phase_results", [])
            ],
        }
        return _truncate(f"Test results:\n{json.dumps(summary, indent=2)}")

    # ── Generic fallback ────────────────────────────────────────────
    # Return the output dict, truncated if too large
    return _truncate(json.dumps(output, indent=2))


def _truncate(text: str, max_chars: int = _MAX_ORACLE_CHARS) -> str:
    """Truncate text to max_chars with a note."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n... [truncated — {len(text)} total chars]"


def get_file_from_context(
    context: dict[str, Any],
    file_path: str,
) -> str | None:
    """Get a specific generated file from the pipeline context.

    Searches both Shubham (backend) and Aanya (frontend) outputs.

    Args:
        context: The pipeline run context dict.
        file_path: The file path to look up.

    Returns:
        The file content as a string, or None if not found.
    """
    for agent_name in ("shubham", "aanya"):
        agent_output = context.get(agent_name, {})
        if isinstance(agent_output, dict):
            file_contents = agent_output.get("file_contents", {})
            if file_path in file_contents:
                return file_contents[file_path]
    return None


def list_generated_files(context: dict[str, Any]) -> dict[str, list[str]]:
    """List all generated files from both Shubham and Aanya.

    Returns:
        Dict with keys "backend" and "frontend", each a list of file paths.
    """
    result: dict[str, list[str]] = {"backend": [], "frontend": []}

    shubham_out = context.get("shubham", {})
    if isinstance(shubham_out, dict):
        result["backend"] = list(shubham_out.get("file_contents", {}).keys())

    aanya_out = context.get("aanya", {})
    if isinstance(aanya_out, dict):
        result["frontend"] = list(aanya_out.get("file_contents", {}).keys())

    return result
