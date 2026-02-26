"""Dhruv — Database Architect: SQL DDL, migrations, seed data.

Reads Vikram's architecture contract and generates:
- CREATE TABLE statements (PostgreSQL DDL)
- Alembic migration scripts
- Indexing strategy
- Seed data for development
- Compliance tables (audit_log, consent_records) if flagged

All DDL is generated from the contract — never hallucinated.
Column types, constraints, and relationships come directly from
the architecture contract JSON.
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


class Dhruv(BaseAgent):
    """Database Architect — DDL generation from architecture contract."""

    name = "dhruv"
    display_name = "Dhruv — Database Architect"
    default_complexity = TaskComplexity.HIGH

    def __init__(self) -> None:
        super().__init__()

        self.register_tool(ToolDefinition(
            name="write_file",
            description="Write a file to the project output directory.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path."},
                    "content": {"type": "string", "description": "File content."},
                },
                "required": ["path", "content"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="generate_migration",
            description="Generate an Alembic migration script.",
            parameters={
                "type": "object",
                "properties": {
                    "migration_name": {"type": "string"},
                    "sql_up": {"type": "string", "description": "SQL for upgrade."},
                    "sql_down": {"type": "string", "description": "SQL for downgrade."},
                },
                "required": ["migration_name", "sql_up", "sql_down"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="generate_seed_data",
            description="Generate seed data SQL for development.",
            parameters={
                "type": "object",
                "properties": {
                    "table_name": {"type": "string"},
                    "rows": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["table_name", "rows"],
            },
        ))

    async def execute(
        self,
        pipeline_run_id: str,
        context: dict[str, Any],
    ) -> AgentResult:
        """Generate database artifacts from Vikram's architecture contract."""
        vikram_output = context.get("vikram")
        if not vikram_output:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error="No architecture contract from Vikram",
            )

        contract = vikram_output.get("contract", {})
        tables = contract.get("database", {}).get("tables", [])

        if not tables:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error="Architecture contract has no database tables",
            )

        # Build prompt with contract tables as input
        system_prompt = (
            "You are Dhruv, the Database Architect at NexSidi. "
            "Generate PostgreSQL DDL from the architecture contract.\n\n"
            "RULES:\n"
            "- Output CREATE TABLE statements in dependency order (referenced tables first)\n"
            "- Use UUID for all primary keys with DEFAULT gen_random_uuid()\n"
            "- Include created_at and updated_at TIMESTAMPTZ on every table\n"
            "- Add organization_id FK where specified in the contract\n"
            "- Create indexes on all foreign keys and frequently queried columns\n"
            "- Use appropriate PostgreSQL types: UUID, VARCHAR, TEXT, INTEGER, "
            "BOOLEAN, TIMESTAMPTZ, JSONB, NUMERIC\n"
            "- Add CHECK constraints where appropriate\n"
            "- Include CASCADE on foreign key deletes where appropriate\n"
            "- Generate seed data INSERT statements for dev environment\n"
            "- If compliance tables are needed, generate audit_log and consent_records\n\n"
            "Output JSON with keys:\n"
            "- ddl: full CREATE TABLE SQL string\n"
            "- migration_up: SQL for Alembic upgrade\n"
            "- migration_down: SQL for Alembic downgrade (DROP TABLE in reverse order)\n"
            "- seed_data: SQL INSERT statements for development\n"
            "- index_strategy: description of indexing decisions"
        )

        import orjson
        tables_json = orjson.dumps(tables).decode("utf-8")
        compliance = contract.get("compliance", {})

        user_content = (
            f"## Tables from Architecture Contract\n```json\n{tables_json}\n```\n\n"
            f"## Compliance Requirements\n{compliance}"
        )

        try:
            response = await self.call_ai(
                messages=[{"role": "user", "content": user_content}],
                system_prompt=system_prompt,
                task_type="general",
                temperature=0.1,
                enable_thinking=True,
            )
        except Exception as exc:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error=f"AI call failed: {exc}",
            )

        output = {
            "database_artifacts": response.content,
            "table_count": len(tables),
            "model_used": response.model_used,
            "tokens": {
                "input": response.input_tokens,
                "output": response.output_tokens,
            },
        }

        await self.store_output(pipeline_run_id, output)

        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.COMPLETED,
            output=output,
            model_used=response.model_used,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )


# Register the agent
_dhruv = Dhruv()
register_agent(_dhruv)
