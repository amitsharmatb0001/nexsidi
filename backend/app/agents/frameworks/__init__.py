"""Framework configuration registry for backend code generation.

Each supported backend framework (FastAPI, Django, Express, etc.) has a
``FrameworkConfig`` that tells Shubham how to generate code for that stack:
- Language-aware code fences (Python vs TypeScript)
- Framework-specific mandatory rules (ORM, validation, routing, auth)
- Golden code examples per generation step
- Expected file structure for correct imports

New frameworks are added by creating a config file (e.g. ``flask_config.py``)
and calling ``register_framework()`` at module level.

Usage::

    from app.agents.frameworks import get_framework_config

    config = get_framework_config("django")
    print(config.language)          # "python"
    print(len(config.rules))        # 14
    print(config.golden_examples)   # {"models": "...", "serializers": "...", ...}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class FrameworkConfig:
    """Immutable configuration for a backend framework.

    Attributes:
        name: Canonical name (e.g. ``"fastapi"``, ``"django"``, ``"express"``).
        display_name: Human-readable name (e.g. ``"FastAPI"``).
        language: Programming language (``"python"`` or ``"typescript"``).
        code_block_lang: Markdown code fence language tag.
        error_comment_prefix: ``"#"`` for Python, ``"//"`` for TypeScript.
        file_structure: Map of generation step name to expected file path
            (e.g. ``{"models": "app/models.py"}``).
        rules: 12-15 mandatory framework-specific rules for the AI to follow.
        golden_examples: Map of generation step name to a short (~15-30 line)
            code snippet showing the exact pattern the AI should follow.
    """

    name: str
    display_name: str
    language: str
    code_block_lang: str
    error_comment_prefix: str
    file_structure: dict[str, str]
    rules: tuple[str, ...]  # Tuple for hashability (frozen dataclass)
    golden_examples: dict[str, str]
    # OCP-FIX: Generation DAG moved from shubham.py into each framework plugin.
    # shubham.execute() reads these instead of the module-level dicts.
    generation_order: tuple[dict[str, str], ...] = field(default_factory=tuple)
    dependency_graph: dict[str, set[str]] = field(default_factory=dict)


# ── Registry ────────────────────────────────────────────────────────

_FRAMEWORK_REGISTRY: dict[str, FrameworkConfig] = {}


def register_framework(config: FrameworkConfig) -> None:
    """Register a framework configuration.

    Called at import time by each framework config module.
    Duplicate registrations overwrite silently (for hot-reload).
    """
    _FRAMEWORK_REGISTRY[config.name] = config
    logger.debug("framework_registered", name=config.name)


def get_framework_config(name: str) -> FrameworkConfig:
    """Get framework config by canonical name.

    Falls back to ``"fastapi"`` for unknown frameworks (backward compatible).

    Args:
        name: Framework name (case-insensitive).

    Returns:
        Matching FrameworkConfig, or FastAPI config as fallback.
    """
    key = name.lower()
    if key in _FRAMEWORK_REGISTRY:
        return _FRAMEWORK_REGISTRY[key]

    # Fallback to fastapi
    if "fastapi" in _FRAMEWORK_REGISTRY:
        logger.warning(
            "unknown_framework_fallback",
            requested=name,
            fallback="fastapi",
        )
        return _FRAMEWORK_REGISTRY["fastapi"]

    raise ValueError(f"No framework config registered for '{name}' and no fastapi fallback available")


def list_frameworks() -> list[str]:
    """List all registered framework names."""
    return sorted(_FRAMEWORK_REGISTRY.keys())


# ── Auto-register all framework configs on import ───────────────────

from app.agents.frameworks import aspnet_config as _aspnet  # noqa: E402, F401
from app.agents.frameworks import django_config as _django  # noqa: E402, F401
from app.agents.frameworks import express_config as _express  # noqa: E402, F401
from app.agents.frameworks import fastapi_config as _fastapi  # noqa: E402, F401
from app.agents.frameworks import flask_config as _flask  # noqa: E402, F401
from app.agents.frameworks import go_gin_config as _go_gin  # noqa: E402, F401
from app.agents.frameworks import kotlin_ktor_config as _kotlin_ktor  # noqa: E402, F401
from app.agents.frameworks import laravel_config as _laravel  # noqa: E402, F401
from app.agents.frameworks import nestjs_config as _nestjs  # noqa: E402, F401
from app.agents.frameworks import nextjs_config as _nextjs  # noqa: E402, F401
from app.agents.frameworks import rails_config as _rails  # noqa: E402, F401
from app.agents.frameworks import rust_axum_config as _rust_axum  # noqa: E402, F401
from app.agents.frameworks import springboot_config as _springboot  # noqa: E402, F401
