"""Documentation Agent: generates API docs, user guide, and README.

Runs post-delivery to create comprehensive project documentation:
1. API documentation from OpenAPI spec + route analysis
2. User guide from architecture contract + UI design
3. README with setup instructions, tech stack, and deployment info

Uses template-based generation from structured pipeline context data.
No AI calls — pure string templating from contract + generated file metadata.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.agents.base import (
    AgentResult,
    AgentStatus,
    ToolDefinition,
    WEB_SEARCH_TOOL,
    WEB_SCRAPE_TOOL,
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


# AUDIT-FIX: Escape curly braces in values before str.format().
# Prevents KeyError/ValueError when project names or endpoint paths
# contain { or } (e.g., "My {Cool} Project", "/users/{id}").
def _safe(val: str) -> str:
    return str(val).replace("{", "{{").replace("}", "}}")


# ── Documentation Templates ──────────────────────────────────────

README_SKELETON = """\
# {project_name}

{description}

## Tech Stack

- **Backend**: {backend_stack}
- **Frontend**: {frontend_stack}
- **Database**: {database_stack}
- **Deployment**: {deploy_provider}

## Getting Started

### Prerequisites

{prerequisites}

### Installation

```bash
{install_steps}
```

### Running Locally

```bash
{run_steps}
```

## API Documentation

{api_summary}

## Project Structure

```
{project_structure}
```

## Deployment

{deploy_info}

## License

{license_text}
"""

API_DOC_TEMPLATE = """\
# API Reference: {project_name}

Base URL: `{base_url}`

## Authentication

All endpoints require Bearer token authentication unless marked as public.

{endpoints_doc}
"""


WRITE_FILE_TOOL = ToolDefinition(
    name="write_file",
    description=(
        "Write a documentation file. Use this to create or overwrite "
        "generated documentation files (README.md, API docs, setup guides, etc.)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path relative to project root (e.g., 'README.md', 'docs/API_REFERENCE.md').",
            },
            "content": {
                "type": "string",
                "description": "The full content to write to the file.",
            },
        },
        "required": ["path", "content"],
    },
)

READ_FILE_TOOL = ToolDefinition(
    name="read_file",
    description=(
        "Read a generated code file from the pipeline context. Use this to "
        "inspect generated source code when writing documentation that needs "
        "to reference actual implementation details."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path to read (e.g., 'backend/app/main.py').",
            },
        },
        "required": ["path"],
    },
)


class DocsToolHandler:
    """Handles tool calls for DocsAgent's agentic documentation loop."""

    def __init__(self, context: dict[str, Any]) -> None:
        self._context = context
        self._documents: dict[str, str] = {}
        self._complete: bool = False  # Signals call_ai_with_tools to stop

    async def __call__(self, tool_name: str, tool_input: dict) -> str:
        # Delegate web tools first
        web_result = await handle_web_tool(tool_name, tool_input)
        if web_result is not None:
            return web_result

        if tool_name == "write_file":
            return self._write_file(tool_input["path"], tool_input["content"])
        elif tool_name == "read_file":
            return self._read_file(tool_input["path"])
        else:
            return f"Unknown tool: {tool_name}"

    def _write_file(self, path: str, content: str) -> str:
        """Write a documentation file to the output documents dict."""
        self._documents[path] = content
        return f"File written: {path} ({len(content)} chars)"

    def _read_file(self, path: str) -> str:
        """Read a generated code file from the pipeline context."""
        # Check all agents' file_contents for the requested path
        for agent_name in ("shubham", "aanya", "dhruv"):
            agent_output = self._context.get(agent_name, {})
            file_contents = agent_output.get("file_contents", {})
            if path in file_contents:
                content = file_contents[path]
                # Cap at 5000 chars to avoid token bloat
                if len(content) > 5000:
                    return content[:5000] + "\n... [truncated]"
                return content

        # Also check documents already written in this session
        if path in self._documents:
            return self._documents[path]

        return f"File not found: {path}"


class DocsAgent:
    """Documentation Agent — generates API docs, user guide, README."""

    name = "docs_agent"
    display_name = "Documentation Agent"
    default_complexity = TaskComplexity.MEDIUM
    default_model: str | None = None

    @property
    def tools(self) -> list[ToolDefinition]:
        """Tools for documentation generation with web research capabilities."""
        return [
            WEB_SEARCH_TOOL,
            WEB_SCRAPE_TOOL,
            WRITE_FILE_TOOL,
            READ_FILE_TOOL,
        ]

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
        """Generate project documentation from pipeline context."""
        # Check inbox for messages from other agents (esp. AUTHORITY directives)
        inbox_messages = await check_inbox(self.name, pipeline_run_id)
        inbox_context = format_inbox_for_prompt(inbox_messages)

        contract = context.get("vikram", {}).get("contract", {})
        if not contract:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error="No architecture contract — cannot generate docs",
            )

        project_name = contract.get("project_name", "Project")

        # Generate base documents from templates (preserved from original)
        readme = self._generate_readme(contract, context)
        api_docs = self._generate_api_docs(contract, context)
        setup_guide = self._generate_setup_guide(contract, context)

        # Initialize tool handler with template-generated docs as starting point
        handler = DocsToolHandler(context)
        handler._documents = {
            "README.md": readme,
            "docs/API_REFERENCE.md": api_docs,
            "docs/SETUP_GUIDE.md": setup_guide,
        }

        # Build system prompt for AI-enhanced documentation
        import json as _json_mod
        tech_stack = contract.get("tech_stack", {})
        endpoints = contract.get("api", {}).get("endpoints", [])
        endpoint_summary = _json_mod.dumps(endpoints[:10], default=str)[:2000] if endpoints else "[]"

        system_prompt = (
            "You are the Documentation Agent at NexSidi. Your job is to generate "
            "comprehensive, accurate project documentation.\n\n"
            "Template-generated base documents have already been written to:\n"
            "- README.md\n"
            "- docs/API_REFERENCE.md\n"
            "- docs/SETUP_GUIDE.md\n\n"
            "## Your capabilities:\n"
            "- **web_search**: Research best practices, framework docs, or library usage\n"
            "- **web_scrape**: Read specific documentation pages found via search\n"
            "- **read_file**: Inspect generated source code files for accuracy\n"
            "- **write_file**: Update or create documentation files\n\n"
            "## Your workflow:\n"
            "1. Review the base documents already generated\n"
            "2. Optionally use web_search to research framework-specific setup or best practices\n"
            "3. Optionally use read_file to inspect generated code for accuracy\n"
            "4. Use write_file to improve or add documentation as needed\n"
            "5. When satisfied with all docs, stop calling tools\n\n"
            "Focus on accuracy and completeness. Keep existing template content if it's correct."
        )

        user_content = (
            f"Project: {project_name}\n"
            f"Tech Stack: {_json_mod.dumps(tech_stack, default=str)[:1000]}\n"
            f"Endpoints ({len(endpoints)} total): {endpoint_summary}\n\n"
            "Base documentation has been generated from templates. "
            "Review and enhance the docs. Use web_search if you need to research "
            "framework-specific setup instructions or best practices. "
            "Use read_file to check generated code for accuracy in API docs."
        )

        # Inject inbox context if present
        if inbox_context:
            user_content = f"{inbox_context}\n\n{user_content}"

        try:
            response = await call_ai_with_tools(
                agent=self,
                messages=[{"role": "user", "content": user_content}],
                system_prompt=system_prompt,
                task_type="general",
                tool_handler=handler,
                max_tool_rounds=10,
            )
        except Exception as exc:
            # Fallback to template-generated docs if AI call fails
            from app.services.ai_router import _sanitize_error
            logger.warning(
                "docs_ai_enhancement_failed",
                error=_sanitize_error(exc),
                fallback="using template docs",
            )

        # Use handler's documents (may have been enhanced by AI, or still template originals)
        documents = handler._documents

        output = {
            "documents": documents,
            "total_docs": len(documents),
            "project_name": project_name,
        }

        await store_output(self, pipeline_run_id, output)

        logger.info(
            "docs_generated",
            project=project_name,
            total_docs=len(documents),
        )

        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.COMPLETED,
            output=output,
        )

    def _generate_readme(self, contract: dict[str, Any], context: dict[str, Any]) -> str:
        """Generate README from contract + context."""
        project_name = contract.get("project_name", "Project")
        tech_stack = contract.get("tech_stack", {})
        deploy = context.get("pranav", {})

        # Derive tech stack info
        backend_stack = tech_stack.get("backend", "FastAPI + Python 3.12")
        frontend_stack = tech_stack.get("frontend", "Next.js 15 + React 19")
        database_stack = tech_stack.get("database", "PostgreSQL 16")
        deploy_provider = deploy.get("provider", "Railway")

        # Derive endpoints summary
        endpoints = contract.get("api", {}).get("endpoints", [])
        api_summary = f"See `docs/API_REFERENCE.md` for full API documentation ({len(endpoints)} endpoints)."

        # Project structure from generated files
        backend_files = list(context.get("shubham", {}).get("file_contents", {}).keys())
        frontend_files = list(context.get("aanya", {}).get("file_contents", {}).keys())
        all_paths = sorted(backend_files + frontend_files)
        project_structure = "\n".join(all_paths[:20])
        if len(all_paths) > 20:
            project_structure += f"\n... and {len(all_paths) - 20} more files"

        return README_SKELETON.format(
            project_name=_safe(project_name),
            description=_safe(contract.get("description", f"{project_name} — built with NexSidi")),
            backend_stack=backend_stack,
            frontend_stack=frontend_stack,
            database_stack=database_stack,
            deploy_provider=deploy_provider,
            prerequisites="- Docker & Docker Compose (recommended)\n- Python 3.12+ (manual setup)\n- Node.js 20+ (manual setup)\n- PostgreSQL 16 (manual setup)",
            install_steps=(
                "## Quick Start (Docker — recommended)\n"
                "```bash\n"
                "git clone <repo-url>\n"
                "cd " + project_name.lower().replace(" ", "-") + "\n"
                "docker-compose up\n"
                "# App runs at http://localhost:8000 (backend) + http://localhost:3000 (frontend)\n"
                "```\n\n"
                "## Manual Setup (for development)\n"
                "### Backend:\n"
                "```bash\n"
                "cp backend/.env.example backend/.env  # Edit with your values\n"
                "cd backend && pip install -r requirements.txt\n"
                "alembic upgrade head  # Run migrations\n"
                "uvicorn app.main:app --reload\n"
                "```\n"
                "### Frontend:\n"
                "```bash\n"
                "cp frontend/.env.local.example frontend/.env.local  # Edit API URL\n"
                "cd frontend && npm install && npm run dev\n"
                "```"
            ),
            run_steps="# Using Docker (recommended):\ndocker-compose up\n\n# Manual:\n# Backend: uvicorn app.main:app --reload\n# Frontend: npm run dev",
            api_summary=api_summary,
            project_structure=_safe(project_structure),
            deploy_info=f"Deployed via {deploy_provider}. See deployment config in `/deploy` directory.",
            license_text="MIT License",
        )

    def _generate_api_docs(self, contract: dict[str, Any], context: dict[str, Any]) -> str:
        """Generate API reference from contract endpoints."""
        project_name = contract.get("project_name", "Project")
        endpoints = contract.get("api", {}).get("endpoints", [])

        sections: list[str] = []
        for ep in endpoints:
            method = ep.get("method", "GET").upper()
            path = ep.get("path", "/")
            description = ep.get("description", "")
            auth = "Requires authentication" if ep.get("auth_required", True) else "Public"

            sections.append(
                f"### `{method} {path}`\n\n"
                f"{description}\n\n"
                f"**Auth**: {auth}\n"
            )

        # AUDIT-FIX: Escape curly braces in all values before .format().
        # Endpoint paths like /users/{id} contain { and } which crash str.format()
        # with KeyError. This is the common case for any real API project.
        endpoints_doc = "\n---\n\n".join(sections) if sections else "No endpoints defined."
        return API_DOC_TEMPLATE.format(
            project_name=_safe(project_name),
            base_url="/api/v1",
            endpoints_doc=_safe(endpoints_doc),
        )

    def _generate_setup_guide(self, contract: dict[str, Any], context: dict[str, Any]) -> str:
        """Generate setup/development guide."""
        project_name = contract.get("project_name", "Project")
        tech_stack = contract.get("tech_stack", {})
        db = contract.get("database", {})
        tables = db.get("tables", [])

        lines = [
            f"# Setup Guide: {project_name}",
            "",
            "## Prerequisites",
            "",
            "- Python 3.12+",
            "- Node.js 20+ (LTS)",
            "- PostgreSQL 16",
            "- Docker & Docker Compose",
            "",
            "## Database Setup",
            "",
            f"This project uses {len(tables)} database tables.",
            "",
            "```bash",
            "# Create the database",
            f"createdb {project_name.lower().replace(' ', '_')}",
            "",
            "# Run migrations",
            "alembic upgrade head",
            "```",
            "",
            "## Environment Variables",
            "",
            "```env",
            "DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/db",
            "JWT_SECRET_KEY=your-secret-key-here",
            "ENVIRONMENT=development",
            "```",
            "",
            "## Running Tests",
            "",
            "```bash",
            "pytest tests/ -v",
            "```",
        ]

        return "\n".join(lines)


# Register
_docs_agent = DocsAgent()
register_agent(_docs_agent)
