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
    call_ai_with_tools,
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


class DhruvToolHandler:
    """Handles tool calls for Dhruv's agentic database schema generation loop."""

    def __init__(self, db_config: "DatabaseConfig") -> None:
        self._db_config = db_config
        self._written_files: dict[str, str] = {}
        self._migrations: list[dict[str, str]] = []
        self._seed_data: list[dict[str, Any]] = []

    async def __call__(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == "write_file":
            return self._write_file(tool_input["path"], tool_input["content"])
        elif tool_name == "generate_migration":
            return self._generate_migration(tool_input)
        elif tool_name == "generate_seed_data":
            return self._generate_seed_data(tool_input)
        elif tool_name == "validate_schema":
            return self._validate_schema(
                tool_input["content"],
                tool_input.get("dialect", self._db_config.name),
            )
        else:
            return f"Unknown tool: {tool_name}"

    def _write_file(self, path: str, content: str) -> str:
        if len(content) < 5:
            return "Error: content too short — must contain actual schema definitions"
        self._written_files[path] = content

        # Manifest injection: show Dhruv what DDL files it has generated
        msg = f"Written {path} ({len(content)} chars)"
        manifest_lines = ["\n\n📂 GENERATED DDL FILES (avoid duplicates, ensure completeness):"]
        for fpath in sorted(self._written_files.keys()):
            fc = self._written_files[fpath]
            if not fc:
                continue
            fline_count = fc.count("\n") + 1
            # Extract table names from SQL CREATE TABLE statements
            import re
            tables = re.findall(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?["`]?(\w+)', fc, re.IGNORECASE)
            table_str = f" — tables: {', '.join(tables)}" if tables else ""
            manifest_lines.append(f"  - `{fpath}` ({fline_count} lines){table_str}")
        if len(manifest_lines) > 1:
            msg += "\n".join(manifest_lines)
        return msg

    def _generate_migration(self, tool_input: dict) -> str:
        migration = {
            "name": tool_input.get("migration_name", "unnamed"),
            "sql_up": tool_input.get("sql_up", ""),
            "sql_down": tool_input.get("sql_down", ""),
        }
        self._migrations.append(migration)
        return f"Migration '{migration['name']}' recorded (up: {len(migration['sql_up'])} chars, down: {len(migration['sql_down'])} chars)"

    def _generate_seed_data(self, tool_input: dict) -> str:
        import json as _json
        seed = {
            "table_name": tool_input.get("table_name", "unknown"),
            "rows": tool_input.get("rows", []),
        }
        self._seed_data.append(seed)
        return f"Seed data for '{seed['table_name']}': {len(seed['rows'])} rows recorded"

    def _validate_schema(self, content: str, dialect: str) -> str:
        """Basic schema validation — check bracket balance and SQL keywords."""
        # Check bracket balance
        opens = content.count("(")
        closes = content.count(")")
        if opens != closes:
            return f"Validation FAILED: unbalanced parentheses (opens={opens}, closes={closes})"

        # Check for common SQL DDL keywords (relational DBs)
        if dialect in ("postgresql", "mysql", "sqlite", "cockroachdb", "supabase"):
            has_create = "CREATE" in content.upper()
            if not has_create:
                return "Validation WARNING: no CREATE statements found"

        return "Validation OK — schema syntax looks correct"


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

        self.register_tool(ToolDefinition(
            name="validate_schema",
            description="Validate generated SQL/schema for syntax correctness.",
            parameters={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Schema content to validate."},
                    "dialect": {"type": "string", "description": "Database dialect (postgresql, mysql, etc)."},
                },
                "required": ["content"],
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
        raw_db = str(tech_stack.get("database", "postgresql")).lower()
        # AUDIT-FIX: Normalize database name for DatabaseConfig registry lookup.
        # Contract may contain "PostgreSQL 16" but registry expects "postgresql".
        _DB_ALIASES: dict[str, str] = {
            "postgres": "postgresql",
            "mysql": "mysql",
            "mongo": "mongodb",
            "sqlite": "sqlite",
            "firebase": "firebase",
            "supabase": "supabase",
            "dynamo": "dynamodb",
            "neo4j": "neo4j",
            "cockroach": "cockroachdb",
            "redis": "redis",
        }
        for alias, canonical in _DB_ALIASES.items():
            if alias in raw_db:
                return canonical
        return "postgresql"  # Safe default

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

        # Add agentic instructions
        system_prompt += "\n".join([
            "",
            "## TOOLS",
            "You have tools to generate database artifacts iteratively:",
            "- write_file: Write schema files (models, migrations, configs)",
            "- generate_migration: Create migration scripts with up/down SQL",
            "- generate_seed_data: Create seed data for development",
            "- validate_schema: Validate your generated schema for correctness",
            "",
            "Workflow:",
            "1. Generate the main schema/DDL and write it with write_file",
            "2. Validate the schema with validate_schema",
            "3. Generate migration scripts with generate_migration",
            "4. Generate seed data with generate_seed_data",
            "5. If validation fails, fix and re-write",
        ])

        import orjson
        tables_json = orjson.dumps(tables).decode("utf-8")
        compliance = contract.get("compliance", {})

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

        # FIX-40: Inject rejected approaches
        from app.agents.base import build_rejection_context
        _rejection_ctx = build_rejection_context(context)
        if _rejection_ctx:
            user_content += _rejection_ctx

        handler = DhruvToolHandler(db_config=db_config)

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
            from app.services.ai_router import _sanitize_error
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error=f"AI call failed: {_sanitize_error(exc)}",
            )

        # Build output from tool handler results + any final text response
        output = {
            "database_artifacts": response.content,
            "written_files": handler._written_files,
            "migrations": handler._migrations,
            "seed_data": handler._seed_data,
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

        # ── LLM self-evaluation: check completeness against contract ──
        try:
            llm_eval = await self._run_llm_self_evaluation(
                handler._written_files, tables, db_config,
            )
            output["llm_evaluation"] = llm_eval
        except Exception:
            logger.warning("dhruv_self_eval_failed", exc_info=True)

        await store_output(self, pipeline_run_id, output)

        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.COMPLETED,
            output=output,
            model_used=response.model_used,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )

    # ── LLM-driven self-evaluation ──────────────────────────────────

    async def _run_llm_self_evaluation(
        self,
        written_files: dict[str, str],
        contract_tables: list[dict[str, Any]],
        db_config: DatabaseConfig,
    ) -> dict[str, Any]:
        """LLM reviews its own DDL/migrations against the contract tables.

        Checks: all tables generated, all columns present, foreign keys correct,
        indexes for query patterns, migration files for each table.
        Uses cheapest model (~$0.002/call).
        """
        # Build file summary
        file_lines: list[str] = []
        for path, content in sorted(written_files.items()):
            if not content:
                continue
            lc = content.count("\n") + 1
            # Show first few CREATE/table lines
            key_lines = [
                ln.strip() for ln in content.split("\n")
                if any(kw in ln.upper() for kw in ("CREATE", "TABLE", "INDEX", "FOREIGN KEY", "REFERENCES"))
            ][:5]
            key_str = "; ".join(key_lines) if key_lines else ""
            file_lines.append(f"  {path} ({lc} lines) — {key_str}")

        files_text = "\n".join(file_lines) if file_lines else "  (no files written)"

        # Build contract summary
        table_lines: list[str] = []
        for tbl in contract_tables[:20]:
            name = tbl.get("name", "unknown")
            cols = [c.get("name", "?") for c in tbl.get("columns", tbl.get("fields", []))]
            rels = tbl.get("relationships", [])
            rel_str = f" | rels: {', '.join(str(r) for r in rels[:3])}" if rels else ""
            table_lines.append(f"  {name}: {', '.join(cols[:12])}{rel_str}")
        tables_text = "\n".join(table_lines)

        eval_prompt = (
            "You are reviewing database artifacts YOU just generated. Be brutally honest.\n\n"
            f"## Database: {db_config.display_name} ({db_config.category})\n\n"
            f"## Contract Tables Required\n{tables_text}\n\n"
            f"## Files You Generated\n{files_text}\n\n"
            "## Your Task\n"
            "Compare what the contract REQUIRES vs what you ACTUALLY generated.\n"
            "Find:\n"
            "1. Missing tables — tables in contract but NOT in any generated file\n"
            "2. Missing columns — columns listed in contract but NOT in the DDL\n"
            "3. Missing foreign keys/relationships — declared in contract but NOT implemented\n"
            "4. Missing indexes — columns that will be queried but have no index\n"
            "5. Missing migrations — tables without migration files\n"
            "6. Completeness — what percentage of the contract is implemented?\n\n"
            "Respond in JSON:\n"
            "{\n"
            '  "missing_tables": [{"name": "table_name", "reason": "not generated"}],\n'
            '  "missing_columns": [{"table": "name", "column": "col_name"}],\n'
            '  "missing_relationships": [{"from": "t1", "to": "t2", "type": "FK"}],\n'
            '  "completeness_pct": 0-100,\n'
            '  "verdict": "PASS" or "FAIL",\n'
            '  "reasoning": "brief explanation"\n'
            "}\n"
            "If everything is complete, return empty arrays and PASS."
        )

        from app.services.ai_router import get_ai_router, AIRequest, AIMessage

        router = get_ai_router()
        resp = await router.call(AIRequest(
            messages=[AIMessage(role="user", content=eval_prompt)],
            complexity=TaskComplexity.LOW,
            max_tokens=1000,
            agent_name=f"{self.name}_self_eval",
        ))

        from app.utils.json_parser import parse_json
        result = parse_json(resp.content, fallback={})
        if not isinstance(result, dict):
            result = {}
        return result


# Register the agent
_dhruv = Dhruv()
register_agent(_dhruv)
