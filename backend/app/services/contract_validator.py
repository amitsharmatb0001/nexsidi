"""Contract coherence validator.

Validates referential integrity within the Vikram architecture contract BEFORE
downstream agents (Shubham, Aanya, Dhruv) consume it.

Schema validation (run inside Vikram) checks structural correctness.
Coherence validation (run here) checks cross-entity consistency:
- FK columns reference existing tables
- No duplicate table names (would produce duplicate Alembic migrations)
- No duplicate endpoint method+path combos (would cause FastAPI router conflicts)

Fail-fast: if coherence checks fail, the pipeline stops immediately instead of
generating thousands of lines of incorrect code that references phantom tables.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def validate_contract_coherence(contract: dict[str, Any]) -> list[str]:
    """Check cross-entity referential integrity within the architecture contract.

    Returns a list of coherence errors (empty = contract is coherent).
    """
    if not isinstance(contract, dict):
        return ["contract is not a JSON object"]

    errors: list[str] = []

    tables: list[dict[str, Any]] = contract.get("database", {}).get("tables", [])
    endpoints: list[dict[str, Any]] = contract.get("api", {}).get("endpoints", [])

    # ── 1. Collect all known table names (detect duplicates too) ──────────────
    table_names: set[str] = set()
    for table in tables:
        name = table.get("name")
        if not name:
            continue
        if name in table_names:
            errors.append(f"Duplicate table name: '{name}'")
        table_names.add(name)

    # ── 2. Foreign-key referential integrity ──────────────────────────────────
    # FK field format: "referenced_table.column" or "referenced_table"
    for table in tables:
        tname = table.get("name", "<unnamed>")
        for col in table.get("columns", []):
            fk = col.get("foreign_key")
            if not fk or not isinstance(fk, str):
                continue
            ref_table = fk.split(".")[0]
            if ref_table and ref_table not in table_names:
                errors.append(
                    f"table '{tname}' column '{col.get('name', '<unnamed>')}': "
                    f"foreign_key references non-existent table '{ref_table}'"
                )

    # ── 3. Duplicate endpoint method+path combos ──────────────────────────────
    seen_endpoints: set[str] = set()
    for ep in endpoints:
        method = ep.get("method", "").upper()
        path = ep.get("path", "")
        if not method or not path:
            continue
        key = f"{method} {path}"
        if key in seen_endpoints:
            errors.append(f"Duplicate API endpoint: {key}")
        seen_endpoints.add(key)

    if errors:
        logger.warning(
            "contract_coherence_failed",
            error_count=len(errors),
            errors=errors[:10],  # log first 10 for brevity
        )
    else:
        logger.debug("contract_coherence_passed", tables=len(table_names), endpoints=len(seen_endpoints))

    return errors
