"""Vikram — Chief Architect: generates the Architecture Contract (JSON).

Vikram produces THE single source of truth for the entire pipeline:
a machine-readable JSON contract specifying exact tables, columns,
indexes, API endpoints, request/response schemas, frontend pages,
and module structure.

This contract is:
- Cached once via prompt caching (90% token savings)
- Reused by ALL downstream agents (Dhruv, Shubham, Aanya, etc.)
- Validated against a schema before downstream agents consume it
- Immutable after CHECKPOINT 1 approval

Contract structure:
{
    "project_name": "...",
    "tech_stack": {...},
    "database": { "tables": [...], "indexes": [...], "enums": [...] },
    "api": { "endpoints": [...], "auth_strategy": "..." },
    "frontend": { "pages": [...], "components": [...] },
    "integrations": [...],
    "security": {...},
    "compliance": {...}
}
"""

from __future__ import annotations

from typing import Any

import orjson
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

# JSON Schema for validating the architecture contract
CONTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["project_name", "tech_stack", "database", "api", "frontend"],
    "properties": {
        "project_name": {"type": "string"},
        "tech_stack": {
            "type": "object",
            "required": ["backend", "frontend", "database"],
            "properties": {
                "backend": {"type": "string"},
                "frontend": {"type": "string"},
                "database": {"type": "string"},
                "cache": {"type": "string"},
                "hosting": {"type": "string"},
            },
        },
        "database": {
            "type": "object",
            "required": ["tables"],
            "properties": {
                "tables": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["name", "columns"],
                        "properties": {
                            "name": {"type": "string"},
                            "columns": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["name", "type"],
                                    "properties": {
                                        "name": {"type": "string"},
                                        "type": {"type": "string"},
                                        "nullable": {"type": "boolean"},
                                        "primary_key": {"type": "boolean"},
                                        "foreign_key": {"type": "string"},
                                        "unique": {"type": "boolean"},
                                        "default": {},
                                    },
                                },
                            },
                            "indexes": {"type": "array", "items": {"type": "object"}},
                            "constraints": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "enums": {"type": "array", "items": {"type": "object"}},
            },
        },
        "api": {
            "type": "object",
            "required": ["endpoints"],
            "properties": {
                "endpoints": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["method", "path", "description"],
                        "properties": {
                            "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                            "path": {"type": "string"},
                            "description": {"type": "string"},
                            "auth_required": {"type": "boolean"},
                            "request_body": {"type": "object"},
                            "response_schema": {"type": "object"},
                            "status_codes": {"type": "array", "items": {"type": "integer"}},
                        },
                    },
                },
                "auth_strategy": {"type": "string"},
                "rate_limiting": {"type": "object"},
            },
        },
        "frontend": {
            "type": "object",
            "required": ["pages"],
            "properties": {
                "pages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["name", "route"],
                        "properties": {
                            "name": {"type": "string"},
                            "route": {"type": "string"},
                            "description": {"type": "string"},
                            "auth_required": {"type": "boolean"},
                            "components": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "components": {"type": "array", "items": {"type": "object"}},
                "design_system": {"type": "object"},
            },
        },
        "integrations": {"type": "array", "items": {"type": "object"}},
        "security": {"type": "object"},
        "compliance": {"type": "object"},
    },
}


def validate_contract(contract: dict[str, Any]) -> list[str]:
    """Validate an architecture contract against the schema.

    Returns a list of validation errors (empty if valid).
    This is a lightweight check — not a full JSON Schema validator,
    but catches the most common structural issues.
    """
    errors: list[str] = []

    # Required top-level keys
    for key in CONTRACT_SCHEMA["required"]:
        if key not in contract:
            errors.append(f"Missing required key: {key}")

    # Check database has tables
    db = contract.get("database", {})
    if "tables" not in db:
        errors.append("database.tables is required")
    elif not isinstance(db["tables"], list):
        errors.append("database.tables must be an array")
    else:
        for i, table in enumerate(db["tables"]):
            if "name" not in table:
                errors.append(f"database.tables[{i}] missing 'name'")
            if "columns" not in table:
                errors.append(f"database.tables[{i}] missing 'columns'")

    # Check API has endpoints
    api = contract.get("api", {})
    if "endpoints" not in api:
        errors.append("api.endpoints is required")
    elif not isinstance(api["endpoints"], list):
        errors.append("api.endpoints must be an array")
    else:
        for i, ep in enumerate(api["endpoints"]):
            if "method" not in ep:
                errors.append(f"api.endpoints[{i}] missing 'method'")
            if "path" not in ep:
                errors.append(f"api.endpoints[{i}] missing 'path'")

    # Check frontend has pages
    fe = contract.get("frontend", {})
    if "pages" not in fe:
        errors.append("frontend.pages is required")
    elif not isinstance(fe["pages"], list):
        errors.append("frontend.pages must be an array")

    return errors


class Vikram(BaseAgent):
    """Chief Architect — generates the Architecture Contract."""

    name = "vikram"
    display_name = "Vikram — Chief Architect"
    default_complexity = TaskComplexity.HIGH  # Architecture is always high complexity

    def __init__(self) -> None:
        super().__init__()

        self.register_tool(ToolDefinition(
            name="write_contract",
            description="Write the architecture contract JSON.",
            parameters={
                "type": "object",
                "properties": {
                    "contract": {"type": "object", "description": "The full architecture contract."},
                },
                "required": ["contract"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="validate_schema",
            description="Validate the contract against the required schema.",
            parameters={
                "type": "object",
                "properties": {
                    "contract": {"type": "object"},
                },
                "required": ["contract"],
            },
        ))

    async def execute(
        self,
        pipeline_run_id: str,
        context: dict[str, Any],
    ) -> AgentResult:
        """Generate the architecture contract from Saanvi's analysis."""
        saanvi_output = context.get("saanvi")
        tilotma_output = context.get("tilotma")

        if not saanvi_output:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error="No analysis from Saanvi — cannot design architecture",
            )

        analysis = saanvi_output.get("analysis", "")
        raw_input = tilotma_output.get("raw_input", "") if tilotma_output else ""

        system_prompt = (
            "You are Vikram, the Chief Architect at NexSidi. Generate a complete "
            "Architecture Contract — the machine-readable JSON that ALL downstream "
            "agents will use as their single source of truth.\n\n"
            "The contract MUST include:\n"
            "1. project_name: string\n"
            "2. tech_stack: {backend, frontend, database, cache, hosting}\n"
            "3. database.tables: array of {name, columns: [{name, type, nullable, "
            "primary_key, foreign_key, unique, default}], indexes, constraints}\n"
            "4. api.endpoints: array of {method, path, description, auth_required, "
            "request_body, response_schema, status_codes}\n"
            "5. frontend.pages: array of {name, route, description, auth_required, components}\n"
            "6. integrations: array of {name, type, config}\n"
            "7. security: {auth_method, rate_limiting, cors, encryption}\n"
            "8. compliance: {regulations, required_tables, audit_requirements}\n\n"
            "RULES:\n"
            "- Every table MUST have an 'id' column (UUID) as primary key\n"
            "- Every table MUST have 'created_at' and 'updated_at' timestamps\n"
            "- Every multi-tenant table MUST have 'organization_id' FK\n"
            "- Every endpoint MUST specify auth_required (true/false)\n"
            "- Every endpoint MUST list expected status_codes\n"
            "- Include seed data tables if compliance flags exist\n"
            "- Foreign keys MUST reference existing table names\n"
            "- Column types: uuid, varchar, text, integer, boolean, timestamp, jsonb, decimal\n\n"
            "Output ONLY valid JSON. No markdown, no explanation."
        )

        user_content = (
            f"## User's Request\n{raw_input}\n\n"
            f"## Requirements Analysis (from Saanvi)\n{analysis}"
        )

        try:
            response = await self.call_ai(
                messages=[{"role": "user", "content": user_content}],
                system_prompt=system_prompt,
                task_type="general",
                temperature=0.1,  # Very low for precise JSON generation
                enable_thinking=True,  # Complex task needs reasoning
            )
        except Exception as exc:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error=f"AI call failed: {exc}",
            )

        # Parse and validate the contract
        contract = self._parse_contract(response.content)
        if contract is None:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error="Failed to parse architecture contract as valid JSON",
                output={"raw_response": response.content},
            )

        validation_errors = validate_contract(contract)
        if validation_errors:
            logger.warning(
                "contract_validation_errors",
                errors=validation_errors,
                count=len(validation_errors),
            )

        output = {
            "contract": contract,
            "validation_errors": validation_errors,
            "is_valid": len(validation_errors) == 0,
            "stats": {
                "tables": len(contract.get("database", {}).get("tables", [])),
                "endpoints": len(contract.get("api", {}).get("endpoints", [])),
                "pages": len(contract.get("frontend", {}).get("pages", [])),
            },
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

    def _parse_contract(self, content: str) -> dict[str, Any] | None:
        """Parse JSON from AI response, handling markdown code blocks."""
        # Strip markdown code fences if present
        cleaned = content.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            # Remove first and last line (```json and ```)
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines)

        try:
            return orjson.loads(cleaned.encode("utf-8"))
        except orjson.JSONDecodeError:
            logger.error("contract_json_parse_failed", content_preview=cleaned[:200])
            return None


# Register the agent
_vikram = Vikram()
register_agent(_vikram)
