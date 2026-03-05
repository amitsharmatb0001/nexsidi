"""Shared code validation utilities for build agents.

Extracted from Fixer's check_imports to enable import validation during code
generation (Shubham, Aanya) rather than only at fix time.  Also provides
lightweight type-checking via pyright (Python) and tsc (TypeScript).

Usage by agents:
    from app.agents.tools.code_validator import check_imports, check_types, extract_project_files
"""

from __future__ import annotations

import ast
import sys
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ── Known third-party packages ─────────────────────────────────────
# Common packages found in generated projects.  Expanded from the original
# Fixer list to cover all 13 supported frameworks.

KNOWN_THIRD_PARTY: frozenset[str] = frozenset({
    # Python web
    "fastapi", "uvicorn", "pydantic", "pydantic_settings", "sqlalchemy",
    "alembic", "passlib", "jose", "jwt", "bcrypt", "dotenv", "orjson",
    "httpx", "requests", "celery", "redis", "structlog", "django", "flask",
    "starlette", "databases", "aiohttp", "asyncpg", "psycopg2", "psycopg",
    # Data / ML
    "numpy", "pandas", "pillow", "PIL",
    # Auth / security
    "email_validator", "python_multipart", "slowapi", "pyotp", "qrcode",
    "fido2", "webauthn", "cryptography", "argon2",
    # Cloud / infra
    "boto3", "botocore", "google", "firebase_admin",
    # Payments / comms
    "stripe", "sendgrid", "twilio",
    # ORM / DB drivers
    "pymongo", "motor", "tortoise", "peewee", "prisma",
    # Testing
    "pytest", "unittest", "mock", "faker", "factory",
    # Template / serialization
    "jinja2", "greenlet", "yaml", "toml", "msgpack",
    # Misc
    "celery", "kombu", "valkey", "jsonschema",
})

# Top-level prefixes considered project-internal
INTERNAL_PREFIXES: frozenset[str] = frozenset({
    "app", "backend", "core", "config", "src", "lib", "utils", "helpers",
})


# ── Import Checking ────────────────────────────────────────────────


def check_imports(
    path: str,
    code: str,
    project_files: set[str] | None = None,
) -> str:
    """Verify all imports in a Python file resolve to known modules.

    Args:
        path: File path (used for error messages).
        code: Source code to analyse.
        project_files: Set of dotted module paths the project already defines
                       (e.g. ``{"app.models.user", "app.routers"}``).

    Returns:
        ``"OK"``-prefixed string if all imports resolve, or a diagnostic
        listing unresolved imports.
    """
    if not path.endswith(".py"):
        return "check_imports only works on Python files"

    if not code.strip():
        return f"No content for {path}"

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"Cannot parse {path}: SyntaxError at line {e.lineno}: {e.msg}"

    imports: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append({"module": alias.name, "line": node.lineno, "type": "import"})
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append({"module": node.module, "line": node.lineno, "type": "from_import"})

    if not imports:
        return "No imports found in file."

    project_files = project_files or set()
    unresolved: list[dict[str, Any]] = []

    for imp in imports:
        module = imp["module"]
        top_level = module.split(".")[0]

        # stdlib
        if top_level in sys.stdlib_module_names:
            continue
        # known third-party
        if top_level in KNOWN_THIRD_PARTY:
            continue
        # project file
        if any(module.startswith(pf) or pf.startswith(module) for pf in project_files):
            continue
        # internal prefix
        if top_level in INTERNAL_PREFIXES:
            continue

        unresolved.append(imp)

    if not unresolved:
        return f"OK — all {len(imports)} imports in {path} resolved successfully."

    lines = [f"WARNING: {len(unresolved)} unresolved import(s) in {path}:"]
    for u in unresolved:
        lines.append(f"  Line {u['line']}: {u['type']} {u['module']}")
    lines.append("Possible fixes: add to requirements.txt, fix typo, or create missing module.")
    return "\n".join(lines)


# ── Type Checking ──────────────────────────────────────────────────


def check_types(path: str, code: str) -> str:
    """Run lightweight type checking on Python or TypeScript code.

    * Python — invokes ``pyright`` in a subprocess (30 s timeout).
    * TypeScript — invokes ``npx tsc --noEmit`` (30 s timeout).

    Non-blocking: returns warnings only, never stops generation.  If the
    required tool is not installed the function returns a skip message.
    """
    import os
    import subprocess
    import tempfile

    if path.endswith(".py"):
        return _check_types_python(path, code)
    elif path.endswith((".ts", ".tsx")):
        return _check_types_typescript(path, code)
    return f"Type checking not supported for {path}"


def _check_types_python(path: str, code: str) -> str:
    import json as _json
    import os
    import subprocess
    import tempfile

    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8",
        ) as f:
            f.write(code)
            temp_path = f.name

        proc = subprocess.run(
            ["pyright", "--outputjson", temp_path],
            capture_output=True,
            text=True,
            timeout=30,
        )

        try:
            os.unlink(temp_path)
        except OSError:
            pass

        if proc.returncode == 0:
            return f"Type check passed for {path}"

        try:
            result = _json.loads(proc.stdout)
            diagnostics = result.get("generalDiagnostics", [])
            if not diagnostics:
                return f"Type check passed for {path}"
            errors = [
                f"  Line {d.get('range', {}).get('start', {}).get('line', '?')}: "
                f"{d.get('severity', 'error')}: {d.get('message', '')[:120]}"
                for d in diagnostics[:5]
            ]
            return f"Type warnings in {path}:\n" + "\n".join(errors)
        except _json.JSONDecodeError:
            return f"Type check ran but output unparseable for {path}"

    except FileNotFoundError:
        return "pyright not installed — type checking skipped"
    except subprocess.TimeoutExpired:
        return "Type check timed out (>30 s)"
    except Exception as exc:
        return f"Type check failed: {str(exc)[:120]}"


def _check_types_typescript(path: str, code: str) -> str:
    import os
    import subprocess
    import tempfile

    try:
        suffix = os.path.splitext(path)[1] or ".ts"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=suffix, delete=False, encoding="utf-8",
        ) as f:
            f.write(code)
            temp_path = f.name

        proc = subprocess.run(
            ["npx", "tsc", "--noEmit", "--strict", temp_path],
            capture_output=True,
            text=True,
            timeout=30,
        )

        try:
            os.unlink(temp_path)
        except OSError:
            pass

        if proc.returncode == 0:
            return f"Type check passed for {path}"

        lines = proc.stdout.strip().split("\n")[:5]
        return f"TypeScript warnings in {path}:\n" + "\n".join(f"  {ln}" for ln in lines)

    except FileNotFoundError:
        return "tsc not available — TypeScript type checking skipped"
    except subprocess.TimeoutExpired:
        return "TypeScript type check timed out (>30 s)"
    except Exception as exc:
        return f"TypeScript type check failed: {str(exc)[:120]}"


# ── Project File Extraction ────────────────────────────────────────


def extract_project_files(context: dict[str, Any]) -> set[str]:
    """Build a set of dotted module paths from agent context.

    Scans ``shubham`` and ``aanya`` outputs for ``generated_files`` /
    ``file_contents`` keys and converts file paths to Python module paths
    so ``check_imports`` can recognise project-internal imports.
    """
    project_files: set[str] = set()
    for agent in ("shubham", "aanya"):
        agent_out = context.get(agent, {})
        if not isinstance(agent_out, dict):
            continue
        # generated_files list
        for fp in agent_out.get("generated_files", []):
            _add_module_path(project_files, fp)
        # file_contents dict keys
        for fp in agent_out.get("file_contents", {}):
            _add_module_path(project_files, fp)
    return project_files


def _add_module_path(project_files: set[str], fp: str) -> None:
    """Convert a file path to a dotted module path and add it + parent."""
    if not fp.endswith(".py"):
        return
    mod = fp.replace("/", ".").replace("\\", ".")
    # Strip trailing .py
    if mod.endswith(".py"):
        mod = mod[:-3]
    project_files.add(mod)
    # Also add parent package
    parts = mod.rsplit(".", 1)
    if len(parts) > 1:
        project_files.add(parts[0])
