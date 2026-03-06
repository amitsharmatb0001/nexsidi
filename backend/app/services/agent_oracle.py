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

    Uses STRUCTURED FIELD EXTRACTION (dict key lookups and file path patterns)
    instead of keyword matching. This is more reliable — "schema" won't match
    the wrong section, and file lookups use path patterns not content keywords.
    """
    question_lower = question.lower()

    # ── Vikram (architect) ──────────────────────────────────────────
    if agent_name == "vikram":
        contract = output.get("contract", {})
        if not contract:
            return _truncate(json.dumps(output, indent=2))

        # Structured field mapping: question topic → contract key
        _TOPIC_TO_KEY: dict[str, tuple[str, str]] = {
            "database": ("database", "Database schema"),
            "table": ("database", "Database schema"),
            "column": ("database", "Database schema"),
            "schema": ("database", "Database schema"),
            "db": ("database", "Database schema"),
            "endpoint": ("api", "API contract"),
            "api": ("api", "API contract"),
            "route": ("api", "API contract"),
            "rest": ("api", "API contract"),
            "page": ("frontend", "Frontend contract"),
            "frontend": ("frontend", "Frontend contract"),
            "component": ("frontend", "Frontend contract"),
            "ui": ("frontend", "Frontend contract"),
            "tech": ("tech_stack", "Tech stack"),
            "stack": ("tech_stack", "Tech stack"),
            "framework": ("tech_stack", "Tech stack"),
            "security": ("security", "Security contract"),
            "auth": ("security", "Security contract"),
            "cors": ("security", "Security contract"),
        }

        # Find the first matching topic
        for word, (key, label) in _TOPIC_TO_KEY.items():
            if word in question_lower:
                section = contract.get(key, {})
                if section:
                    return _truncate(f"{label}:\n{json.dumps(section, indent=2)}")

        # Default: structured summary with actual counts
        summary = {
            "project_name": contract.get("project_name"),
            "tech_stack": contract.get("tech_stack"),
            "tables": [t.get("name") for t in contract.get("database", {}).get("tables", [])],
            "endpoint_count": len(contract.get("api", {}).get("endpoints", [])),
            "pages": [p.get("name", p.get("title")) for p in contract.get("frontend", {}).get("pages", [])],
        }
        return _truncate(
            f"Architecture contract summary:\n{json.dumps(summary, indent=2)}\n\n"
            f"Available sections: database, api, frontend, tech_stack, security"
        )

    # ── Shubham / Aanya (code generators) ──────────────────────────
    if agent_name in ("shubham", "aanya"):
        file_contents = output.get("file_contents", {})
        label = "Backend" if agent_name == "shubham" else "Frontend"
        if not file_contents:
            return f"{label} files not yet generated."

        files_list = list(file_contents.keys())

        # 1. Exact file path match: if the question mentions a specific file
        for path in files_list:
            filename = path.split("/")[-1].lower()
            if filename in question_lower or path.lower() in question_lower:
                content = file_contents[path]
                exports = _extract_exports(path, content)
                export_str = f"\nExports: {', '.join(exports)}" if exports else ""
                return _truncate(f"File {path}:{export_str}\n```\n{content}\n```")

        # 2. Path pattern matching: use directory/filename structure, not content keywords
        _PATH_PATTERNS: dict[str, list[str]] = {
            "model": ["/models/", "models.py", "/schemas/", "schemas.py"],
            "schema": ["/models/", "models.py", "/schemas/", "schemas.py"],
            "route": ["/routers/", "/routes/", "router.py", "routes.py", "urls.py"],
            "endpoint": ["/routers/", "/routes/", "router.py", "routes.py"],
            "api": ["/routers/", "/api/", "api.py", "service.py", "services/"],
            "auth": ["/auth", "auth.py", "security.py", "jwt"],
            "component": ["/components/", "Component", ".tsx", ".jsx"],
            "page": ["/pages/", "/views/", "Page", "View"],
            "config": ["config.py", "settings.py", ".env", "config/"],
            "test": ["/tests/", "test_", "_test.py", ".test."],
            "middleware": ["/middleware", "middleware.py"],
            "database": ["/db/", "database.py", "db.py", "connection"],
        }

        for topic, patterns in _PATH_PATTERNS.items():
            if topic in question_lower:
                matched = [p for p in files_list if any(pat in p.lower() for pat in patterns)]
                if matched:
                    results = []
                    for mp in matched[:3]:  # Max 3 files
                        exports = _extract_exports(mp, file_contents[mp])
                        export_str = f" — exports: {', '.join(exports[:8])}" if exports else ""
                        results.append(f"  {mp} ({file_contents[mp].count(chr(10))+1} lines){export_str}")
                    file_detail = "\n".join(results)
                    # Return content of first match
                    return _truncate(
                        f"{label} files matching '{topic}':\n{file_detail}\n\n"
                        f"Content of {matched[0]}:\n```\n{file_contents[matched[0]]}\n```"
                    )

        # 3. Default: file list with exports
        lines = [f"{label} files ({len(files_list)} total):"]
        for p in files_list:
            exports = _extract_exports(p, file_contents[p])
            export_str = f" — {', '.join(exports[:5])}" if exports else ""
            lines.append(f"  {p}{export_str}")
        return _truncate("\n".join(lines))

    # ── Aarav (test results) ────────────────────────────────────────
    if agent_name == "aarav":
        is_sim = output.get("is_simulation_sandbox", False)
        summary = {
            "all_passed": output.get("all_passed"),
            "is_simulation": is_sim,
            "total_tests": output.get("total_tests", 0),
            "total_passed": output.get("total_passed", 0),
            "total_failed": output.get("total_failed", 0),
            "phase_results": [
                {
                    "phase": p.get("phase"),
                    "status": p.get("status"),
                    "error_count": len(p.get("errors", [])),
                }
                for p in output.get("phase_results", [])
            ],
        }
        # Include AI analysis if available
        ai_analysis = output.get("ai_analysis", {})
        if ai_analysis:
            summary["ai_analysis"] = ai_analysis
        sim_note = " (SIMULATED — no real tests ran)" if is_sim else ""
        return _truncate(f"Test results{sim_note}:\n{json.dumps(summary, indent=2)}")

    # ── Dhruv (database artifacts) ─────────────────────────────────
    if agent_name == "dhruv":
        written = output.get("written_files", {})
        if written:
            lines = [f"Database artifacts ({len(written)} files):"]
            for path in sorted(written.keys()):
                lc = written[path].count("\n") + 1 if written[path] else 0
                lines.append(f"  {path} ({lc} lines)")
            return _truncate("\n".join(lines))
        return _truncate(json.dumps(output, indent=2))

    # ── Karan (security findings) ──────────────────────────────────
    if agent_name == "karan":
        findings = output.get("findings", [])
        return _truncate(
            f"Security scan: {output.get('files_scanned', 0)} files scanned, "
            f"{len(findings)} findings, "
            f"{output.get('critical_count', 0)} critical, "
            f"{output.get('high_count', 0)} high\n"
            f"Passed: {output.get('passed', 'unknown')}\n"
            f"Findings:\n{json.dumps(findings[:10], indent=2)}"
        )

    # ── Generic fallback ────────────────────────────────────────────
    return _truncate(json.dumps(output, indent=2))


def _extract_exports(path: str, content: str) -> list[str]:
    """Extract top-level class/function names from Python/TS files."""
    if not content:
        return []
    if path.endswith(".py"):
        try:
            import ast
            tree = ast.parse(content)
            names: list[str] = []
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.append(node.name)
            return names
        except SyntaxError:
            return []
    if path.endswith((".ts", ".tsx", ".js", ".jsx")):
        # Simple regex extraction for JS/TS exports
        import re
        exports = re.findall(
            r'export\s+(?:default\s+)?(?:class|function|const|let|var)\s+(\w+)',
            content,
        )
        return exports[:10]
    return []


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
