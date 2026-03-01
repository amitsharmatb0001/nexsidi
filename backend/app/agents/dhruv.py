"""Dhruv -- Database Architect: schema generation for any supported database.

Reads Vikram's architecture contract and generates database artifacts
using the DatabaseConfig registry. Supports all registered databases:
- Relational: PostgreSQL, MySQL, SQLite, CockroachDB, Supabase
- Document:   MongoDB, Firebase Firestore
- Graph:      Neo4j
- Key-Value:  DynamoDB, Redis

The system prompt is dynamically built from each DatabaseConfig's rules,
type_mappings, ddl_examples, index_types, and orm_patterns -- no hardcoded
SQL dialect.
"""

from __future__ import annotations

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
from app.agents.database_configs import DatabaseConfig, get_database_config
from app.services.ai_router import TaskComplexity

logger = structlog.get_logger(__name__)

# Map common backend framework names to their primary language so we can
# select the correct connection template from DatabaseConfig.
_FRAMEWORK_LANGUAGE: dict[str, str] = {
    "fastapi": "python",
    "django": "python",
    "flask": "python",
    "express": "typescript",
    "nestjs": "typescript",
    "nextjs": "typescript",
    "springboot": "java",
    "rails": "ruby",
    "laravel": "php",
    "go_gin": "go",
    "rust_axum": "rust",
    "aspnet": "csharp",
    "kotlin_ktor": "kotlin",
}


class Dhruv:
    """Database Architect -- schema generation from architecture contract."""

    name = "dhruv"
    display_name = "Dhruv -- Database Architect"
    default_complexity = TaskComplexity.HIGH
    default_model: str | None = None

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

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

    # ── Helper: detect database from contract ──────────────────────────

    @staticmethod
    def _detect_database(contract: dict[str, Any]) -> str:
        """Detect database from architecture contract."""
        tech_stack = contract.get("tech_stack", {})
        db = tech_stack.get("database", "postgresql")
        return db

    # ── Helper: build dynamic system prompt from DatabaseConfig ────────

    def _build_system_prompt(
        self,
        db_config: DatabaseConfig,
        backend_framework: str = "",
    ) -> str:
        """Dynamically build the system prompt from a DatabaseConfig.

        Prompt structure mirrors Shubham's ``_build_generation_prompt``:
        1. Role + database identity
        2. DDL dialect
        3. Mandatory rules (from ``db_config.rules``)
        4. Type mappings
        5. DDL / schema examples (golden patterns)
        6. Available index types
        7. Connection template (for the relevant language)
        8. ORM pattern (if backend framework is known)
        9. Output format instructions
        """
        parts: list[str] = []

        # ── 1. Role + Database Identity ──
        parts.extend([
            "You are Dhruv, the Database Architect at NexSidi.",
            f"You are generating schema for **{db_config.display_name}** "
            f"({db_config.category} database).",
            "",
        ])

        # ── 2. DDL Dialect ──
        parts.append(f"DDL dialect: {db_config.ddl_dialect}")
        if db_config.supports_transactions:
            parts.append("This database supports ACID transactions.")
        if db_config.supports_migrations:
            parts.append("This database supports schema migrations.")
        parts.append(f"Default port: {db_config.default_port}")
        parts.append("")

        # ── 3. Mandatory Rules ──
        parts.append(
            f"## MANDATORY {db_config.display_name} RULES (NEVER VIOLATE)"
        )
        for idx, rule in enumerate(db_config.rules, start=1):
            parts.append(f"{idx}. {rule}")
        parts.append("")

        # ── 4. Type Mappings ──
        parts.append(f"## {db_config.display_name} TYPE MAPPINGS")
        parts.append(
            "Use these exact types when generating schema. The left column is "
            "the abstract type from the architecture contract; the right column "
            "is the database-specific type you MUST use."
        )
        for abstract_type, db_type in db_config.type_mappings.items():
            parts.append(f"- {abstract_type} -> {db_type}")
        parts.append("")

        # ── 5. DDL / Schema Examples (Golden Patterns) ──
        if db_config.ddl_examples:
            parts.append(f"## GOLDEN EXAMPLES -- {db_config.display_name}")
            parts.append(
                "Follow these EXACT patterns. Adapt names/fields from the "
                "architecture contract."
            )
            for example_name, example_code in db_config.ddl_examples.items():
                parts.extend([
                    f"### {example_name}",
                    f"```\n{example_code}\n```",
                    "",
                ])

        # ── 6. Available Index Types ──
        parts.append(f"## INDEX TYPES AVAILABLE ({db_config.display_name})")
        parts.append(", ".join(db_config.index_types))
        parts.append("")

        # ── 7. Connection Template ──
        lang = _FRAMEWORK_LANGUAGE.get(backend_framework, "")
        conn_template = db_config.connection_templates.get(lang)
        if conn_template:
            parts.extend([
                f"## CONNECTION TEMPLATE ({lang})",
                f"```\n{conn_template}\n```",
                "",
            ])

        # ── 8. ORM Pattern ──
        orm = db_config.orm_patterns.get(backend_framework)
        if orm:
            parts.extend([
                f"## ORM / DRIVER",
                f"Use **{orm}** for the {backend_framework} backend.",
                "",
            ])

        # ── 9. Output Format ──
        parts.append("## OUTPUT FORMAT")
        parts.append("Output valid JSON with these keys:")

        if db_config.category == "relational":
            # Standard relational output
            parts.extend([
                "- ddl: full CREATE TABLE SQL string (in dependency order, "
                "referenced tables first)",
                "- migration_up: SQL for migration upgrade",
                "- migration_down: SQL for migration downgrade (DROP TABLE "
                "in reverse order)",
                "- seed_data: INSERT statements for development",
                "- index_strategy: description of indexing decisions",
            ])
        elif db_config.name == "mongodb":
            parts.extend([
                "- schema_definition: collection validators (JSON Schema) "
                "for every collection",
                "- model_code: Mongoose/Beanie model definitions",
                "- indexes: createIndex() calls for all collections",
                "- seed_data: insertMany() statements for development",
                "- index_strategy: description of indexing decisions",
            ])
        elif db_config.name == "firebase":
            parts.extend([
                "- security_rules: Firestore Security Rules file",
                "- collection_schema: TypeScript interfaces describing "
                "every collection's document shape",
                "- composite_indexes: firestore.indexes.json content",
                "- seed_data: Firestore batch write code for development",
                "- index_strategy: description of indexing decisions",
            ])
        elif db_config.name == "dynamodb":
            parts.extend([
                "- table_definition: CreateTable JSON (KeySchema, "
                "AttributeDefinitions, BillingMode)",
                "- gsi_config: Global Secondary Index definitions",
                "- access_patterns: mapping of each access pattern to "
                "its PK/SK/GSI design",
                "- seed_data: PutItem / BatchWriteItem examples for "
                "development",
                "- index_strategy: description of key design and GSI "
                "decisions",
            ])
        elif db_config.name == "neo4j":
            parts.extend([
                "- cypher_schema: Cypher CREATE statements for nodes and "
                "relationships",
                "- constraints: uniqueness and existence constraints",
                "- indexes: index creation statements",
                "- seed_data: Cypher MERGE statements for development",
                "- index_strategy: description of indexing decisions",
            ])
        elif db_config.name == "redis":
            parts.extend([
                "- key_design: key naming patterns for every entity",
                "- data_structures: which Redis data structure (Hash, "
                "Set, Sorted Set, List, Stream, JSON) to use per entity",
                "- search_indexes: RediSearch FT.CREATE definitions "
                "(if applicable)",
                "- seed_data: example Redis commands for development",
                "- index_strategy: description of key design and search "
                "index decisions",
            ])
        else:
            # Generic fallback for any future database category
            parts.extend([
                "- schema: full schema definition in the database's native "
                "format",
                "- seed_data: example data inserts for development",
                "- index_strategy: description of indexing decisions",
            ])

        parts.append("")

        return "\n".join(parts)

    # ── Main execution ─────────────────────────────────────────────────

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

        # Detect database and backend framework from contract
        db_name = self._detect_database(contract)
        db_config = get_database_config(db_name)
        backend_framework = contract.get("tech_stack", {}).get(
            "backend", "fastapi"
        )

        logger.info(
            "database_detected",
            database=db_config.name,
            display_name=db_config.display_name,
            category=db_config.category,
            backend=backend_framework,
        )

        # Collect tables / collections / entities from contract
        tables = contract.get("database", {}).get("tables", [])
        if not tables:
            tables = contract.get("database", {}).get("collections", [])
        if not tables:
            tables = contract.get("database", {}).get("entities", [])

        if not tables:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error="Architecture contract has no database tables, "
                      "collections, or entities",
            )

        # Build dynamic system prompt from DatabaseConfig
        system_prompt = self._build_system_prompt(db_config, backend_framework)

        import orjson
        tables_json = orjson.dumps(tables).decode("utf-8")
        compliance = contract.get("compliance", {})

        # Use the appropriate terminology in the user prompt
        entity_label = "Tables"
        if db_config.category == "document":
            entity_label = "Collections"
        elif db_config.category == "graph":
            entity_label = "Node Labels / Relationships"
        elif db_config.category == "key_value":
            entity_label = "Entities"

        user_content = (
            f"## {entity_label} from Architecture Contract\n"
            f"```json\n{tables_json}\n```\n\n"
            f"## Compliance Requirements\n{compliance}"
        )

        try:
            response = await call_ai(self, 
                messages=[{"role": "user", "content": user_content}],
                system_prompt=system_prompt,
                task_type="general",
                temperature=0.1,
                enable_thinking=True,
            )
        except Exception as exc:
            # R21-FIX: Sanitize exception to prevent API key leakage.
            from app.services.ai_router import _sanitize_error
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error=f"AI call failed: {_sanitize_error(exc)}",
            )

        output = {
            "database_artifacts": response.content,
            "table_count": len(tables),
            "database": db_config.name,
            "database_display_name": db_config.display_name,
            "database_category": db_config.category,
            "model_used": response.model_used,
            "tokens": {
                "input": response.input_tokens,
                "output": response.output_tokens,
            },
        }

        await store_output(self, pipeline_run_id, output)

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
