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

    # ── 1b. AUDIT-T1-6: Detect duplicate column names within each table ──────
    for table in tables:
        tname = table.get("name", "<unnamed>")
        col_names: set[str] = set()
        for col in table.get("columns", []):
            cname = col.get("name")
            if not cname:
                continue
            if cname in col_names:
                errors.append(f"Table '{tname}': duplicate column '{cname}'")
            col_names.add(cname)

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


# ── CHANGE-19: API contract validation (Shubham ↔ Aanya) ──────────────


def extract_api_endpoints_from_code(
    file_contents: dict[str, str],
    framework: str = "fastapi",
) -> list[dict[str, str]]:
    """Extract actual API endpoints from generated backend code.

    Regex-scans router/app decorators in generated files to find the
    real endpoints that Shubham produced. Returns list of
    {"method": "POST", "path": "/api/users", "file": "routers/users.py"}.
    """
    import re

    _PATTERNS: dict[str, re.Pattern[str]] = {
        "fastapi": re.compile(
            r"@(?:router|app)\.(get|post|put|delete|patch)\(\s*[\"']([^\"']+)"
        ),
        "express": re.compile(
            r"(?:router|app)\.(get|post|put|delete|patch)\(\s*[\"']([^\"']+)"
        ),
        "django": re.compile(
            r"path\(\s*[\"']([^\"']+)[\"']"
        ),
    }

    pattern = _PATTERNS.get(framework.lower(), _PATTERNS["fastapi"])
    endpoints: list[dict[str, str]] = []

    for path, content in file_contents.items():
        if not any(path.endswith(ext) for ext in (".py", ".ts", ".js")):
            continue
        for match in pattern.finditer(content):
            if framework.lower() == "django":
                endpoints.append(
                    {"method": "ALL", "path": match.group(1), "file": path}
                )
            else:
                endpoints.append(
                    {
                        "method": match.group(1).upper(),
                        "path": match.group(2),
                        "file": path,
                    }
                )

    return endpoints


def validate_api_contract(
    contract_endpoints: list[dict[str, Any]],
    actual_endpoints: list[dict[str, str]],
) -> list[str]:
    """Compare architecture contract endpoints against actually generated ones.

    Returns list of warning strings (empty = no mismatches).
    """
    warnings: list[str] = []

    actual_paths = {
        (ep["method"], ep["path"]) for ep in actual_endpoints
    }
    contract_paths = {
        (ep.get("method", "").upper(), ep.get("path", ""))
        for ep in contract_endpoints
        if ep.get("path")
    }

    # Endpoints in contract but not generated
    missing = contract_paths - actual_paths
    for method, path in sorted(missing)[:5]:
        warnings.append(
            f"Contract endpoint {method} {path} was NOT generated by backend"
        )

    # Endpoints generated but not in contract
    extra = actual_paths - contract_paths
    for method, path in sorted(extra)[:5]:
        warnings.append(
            f"Backend generated {method} {path} which is NOT in the contract"
        )

    if warnings:
        logger.warning(
            "api_contract_mismatch",
            missing=len(missing),
            extra=len(extra),
            warnings=warnings[:5],
        )

    return warnings
