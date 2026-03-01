"""Documentation Agent: generates API docs, user guide, and README.

Runs post-delivery to create comprehensive project documentation:
1. API documentation from OpenAPI spec + route analysis
2. User guide from architecture contract + UI design
3. README with setup instructions, tech stack, and deployment info

Uses AI to generate human-readable documentation from structured data.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.agents.base import (
    AgentResult,
    AgentStatus,
    ToolDefinition,
    register_agent,
    run_agent,
    store_output,
)
from app.services.ai_router import TaskComplexity

logger = structlog.get_logger(__name__)


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


class DocsAgent:
    """Documentation Agent — generates API docs, user guide, README."""

    name = "docs_agent"
    display_name = "Documentation Agent"
    default_complexity = TaskComplexity.MEDIUM
    default_model: str | None = None

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

        self.register_tool(ToolDefinition(
            name="generate_readme",
            description="Generate a README.md for the project.",
            parameters={
                "type": "object",
                "properties": {
                    "format": {"type": "string", "enum": ["markdown", "rst"]},
                },
                "required": ["format"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="generate_api_docs",
            description="Generate API documentation from endpoints.",
            parameters={
                "type": "object",
                "properties": {},
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
        """Generate project documentation from pipeline context."""
        contract = context.get("vikram", {}).get("contract", {})
        if not contract:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error="No architecture contract — cannot generate docs",
            )

        project_name = contract.get("project_name", "Project")

        # Generate README
        readme = self._generate_readme(contract, context)

        # Generate API docs
        api_docs = self._generate_api_docs(contract, context)

        # Generate setup guide
        setup_guide = self._generate_setup_guide(contract, context)

        output = {
            "documents": {
                "README.md": readme,
                "docs/API_REFERENCE.md": api_docs,
                "docs/SETUP_GUIDE.md": setup_guide,
            },
            "total_docs": 3,
            "project_name": project_name,
        }

        await store_output(self, pipeline_run_id, output)

        logger.info(
            "docs_generated",
            project=project_name,
            total_docs=3,
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
            project_name=project_name,
            description=contract.get("description", f"{project_name} — built with NexSidi"),
            backend_stack=backend_stack,
            frontend_stack=frontend_stack,
            database_stack=database_stack,
            deploy_provider=deploy_provider,
            prerequisites="- Python 3.12+\n- Node.js 20+\n- PostgreSQL 16\n- Docker",
            install_steps="git clone <repo-url>\ncd " + project_name.lower().replace(" ", "-") + "\npip install -r requirements.txt\nnpm install",
            run_steps="# Backend\nuvicorn app.main:app --reload\n\n# Frontend\nnpm run dev",
            api_summary=api_summary,
            project_structure=project_structure,
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
            auth = "Requires authentication" if ep.get("auth", True) else "Public"

            sections.append(
                f"### `{method} {path}`\n\n"
                f"{description}\n\n"
                f"**Auth**: {auth}\n"
            )

        return API_DOC_TEMPLATE.format(
            project_name=project_name,
            base_url="/api/v1",
            endpoints_doc="\n---\n\n".join(sections) if sections else "No endpoints defined.",
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
            f"ENVIRONMENT={'development'}",
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
