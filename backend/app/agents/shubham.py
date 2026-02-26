"""Shubham — Backend Engineer: hybrid template + AI code generation.

Shubham generates backend code using a two-phase approach:
1. Template phase (ZERO AI): Dockerfile, docker-compose, requirements.txt,
   .env, database.py, config.py, main.py — all from Jinja2 templates
2. AI phase (dependency-ordered):
   models.py → schemas.py → security.py → routers/ → services/ →
   tests/ → seed_db.py

Each AI-generated file sees the ACTUAL output of previously generated
files (not descriptions — the real code), preventing hallucinated imports.

Supports: FastAPI (default), Django, Express, Go, Java
Security-critical code ALWAYS uses Sonnet 4.6 (AUDIT FIX #17).
"""

from __future__ import annotations

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

# Dependency-ordered file generation sequence for backend
BACKEND_GENERATION_ORDER: list[dict[str, str]] = [
    {"name": "models", "path": "backend/app/models.py", "task_type": "general",
     "description": "SQLAlchemy models from architecture contract tables"},
    {"name": "schemas", "path": "backend/app/schemas.py", "task_type": "general",
     "description": "Pydantic request/response schemas matching models"},
    {"name": "security", "path": "backend/app/security.py", "task_type": "auth_code",
     "description": "Auth middleware, password hashing, JWT handling"},
    {"name": "routers", "path": "backend/app/routers/", "task_type": "general",
     "description": "FastAPI route handlers using schemas and services"},
    {"name": "services", "path": "backend/app/services/", "task_type": "general",
     "description": "Business logic services called by routers"},
    {"name": "tests", "path": "backend/tests/", "task_type": "general",
     "description": "Pytest tests for all endpoints and services"},
    {"name": "seed_db", "path": "backend/scripts/seed.py", "task_type": "general",
     "description": "Database seed script for development"},
]


class Shubham(BaseAgent):
    """Backend Engineer — hybrid template + AI code generation."""

    name = "shubham"
    display_name = "Shubham — Backend Engineer"
    default_complexity = TaskComplexity.HIGH

    def __init__(self) -> None:
        super().__init__()

        self.register_tool(ToolDefinition(
            name="write_file",
            description="Write a generated code file to the project.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path."},
                    "content": {"type": "string", "description": "Complete file content."},
                    "language": {"type": "string", "enum": ["python", "sql", "yaml", "toml"]},
                },
                "required": ["path", "content"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="read_file",
            description="Read a previously generated file for context.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path to read."},
                },
                "required": ["path"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="read_contract",
            description="Read the architecture contract (single source of truth).",
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
        """Generate backend code from approved architecture contract.

        Phase 1: Template engine generates boilerplate (zero AI).
        Phase 2: AI generates business logic in dependency order.
        """
        vikram_output = context.get("vikram")
        if not vikram_output:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error="No architecture contract from Vikram",
            )

        contract = vikram_output.get("contract", {})
        dhruv_output = context.get("dhruv", {})
        generated_files: dict[str, str] = {}

        # ── Phase 1: Template scaffolding (ZERO AI) ──
        try:
            from app.engine.template_engine import get_template_engine

            engine = get_template_engine()
            template_files = engine.render_all(contract, categories=["backend", "config"])

            for f in template_files:
                generated_files[f.path] = f.content

            logger.info(
                "template_phase_complete",
                files_generated=len(template_files),
            )
        except Exception as exc:
            logger.warning("template_phase_failed", error=str(exc))

        # ── Phase 2: AI code generation (dependency-ordered) ──
        accumulated_code: dict[str, str] = {}

        for step in BACKEND_GENERATION_ORDER:
            system_prompt = self._build_generation_prompt(
                step=step,
                contract=contract,
                accumulated_code=accumulated_code,
                db_artifacts=dhruv_output.get("database_artifacts", ""),
            )

            try:
                response = await self.call_ai(
                    messages=[{
                        "role": "user",
                        "content": f"Generate the {step['description']} for this project.",
                    }],
                    system_prompt=system_prompt,
                    task_type=step["task_type"],
                    temperature=0.1,
                )

                accumulated_code[step["name"]] = response.content
                generated_files[step["path"]] = response.content

                logger.info(
                    "ai_generation_step",
                    step=step["name"],
                    model=response.model_used,
                    tokens=response.output_tokens,
                )

            except Exception as exc:
                logger.error("ai_generation_failed", step=step["name"], error=str(exc))
                accumulated_code[step["name"]] = f"# Generation failed: {exc}"

        output = {
            "generated_files": list(generated_files.keys()),
            "template_files": [f.path for f in template_files] if "template_files" in dir() else [],
            "ai_files": [s["path"] for s in BACKEND_GENERATION_ORDER],
            "file_count": len(generated_files),
            "generation_order": [s["name"] for s in BACKEND_GENERATION_ORDER],
        }

        await self.store_output(pipeline_run_id, output)

        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.COMPLETED,
            output=output,
        )

    def _build_generation_prompt(
        self,
        step: dict[str, str],
        contract: dict[str, Any],
        accumulated_code: dict[str, str],
        db_artifacts: str,
    ) -> str:
        """Build context-rich prompt for each generation step.

        Includes: contract + all previously generated files + anti-hallucination rules.
        """
        import orjson

        contract_json = orjson.dumps(contract, option=orjson.OPT_INDENT_2).decode("utf-8")

        prompt_parts = [
            "You are Shubham, the Backend Engineer at NexSidi.",
            f"Generate the {step['description']}.",
            "",
            "## Architecture Contract (SINGLE SOURCE OF TRUTH)",
            f"```json\n{contract_json}\n```",
            "",
        ]

        # Add database artifacts if available
        if db_artifacts and step["name"] in ("models", "schemas"):
            prompt_parts.extend([
                "## Database DDL (from Dhruv)",
                db_artifacts,
                "",
            ])

        # Add previously generated code for dependency context
        if accumulated_code:
            prompt_parts.append("## Previously Generated Files (REAL CODE — use exact names)")
            for name, code in accumulated_code.items():
                prompt_parts.append(f"\n### {name}\n```python\n{code}\n```")
            prompt_parts.append("")

        # Anti-hallucination rules
        prompt_parts.extend([
            "## MANDATORY RULES (NEVER VIOLATE)",
            "1. NEVER use 'pass', '# TODO', '...', or 'raise NotImplementedError'",
            "2. NEVER invent import paths — use ONLY names from contract + existing code above",
            "3. EVERY function must have a REAL, COMPLETE implementation",
            "4. Type hints on EVERY function (parameters + return types)",
            "5. Proper error handling with specific exceptions",
            "6. Match table/column names EXACTLY from the contract",
            "7. Match schema field names EXACTLY from models",
            "8. Output ONLY the code file — no markdown, no explanation",
        ])

        return "\n".join(prompt_parts)


# Register the agent
_shubham = Shubham()
register_agent(_shubham)
